"""Accounting services: periods, posting, reversal, idempotent posting from source documents, ledgers."""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from reporting.periods import fy_range, fy_start_year, month_end

from .models import Account, AccountMapping, FinancialPeriod, FinancialYear, JournalEntry, JournalLine, PeriodEvent, Sequence
from .money import ZERO, q2

BOOKED = (JournalEntry.POSTED, JournalEntry.REVERSED)  # reversed originals stay in the books next to their mirror


class PostingError(ValidationError):
    pass


# ---------------------------------------------------------------- periods
def ensure_financial_year(start_year):
    s, e = fy_range(start_year)
    fy, _ = FinancialYear.objects.get_or_create(start_year=start_year, defaults={'start_date': s, 'end_date': e})
    for i in range(12):
        y, m = (start_year, 4 + i) if i < 9 else (start_year + 1, i - 8)
        FinancialPeriod.objects.get_or_create(year=y, month=m, defaults={
            'financial_year': fy, 'start_date': date(y, m, 1), 'end_date': month_end(y, m)})
    return fy


def period_for(d, create=True):
    p = FinancialPeriod.objects.filter(year=d.year, month=d.month).first()
    if p is None and create:
        ensure_financial_year(fy_start_year(d))
        p = FinancialPeriod.objects.get(year=d.year, month=d.month)
    return p


def assert_period_open(d):
    """Guard: raise PostingError if ``d`` falls in a closed financial period."""
    p = period_for(d)
    if not p.is_open:
        raise PostingError(f'{p.label} is closed. Reopen the period (with a reason) before posting dated {d:%d-%m-%Y}.')
    return p


def close_period(period, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to close a period.')
    if not period.is_open:
        raise ValidationError(f'{period.label} is already closed.')
    now = timezone.now()
    period.status, period.closed_at, period.close_reason = FinancialPeriod.CLOSED, now, reason
    period.save()
    PeriodEvent.objects.create(period=period, action='close', reason=reason, at=now)


def reopen_period(period, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to reopen a period.')
    if period.is_open:
        raise ValidationError(f'{period.label} is already open.')
    now = timezone.now()
    period.status, period.reopened_at, period.reopen_reason = FinancialPeriod.OPEN, now, reason
    period.save()
    PeriodEvent.objects.create(period=period, action='reopen', reason=reason, at=now)


# ---------------------------------------------------------------- entries
def _next_number(d):
    fy = fy_start_year(d)
    name = f'JV-{fy}'
    Sequence.objects.get_or_create(name=name)
    seq = Sequence.objects.select_for_update().get(name=name)
    n = seq.next_value
    seq.next_value = n + 1
    seq.save()
    return f'JV/{fy}-{str(fy + 1)[-2:]}/{n:05d}'


def _clean_lines(lines):
    """lines: iterable of dicts {account, debit, credit, description}. Returns normalised list."""
    out = []
    for i, ln in enumerate(lines, 1):
        acc = ln['account']
        if not isinstance(acc, Account):
            acc = Account.objects.get(pk=acc)
        dr, cr = q2(ln.get('debit') or 0), q2(ln.get('credit') or 0)
        if dr < 0 or cr < 0:
            raise PostingError(f'Line {i}: amounts cannot be negative.')
        if (dr > 0) == (cr > 0):
            raise PostingError(f'Line {i}: enter exactly one of debit or credit (greater than zero).')
        out.append({'account': acc, 'debit': dr, 'credit': cr, 'description': (ln.get('description') or '')[:300],
                    'party_type': ln.get('party_type') or '', 'party_id': str(ln.get('party_id') or '')})
    return out


def validate(entry_date, lines):
    lines = _clean_lines(lines)
    if len(lines) < 2:
        raise PostingError('A journal entry needs at least two lines.')
    for i, ln in enumerate(lines, 1):
        a = ln['account']
        if not a.active:
            raise PostingError(f'Line {i}: account {a} is inactive.')
        if a.is_group:
            raise PostingError(f'Line {i}: {a} is a group account; post to a ledger account under it.')
    dr, cr = sum(l['debit'] for l in lines), sum(l['credit'] for l in lines)
    if dr <= 0:
        raise PostingError('Total must be greater than zero.')
    if dr != cr:
        raise PostingError(f'Debits ({dr}) must equal credits ({cr}); difference {dr - cr}.')
    assert_period_open(entry_date)
    return lines


@transaction.atomic
def create_draft(entry_date, narration, lines, source_type='manual', source_id='', source_key=None):
    lines = _clean_lines(lines)
    e = JournalEntry.objects.create(date=entry_date, narration=narration, source_type=source_type,
                                    source_id=str(source_id or ''), source_key=source_key or None)
    for ln in lines:
        JournalLine.objects.create(entry=e, **ln)
    return e


@transaction.atomic
def post(entry):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.DRAFT:
        raise PostingError(f'{entry} is already {entry.status}.')
    validate(entry.date, [{'account': l.account, 'debit': l.debit, 'credit': l.credit,
                           'description': l.description} for l in entry.lines.select_related('account')])
    entry.number, entry.status, entry.posted_at = _next_number(entry.date), JournalEntry.POSTED, timezone.now()
    entry._posting = True
    entry.save()
    entry._posting = False
    return entry


def create_and_post(entry_date, narration, lines, **kw):
    with transaction.atomic():
        validate(entry_date, lines)
        return post(create_draft(entry_date, narration, lines, **kw))


def post_from_source(source_key, entry_date, narration, lines, source_type='', source_id=''):
    """Idempotent: returns (entry, created). If an entry with ``source_key`` exists nothing is posted again."""
    if not source_key:
        raise PostingError('source_key is required.')
    existing = JournalEntry.objects.filter(source_key=source_key).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            e = create_and_post(entry_date, narration, lines, source_type=source_type or source_key.split(':')[0],
                                source_id=source_id, source_key=source_key)
        return e, True
    except IntegrityError:
        return JournalEntry.objects.get(source_key=source_key), False


@transaction.atomic
def reverse(entry, reason, reversal_date=None):
    """Create a mirror (debits <-> credits) posted entry and mark the original Reversed."""
    reason = (reason or '').strip()
    if not reason:
        raise PostingError('A reason is required to reverse an entry.')
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.POSTED:
        raise PostingError(f'Only posted entries can be reversed ({entry} is {entry.status}).')
    if entry.reversal_of_id:
        raise PostingError('A reversal entry cannot itself be reversed; post a new entry instead.')
    d = reversal_date or timezone.localdate()
    if d < entry.date:
        raise PostingError('Reversal date cannot be before the original entry date.')
    lines = [{'account': l.account, 'debit': l.credit, 'credit': l.debit, 'description': l.description,
              'party_type': l.party_type, 'party_id': l.party_id}
             for l in entry.lines.select_related('account')]
    for ln in lines:  # an inactive account must still be reversible
        ln['account'].active = True
    validate(d, lines)
    rev = JournalEntry.objects.create(date=d, narration=f'Reversal of {entry.number}: {reason}'[:500],
                                      source_type='reversal', source_id=str(entry.pk),
                                      source_key=f'reversal:{entry.pk}', reversal_of=entry, reversal_reason=reason)
    for ln in lines:
        JournalLine.objects.create(entry=rev, **ln)
    rev.number, rev.status, rev.posted_at = _next_number(d), JournalEntry.POSTED, timezone.now()
    rev._posting = True
    rev.save()
    JournalEntry.objects.filter(pk=entry.pk).update(status=JournalEntry.REVERSED, reversal_reason=reason)
    return rev


# ---------------------------------------------------------------- mappings / ledgers
def mapped_account(key):
    m = AccountMapping.objects.select_related('account').filter(key=key).first()
    if not m or not m.account:
        raise PostingError(f'Account mapping "{key}" is not set. Configure it under Accounting > Mappings.')
    return m.account


def balance(account, start=None, end=None):
    qs = JournalLine.objects.filter(account=account, entry__status__in=BOOKED)
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    t = qs.aggregate(d=Sum('debit'), c=Sum('credit'))
    dr, cr = t['d'] or ZERO, t['c'] or ZERO
    return q2(dr - cr if account.debit_normal else cr - dr)


def ledger(account, start, end):
    """Opening balance, lines with running balance (in the account's normal-balance sign), closing balance."""
    opening = ZERO
    qs = JournalLine.objects.filter(account=account, entry__status__in=BOOKED)
    t = qs.filter(entry__date__lt=start).aggregate(d=Sum('debit'), c=Sum('credit'))
    dr, cr = t['d'] or ZERO, t['c'] or ZERO
    opening = q2(dr - cr if account.debit_normal else cr - dr)
    rows, run, td, tc = [], opening, ZERO, ZERO
    for l in qs.filter(entry__date__gte=start, entry__date__lte=end).select_related('entry').order_by('entry__date', 'entry_id', 'id'):
        run = q2(run + (l.debit - l.credit if account.debit_normal else l.credit - l.debit))
        td, tc = td + l.debit, tc + l.credit
        rows.append({'line': l, 'entry': l.entry, 'balance': run})
    return {'opening': opening, 'rows': rows, 'closing': run, 'total_debit': q2(td), 'total_credit': q2(tc)}
