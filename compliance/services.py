"""Rule lookup, rule-based calculations (comparison only), GST input reclass and statutory payment postings."""
import calendar
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounting.money import ZERO, q2
from accounting.postings import L, bank_ledger, current, is_posted, m, mapped_or, sync, unpost

from .models import (PAYABLE_KEYS, PAYMENT_KINDS, DueDateEntry, GstInputClaim, GstInputEvent, StatutoryPayment,
                     StatutoryRule)

NOT_CONFIGURED = 'rule not configured'
TOLERANCE = Decimal('1.00')  # rupee rounding
ENTRY_PAYMENT_KINDS = {**{kind: (kind,) for kind, _ in PAYMENT_KINDS},
                       'gst_return': ('gst_cgst', 'gst_sgst', 'gst_igst'),
                       'tds_return': ('tds_vendor', 'tds_salary'), 'other': ()}


class RuleNotConfigured(Exception):
    def __init__(self, kind, key='', on_date=None):
        self.kind, self.key, self.on_date = kind, key, on_date
        label = dict(StatutoryRule.KINDS).get(kind, kind)
        super().__init__(f'{NOT_CONFIGURED}: {label}{" " + key if key else ""}'
                         f'{" on " + on_date.strftime("%d-%m-%Y") if on_date else ""}')


def get_rule(kind, key, on_date):
    """The one active rule of ``kind``/``key`` covering ``on_date``. Raises RuleNotConfigured - never falls back."""
    key = StatutoryRule.norm_key(kind, key)
    r = (StatutoryRule.objects.filter(kind=kind, key=key, active=True, effective_from__lte=on_date)
         .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date)).order_by('-effective_from').first())
    if r is None:
        raise RuleNotConfigured(kind, key, on_date)
    return r


# ------------------------------------------------------------------ calculations (used only for comparison)
def pct(base, rate):
    return q2(base * rate / 100)


def expected_gst(taxable, sac, on_date, igst=False):
    r = get_rule(StatutoryRule.GST, sac, on_date)
    rate = r.P('rate')
    if igst:
        return {'rate': rate, 'cgst': ZERO, 'sgst': ZERO, 'igst': pct(taxable, rate), 'rule': r}
    half = pct(taxable, rate / 2)
    return {'rate': rate, 'cgst': half, 'sgst': half, 'igst': ZERO, 'rule': r}


def expected_pf(basic, on_date):
    r = get_rule(StatutoryRule.PF, '', on_date)
    c = r.P('wage_ceiling')
    wage = min(basic, c) if c is not None else basic
    return {'employee': pct(wage, r.P('employee_pct')), 'employer': pct(wage, r.P('employer_pct')), 'wage': wage,
            'rule': r}


def expected_esi(gross, on_date):
    r = get_rule(StatutoryRule.ESI, '', on_date)
    if gross > r.P('wage_ceiling'):
        return {'employee': ZERO, 'employer': ZERO, 'applies': False, 'rule': r}
    return {'employee': pct(gross, r.P('employee_pct')), 'employer': pct(gross, r.P('employer_pct')), 'applies': True,
            'rule': r}


def expected_pt(gross, state, on_date):
    r = get_rule(StatutoryRule.PT, state, on_date)
    for s in r.params['slabs']:
        lo, hi = Decimal(s['from']), None if s['to'] is None else Decimal(s['to'])
        if gross >= lo and (hi is None or gross <= hi):
            return {'amount': Decimal(s['amount']), 'rule': r}
    return {'amount': ZERO, 'rule': r}


def expected_tds(amount, section, on_date):
    r = get_rule(StatutoryRule.TDS, section, on_date)
    ts = r.P('threshold_single')
    if ts is not None and amount <= ts:
        return {'amount': ZERO, 'below_threshold': True, 'rule': r}
    return {'amount': pct(amount, r.P('rate')), 'below_threshold': False, 'rule': r}


# ------------------------------------------------------------------ GST input eligibility
GST_SPLIT = (('cgst_amount', 'gst.input.cgst', 'CGST'), ('sgst_amount', 'gst.input.sgst', 'SGST'),
             ('igst_amount', 'gst.input.igst', 'IGST'))


def gst_input_key(expense):
    return f'gst_input:{expense.pk}'


def claim_for(expense):
    c, _ = GstInputClaim.objects.get_or_create(expense=expense)
    return c


def _inr(v, rate):
    return q2((v or ZERO) * (rate or 1))


def gst_input_lines(e):
    parts = [(key, lab, _inr(getattr(e, f), e.exchange_rate)) for f, key, lab in GST_SPLIT]
    total = q2(sum((a for _, _, a in parts), ZERO))
    if total <= 0:
        raise ValidationError(f'{e.expense_id} has no CGST / SGST / IGST split, so there is nothing to claim.')
    desc = f'{e.expense_id} {e.invoice_number} input GST reclass'.strip()
    lines = [L(m(key), debit=a, description=f'{desc} {lab}') for key, lab, a in parts if a]
    lines.append(L(mapped_or(f'expense.category.{e.category_id}', 'expense.default'), credit=total, description=desc))
    return lines


@transaction.atomic
def mark_eligible(expense, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to mark GST input as eligible.')
    if expense.is_cancelled:
        raise ValidationError('A cancelled expense cannot be claimed.')
    from expense_management.ledger import expense_key
    if not is_posted(expense_key(expense)):
        raise ValidationError(f'{expense.expense_id} is not posted to the ledger yet.')
    c = claim_for(expense)
    entry = sync(gst_input_key(expense), expense.expense_date, f'Input GST claim {expense.expense_id}',
                 gst_input_lines(expense), 'gst_input', expense.pk, reason='Bill changed')
    if c.status != c.ELIGIBLE:
        c.status, c.reason, c.changed_at = c.ELIGIBLE, reason[:300], timezone.now()
        c.save()
        GstInputEvent.objects.create(claim=c, action='mark', reason=reason[:300])
    return entry


@transaction.atomic
def unmark(expense, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to unmark GST input.')
    c = claim_for(expense)
    rev = unpost(gst_input_key(expense), f'Input GST unmarked: {reason}'[:300], expense.expense_date)
    if c.status != c.REVIEW:
        c.status, c.reason, c.changed_at = c.REVIEW, reason[:300], timezone.now()
        c.save()
        GstInputEvent.objects.create(claim=c, action='unmark', reason=reason[:300])
    return rev


def gst_input_posted(expense):
    return current(gst_input_key(expense))


# ------------------------------------------------------------------ statutory payments
def statutory_payment_key(p):
    return f'statutory_payment:{p.pk}'


def statutory_payment_lines(p):
    if p.cancelled:
        return []
    desc = f'{p.get_kind_display()} {p.period_label} challan {p.challan_ref}'
    return [L(m(PAYABLE_KEYS[p.kind]), debit=q2(p.amount), description=desc),
            L(bank_ledger(p.bank_account, 'payroll.payment_bank'), credit=q2(p.amount), description=desc)]


@transaction.atomic
def record_payment(p):
    p.full_clean()
    p.save()
    return sync(statutory_payment_key(p), p.date, f'Statutory payment {p.get_kind_display()} {p.period_label}',
                statutory_payment_lines(p), 'statutory_payment', p.pk)


@transaction.atomic
def cancel_payment(p, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to cancel a statutory payment.')
    p.cancelled, p.cancel_reason = True, reason[:300]
    p.save()
    return unpost(statutory_payment_key(p), f'Statutory payment cancelled: {reason}'[:300], p.date)


# ------------------------------------------------------------------ due-date calendar
def add_months(y, mth, n):
    t = y * 12 + (mth - 1) + n
    return t // 12, t % 12 + 1


def due_calendar(start, months=3):
    """Due dates falling in [start month, start + months) from active configured entries."""
    out = []
    entries = list(DueDateEntry.objects.filter(active=True))
    paid = {}
    for p in StatutoryPayment.objects.filter(cancelled=False):
        key = (p.kind, p.period_year, p.period_month)
        paid[key] = paid.get(key, ZERO) + q2(p.amount)
    offsets = [e.month_offset or 0 for e in entries]
    for i in range(-(max(offsets, default=0) + 1), months + 1):
        py, pm = add_months(start.year, start.month, i)
        for e in entries:
            if e.month_list() and pm not in e.month_list():
                continue
            dy, dm = add_months(py, pm, e.month_offset)
            d = date(dy, dm, min(e.day_of_month, calendar.monthrange(dy, dm)[1]))
            end = date(*add_months(start.year, start.month, months), 1)
            if not (date(start.year, start.month, 1) <= d < end):
                continue
            amt = q2(sum((paid.get((k, py, pm), ZERO) for k in ENTRY_PAYMENT_KINDS.get(e.kind, ())), ZERO))
            out.append({'date': d, 'entry': e, 'period': f'{pm:02d}/{py}',
                        'paid': amt > 0, 'paid_amount': amt, 'overdue': d < timezone.localdate()})
    return sorted(out, key=lambda x: (x['date'], x['entry'].name))
