"""Bank services: ledger sub-accounts, opening balances, transfers, manual entries, matching, reconciliation."""
import hashlib
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounting import services as A
from accounting.models import Account, JournalEntry, JournalLine
from accounting.money import ZERO, q2

from .models import BankAccount, BankTransaction, BankTransfer, ReconciliationMatch


class BankError(ValidationError):
    pass


# ------------------------------------------------------------- accounts
def create_ledger_account(name, kind):
    group = Account.objects.filter(code='1100', is_group=True).first() or \
        Account.objects.filter(account_type='asset', is_group=True).order_by('code').first()
    base, top = (1110, 1119) if kind == BankAccount.CASH else (1120, 1199)
    for n in range(base + 1, top + 1):
        if not Account.objects.filter(code=str(n)).exists():
            return Account.objects.create(code=str(n), name=f'{"Cash" if kind == "cash" else "Bank"} - {name}'[:150],
                                          account_type='asset', parent=group)
    raise BankError('No free account code left under Cash and Bank; pick an existing ledger account.')


def validate_ledger_account(acct, bank=None):
    if acct.account_type != 'asset' or acct.is_group or not acct.active:
        raise BankError('Linked ledger must be an active asset (non-group) account.')
    other = BankAccount.objects.filter(ledger_account=acct)
    if bank and bank.pk:
        other = other.exclude(pk=bank.pk)
    if other.exists():
        raise BankError('That ledger account is already linked to another bank/cash account.')


@transaction.atomic
def sync_opening(bank):
    """Post (idempotently) the opening balance journal; if amount/date changed, reverse the old one first."""
    amt, d = q2(bank.opening_balance), bank.opening_date
    old = bank.opening_entry
    if old and old.status == JournalEntry.POSTED:
        ln = old.lines.get(account=bank.ledger_account)
        if old.date == d and q2(ln.debit - ln.credit) == amt:
            return old
        A.reverse(old, 'Opening balance changed', max(old.date, d or old.date))
    if not amt or not d:
        if old:
            BankAccount.objects.filter(pk=bank.pk).update(opening_entry=None)
            bank.opening_entry = None
        return None
    equity = A.mapped_account('bank.opening_equity')
    side = (lambda acct, v: {'account': acct, 'debit': v}) , (lambda acct, v: {'account': acct, 'credit': v})
    a = abs(amt)
    lines = [side[0](bank.ledger_account, a), side[1](equity, a)] if amt > 0 else [side[1](bank.ledger_account, a), side[0](equity, a)]
    key = f'bank:{bank.pk}:opening:{d.isoformat()}:{amt}'
    e, _ = A.post_from_source(key, d, f'Opening balance - {bank.name}', lines, source_type='bank_opening', source_id=bank.pk)
    BankAccount.objects.filter(pk=bank.pk).update(opening_entry=e)
    bank.opening_entry = e
    return e


# ------------------------------------------------------------- postings
def transfer(from_bank, to_bank, amount, d, narration='', reference=''):
    amt = q2(amount)
    if from_bank.pk == to_bank.pk:
        raise BankError('Choose two different accounts.')
    if amt <= 0:
        raise BankError('Transfer amount must be greater than zero.')
    if not (from_bank.active and to_bank.active):
        raise BankError('Both accounts must be active.')
    with transaction.atomic():
        narr = narration or f'Transfer {from_bank.name} -> {to_bank.name}'
        e = A.create_and_post(d, narr + (f' (ref {reference})' if reference else ''), [
            {'account': to_bank.ledger_account, 'debit': amt, 'description': f'From {from_bank.name}'},
            {'account': from_bank.ledger_account, 'credit': amt, 'description': f'To {to_bank.name}'}],
            source_type='bank_transfer')
        t = BankTransfer.objects.create(date=d, from_account=from_bank, to_account=to_bank, amount=amt,
                                        reference=reference, narration=narr, entry=e)
        JournalEntry.objects.filter(pk=e.pk).update(source_id=str(t.pk))
    return t


def manual_entry(bank, d, direction, amount, counter_account, narration):
    """direction 'out' (e.g. bank charges: Dr expense / Cr bank) or 'in' (e.g. interest: Dr bank / Cr income)."""
    amt = q2(amount)
    if amt <= 0:
        raise BankError('Amount must be greater than zero.')
    if counter_account.pk == bank.ledger_account_id:
        raise BankError('Counter account cannot be the bank account itself.')
    if direction not in ('in', 'out'):
        raise BankError('Choose money in or money out.')
    bank_side = {'account': bank.ledger_account, ('debit' if direction == 'in' else 'credit'): amt}
    other = {'account': counter_account, ('credit' if direction == 'in' else 'debit'): amt}
    return A.create_and_post(d, narration or f'Bank entry - {bank.name}', [bank_side, other],
                             source_type='bank_manual', source_id=bank.pk)


# ------------------------------------------------------------- statement rows
def row_hash(bank_id, d, amount, reference, description, occurrence=0):
    norm = '|'.join([str(bank_id), d.isoformat(), str(q2(amount)), (reference or '').strip().lower(),
                     ' '.join((description or '').lower().split()), str(occurrence)])
    return hashlib.sha256(norm.encode()).hexdigest()


def add_transaction(bank, d, description, debit=0, credit=0, reference='', value_date=None, balance=None,
                    source='manual', batch=None, occurrence=0):
    """Returns (txn, created). Duplicate rows (same hash) are skipped."""
    debit, credit = q2(debit), q2(credit)
    if debit < 0 or credit < 0 or (debit > 0) == (credit > 0):
        raise BankError('Enter exactly one of withdrawal or deposit (greater than zero).')
    h = row_hash(bank.pk, d, credit - debit, reference, description, occurrence)
    ex = BankTransaction.objects.filter(row_hash=h).first()
    if ex:
        return ex, False
    t = BankTransaction.objects.create(bank_account=bank, date=d, value_date=value_date, description=(description or '')[:500],
                                       reference=(reference or '')[:120], debit=debit, credit=credit,
                                       balance=q2(balance) if balance not in (None, '') else None,
                                       source=source, batch=batch, row_hash=h)
    return t, True


# ------------------------------------------------------------- matching
def line_signed(line):
    return q2(line.debit - line.credit)


def matched_on_txn(txn):
    return q2(txn.matches.filter(active=True).aggregate(s=Sum('amount'))['s'] or 0)


def matched_on_line(line):
    return q2(ReconciliationMatch.objects.filter(journal_line=line, active=True).aggregate(s=Sum('amount'))['s'] or 0)


def txn_remaining(txn):
    return q2(abs(txn.amount) - matched_on_txn(txn))


def line_remaining(line):
    return q2(abs(line_signed(line)) - matched_on_line(line))


def refresh_status(txn):
    if txn.status == BankTransaction.IGNORED:
        return txn
    m = matched_on_txn(txn)
    st = BankTransaction.MATCHED if m == abs(txn.amount) else BankTransaction.PARTIAL if m > 0 else BankTransaction.UNRECONCILED
    if st != txn.status:
        txn.status = st
        txn.save(update_fields=['status'])
    return txn


def _booked_lines(bank):
    return JournalLine.objects.filter(account=bank.ledger_account, entry__status__in=A.BOOKED).select_related('entry')


@transaction.atomic
def match(txn, line, amount=None):
    txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
    if txn.status == BankTransaction.IGNORED:
        raise BankError('Transaction is ignored; un-ignore it first.')
    if line.account_id != txn.bank_account.ledger_account_id:
        raise BankError("Journal line is not on this bank's ledger account.")
    if line.entry.status not in A.BOOKED:
        raise BankError('Only posted journal lines can be matched.')
    if (txn.amount > 0) != (line_signed(line) > 0):
        raise BankError('Direction differs: a deposit must match a ledger debit and a withdrawal a ledger credit.')
    tr, lr = txn_remaining(txn), line_remaining(line)
    amt = q2(amount) if amount not in (None, '') else min(tr, lr)
    if amt <= 0:
        raise BankError('Nothing left to match.')
    if amt > tr:
        raise BankError(f'Amount {amt} exceeds the unmatched transaction amount {tr}.')
    if amt > lr:
        raise BankError(f'Amount {amt} exceeds the unmatched journal line amount {lr}.')
    m = ReconciliationMatch.objects.create(transaction=txn, journal_line=line, amount=amt)
    refresh_status(txn)
    return m


@transaction.atomic
def unmatch(m, reason):
    reason = (reason or '').strip()
    if not reason:
        raise BankError('A reason is required to unmatch.')
    if not m.active:
        raise BankError('Already unmatched.')
    m.active, m.unmatched_at, m.unmatch_reason = False, timezone.now(), reason
    m.save()
    refresh_status(m.transaction)
    return m


def set_ignored(txn, ignored, reason=''):
    if ignored:
        if matched_on_txn(txn):
            raise BankError('Unmatch first; a matched transaction cannot be ignored.')
        if not (reason or '').strip():
            raise BankError('A reason is required to ignore a transaction.')
        txn.status, txn.ignore_reason = BankTransaction.IGNORED, reason.strip()
    else:
        txn.status, txn.ignore_reason = BankTransaction.UNRECONCILED, ''
    txn.save(update_fields=['status', 'ignore_reason'])
    return refresh_status(txn)


def suggestions(txn, days=3):
    """Booked ledger lines on the bank's account within +-days, same direction, with unmatched amount.
    Exact remaining-amount matches first."""
    from datetime import timedelta
    tr = txn_remaining(txn)
    if tr <= 0 or txn.status == BankTransaction.IGNORED:
        return []
    qs = _booked_lines(txn.bank_account).filter(entry__date__gte=txn.date - timedelta(days=days),
                                                entry__date__lte=txn.date + timedelta(days=days))
    qs = qs.filter(debit__gt=0) if txn.amount > 0 else qs.filter(credit__gt=0)
    out = []
    for l in qs:
        lr = line_remaining(l)
        if lr > 0:
            out.append({'line': l, 'remaining': lr, 'exact': lr == tr, 'gap': abs((l.entry.date - txn.date).days)})
    out.sort(key=lambda s: (not s['exact'], s['gap'], abs(s['remaining'] - tr)))
    return out


# ------------------------------------------------------------- reconciliation summary
def summary(bank, end, start=None):
    """As of ``end``: book balance (ledger), statement balance, unreconciled items both sides, difference.
    statement = book + unreconciled statement items - unreconciled book items  (when everything is explained)."""
    book = A.balance(bank.ledger_account, end=end)
    txns = list(bank.transactions.filter(date__lte=end).order_by('date', 'id'))
    with_bal = [t for t in txns if t.balance is not None]
    stmt_opening = q2(bank.opening_balance) if bank.opening_date and bank.opening_date <= end else ZERO
    if with_bal:
        statement, basis = q2(with_bal[-1].balance), 'last statement balance'
        if with_bal[-1] is not txns[-1]:  # rows after the last printed balance
            statement = q2(statement + sum((t.amount for t in txns if (t.date, t.id) > (with_bal[-1].date, with_bal[-1].id)), ZERO))
    else:
        statement, basis = q2(stmt_opening + sum((t.amount for t in txns), ZERO)), 'opening + statement movements'
    unrec_bank = []
    for t in txns:
        if t.status == BankTransaction.IGNORED:
            continue
        rem = txn_remaining(t)
        if rem > 0:
            unrec_bank.append({'txn': t, 'remaining': q2(rem if t.amount > 0 else -rem)})
    unrec_book = []
    opening_entry_id = bank.opening_entry_id
    for l in _booked_lines(bank).filter(entry__date__lte=end).order_by('entry__date', 'id'):
        if l.entry_id == opening_entry_id:
            continue  # opening balance is on both sides already
        rem = line_remaining(l)
        if rem > 0:
            unrec_book.append({'line': l, 'remaining': q2(rem if line_signed(l) > 0 else -rem)})
    ub = q2(sum((x['remaining'] for x in unrec_bank), ZERO))
    ul = q2(sum((x['remaining'] for x in unrec_book), ZERO))
    adjusted_book = q2(book + ub - ul)
    return {'end': end, 'book_balance': book, 'statement_balance': statement, 'statement_basis': basis,
            'difference': q2(statement - book), 'unreconciled_bank': unrec_bank, 'unreconciled_book': unrec_book,
            'unreconciled_bank_total': ub, 'unreconciled_book_total': ul, 'adjusted_book': adjusted_book,
            'unexplained': q2(statement - adjusted_book)}


# ------------------------------------------------------------- legacy free-text preview (read-only)
def legacy_bank_text_preview():
    from expense_management.models import CompanyExpensePayment, EmployeeReimbursement
    banks = list(BankAccount.objects.all())
    rows = {}
    for model, label, date_f in ((CompanyExpensePayment, 'Company expense payments', 'payment_date'),
                                 (EmployeeReimbursement, 'Employee reimbursements', 'reimbursement_date')):
        for text, amount in model.objects.values_list('bank_account', 'amount'):
            key = ((text or '').strip(), label)
            r = rows.setdefault(key, {'text': key[0], 'source': label, 'count': 0, 'total': ZERO})
            r['count'] += 1
            r['total'] = q2(r['total'] + (amount or 0))
    out = []
    for r in rows.values():
        r['suggestion'], r['why'] = suggest_bank(r['text'], banks)
        out.append(r)
    out.sort(key=lambda r: (r['source'], r['text'].lower()))
    return out


def suggest_bank(text, banks):
    import re
    t = (text or '').lower()
    if not t:
        return None, 'blank'
    digits = re.findall(r'\d{4,}', t)
    for b in banks:
        num = (b.account_number or '').replace(' ', '')
        if num and any(num.endswith(d[-4:]) for d in digits):
            return b, 'last 4 digits match'
    for b in banks:
        if b.name.lower() in t or t in b.name.lower():
            return b, 'name match'
    for b in banks:
        if b.bank_name and b.bank_name.lower() in t:
            return b, 'bank name match'
    if 'cash' in t:
        c = next((b for b in banks if b.kind == BankAccount.CASH), None)
        if c:
            return c, 'cash'
    return None, 'no match'
