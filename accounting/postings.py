"""Stage 4: posting rules that turn source documents into journal entries.

* Every account comes from an AccountMapping key (accounting.services.mapped_account); bank lines use the
  BankAccount's own ledger account (or the default BankAccount, else the mapping key).
* Every posting goes through accounting.services.post_from_source with a unique, versioned source key:
  ``<base>`` for the first posting and ``<base>:v2``, ``<base>:v3``... after a change.
* ``sync()`` is idempotent: if the current entry already matches it returns it; if the document changed it
  reverses the current entry (keeping it in the books) and posts a new version. Nothing is ever edited.
* Sub-ledgers (customer / vendor / employee) are tagged on the lines with party_type / party_id.
"""
from collections import OrderedDict

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import services as acc
from .models import AccountMapping, JournalEntry, JournalLine
from .money import ZERO, q2

PostingError = acc.PostingError


# ------------------------------------------------------------------ generic helpers
def _keys(base):
    return Q(source_key=base) | Q(source_key__startswith=base + ':v')


def history(base):
    """All entries ever posted for a source document (old versions included), oldest first."""
    return JournalEntry.objects.filter(_keys(base)).order_by('id')


def current(base):
    """The live (posted, not reversed) entry for a source document, or None."""
    return JournalEntry.objects.filter(_keys(base), status=JournalEntry.POSTED).order_by('-id').first()


def is_posted(base):
    return current(base) is not None


def _norm(lines):
    out = []
    for ln in lines:
        dr, cr = q2(ln.get('debit') or 0), q2(ln.get('credit') or 0)
        net = q2(dr - cr)
        if net == 0:
            continue
        ln = dict(ln, debit=net if net > 0 else ZERO, credit=-net if net < 0 else ZERO,
                  party_type=ln.get('party_type') or '', party_id=str(ln.get('party_id') or ''))
        out.append(ln)
    return out


def _sig_lines(d, lines):
    return d, sorted((l['account'].pk, l['debit'], l['credit'], l['party_type'], l['party_id']) for l in lines)


def _sig_entry(e):
    return e.date, sorted((l.account_id, q2(l.debit), q2(l.credit), l.party_type, l.party_id) for l in e.lines.all())


@transaction.atomic
def sync(base, entry_date, narration, lines, source_type, source_id='', reason='Source document changed'):
    """Make the ledger match ``lines`` for this source document. Returns the live entry (or None when the
    document now posts nothing)."""
    lines = _norm(lines)
    cur = current(base)
    if cur and lines and _sig_entry(cur) == _sig_lines(entry_date, lines):
        return cur
    if cur:
        acc.reverse(cur, reason, cur.date)  # dated like the original so the correction lands in the same period
    if not lines:
        return None
    n = JournalEntry.objects.filter(_keys(base)).count()
    key = base if n == 0 else f'{base}:v{n + 1}'
    entry, _ = acc.post_from_source(key, entry_date, narration[:500], lines, source_type=source_type,
                                    source_id=str(source_id))
    return entry


@transaction.atomic
def unpost(base, reason, reversal_date=None):
    """Reverse the live entry of a source document (history kept). Returns the reversal or None."""
    cur = current(base)
    if not cur:
        return None
    d = reversal_date or timezone.localdate()
    return acc.reverse(cur, reason, max(d, cur.date))


def m(key):
    return acc.mapped_account(key)


def mapped_or(key, fallback_key):
    """Account mapped to ``key`` when that mapping exists and has an account, else ``fallback_key``'s."""
    mp = AccountMapping.objects.select_related('account').filter(key=key).first()
    if mp and mp.account_id:
        return mp.account
    return m(fallback_key)


def bank_ledger(bank_account=None, fallback_key='invoice.receipt_bank', cash=False):
    """Ledger account for money in/out: the chosen BankAccount, else the default (cash: first active cash
    account), else the mapping ``fallback_key`` (``cash.default`` for cash)."""
    if bank_account is not None:
        return bank_account.ledger_account
    from bank_management.models import BankAccount
    qs = BankAccount.objects.filter(active=True).select_related('ledger_account')
    if cash:
        b = qs.filter(kind=BankAccount.CASH).order_by('-is_default', 'id').first()
        return b.ledger_account if b else m('cash.default')
    b = qs.filter(is_default=True).first()
    return b.ledger_account if b else m(fallback_key)


def party_balance(account, party_type, party_id, as_at=None):
    """Debit-positive balance of one party on one account (booked entries only)."""
    qs = JournalLine.objects.filter(account=account, party_type=party_type, party_id=str(party_id),
                                    entry__status__in=acc.BOOKED)
    if as_at:
        qs = qs.filter(entry__date__lte=as_at)
    t = qs.aggregate(d=Sum('debit'), c=Sum('credit'))
    return q2((t['d'] or ZERO) - (t['c'] or ZERO))


def L(account, debit=ZERO, credit=ZERO, description='', party=None):
    d = {'account': account, 'debit': debit, 'credit': credit, 'description': description[:300]}
    if party:
        d['party_type'], d['party_id'] = party
    return d


# ------------------------------------------------------------------ invoices / receipts / credit notes
GST = (('cgst_amount', 'gst.output.cgst', 'CGST'), ('sgst_amount', 'gst.output.sgst', 'SGST'),
       ('igst_amount', 'gst.output.igst', 'IGST'))


def invoice_key(inv):
    return f'invoice:{inv.pk}'


def _check_inr(inv):
    if (inv.currency or 'INR').upper() != 'INR':
        raise PostingError(f'Invoice {inv.invoice_number} is in {inv.currency}; the ledger is INR and invoices '
                           'have no exchange-rate field, so it cannot be posted automatically.')


def invoice_lines(inv):
    _check_inr(inv)
    who = (inv.client.name if inv.client_id else '')[:80]
    party = ('customer', inv.client_id)
    revenue_key = 'invoice.recruitment_revenue' if getattr(inv, 'service_type', '') == 'recruitment_placement' else 'invoice.revenue'
    lines = [L(m('invoice.ar'), debit=inv.grand_total, description=f'Invoice {inv.invoice_number} {who}', party=party),
             L(m(revenue_key), credit=inv.taxable_value, description=f'Invoice {inv.invoice_number} taxable value')]
    for f, key, lab in GST:
        amt = q2(getattr(inv, f) or 0)
        if amt:
            lines.append(L(m(key), credit=amt, description=f'Invoice {inv.invoice_number} output {lab}'))
    return lines


def post_invoice(inv):
    """Post (idempotent) an invoice and then its receipts and credit notes. Returns the invoice entry."""
    with transaction.atomic():
        e = sync(invoice_key(inv), inv.invoice_date, f'Invoice {inv.invoice_number} - {inv.client}',
                 invoice_lines(inv), 'invoice', inv.pk, reason='Invoice changed')
        for p in inv.payments.all():
            post_receipt(p)
        for cn in inv.credit_notes.filter(status='active'):
            post_credit_note(cn)
    return e


def receipt_key(p):
    return f'invoice_payment:{p.pk}'


def receipt_lines(p):
    inv = p.invoice
    charges = q2(p.bank_charges or 0)
    tds = q2((p.deductions or 0) - charges)
    if tds < 0:
        raise PostingError('Bank charges cannot exceed the total TDS / charges deducted.')
    bank = bank_ledger(p.bank_account, 'invoice.receipt_bank', cash=(p.mode == 'cash' and not p.bank_account_id))
    party = ('customer', inv.client_id)
    ref = f'Receipt {inv.invoice_number} {p.reference}'.strip()
    return [L(bank, debit=p.amount, description=ref),
            L(m('invoice.tds_receivable'), debit=tds, description=f'TDS deducted by customer on {inv.invoice_number}'),
            L(m('bank.charges'), debit=charges, description=f'Bank charges on {inv.invoice_number}'),
            L(m('invoice.ar'), credit=q2(p.amount + (p.deductions or 0)), description=ref, party=party)]


def post_receipt(p, reason='Receipt changed'):
    """Receipts post only once their invoice is posted (legacy invoices: when 'Post to ledger' runs)."""
    if not is_posted(invoice_key(p.invoice)):
        return None
    _check_inr(p.invoice)
    return sync(receipt_key(p), p.receipt_date, f'Receipt against invoice {p.invoice.invoice_number}',
                receipt_lines(p), 'invoice_payment', p.pk, reason=reason)


def credit_note_key(cn):
    return f'credit_note:{cn.pk}'


def credit_note_lines(cn):
    inv = cn.invoice
    party = ('customer', inv.client_id)
    revenue_key = 'invoice.recruitment_revenue' if getattr(inv, 'service_type', '') == 'recruitment_placement' else 'invoice.revenue'
    lines = [L(m(revenue_key), debit=cn.taxable_amount, description=f'Credit note {cn.number} on {inv.invoice_number}')]
    for f, key, lab in GST:
        amt = q2(getattr(cn, f) or 0)
        if amt:
            lines.append(L(m(key), debit=amt, description=f'Credit note {cn.number} output {lab} reversed'))
    lines.append(L(m('invoice.ar'), credit=cn.total, description=f'Credit note {cn.number}', party=party))
    return lines


def post_credit_note(cn):
    if not is_posted(invoice_key(cn.invoice)):
        return None
    _check_inr(cn.invoice)
    return sync(credit_note_key(cn), cn.date, f'Credit note {cn.number} against invoice {cn.invoice.invoice_number}',
                credit_note_lines(cn), 'credit_note', cn.pk, reason='Credit note changed')


# ------------------------------------------------------------------ payroll
EMPLOYEE_DEDUCTION_KEYS = {'pf': 'payroll.pf_payable', 'esi': 'payroll.esi_payable',
                           'professional_tax': 'payroll.pt_payable', 'tds': 'payroll.tds_payable',
                           'loan': 'payroll.advance', 'advance': 'payroll.advance'}
EMPLOYER_KEYS = {'pf': 'payroll.pf_payable', 'esi': 'payroll.esi_payable'}
OTHER_DEDUCTIONS_KEY = 'payroll.other_deductions_payable'
UNTRACKED_PAYROLL = [
    'Loan and advance recoveries both credit payroll.advance (Employee Advances); there is no separate loan ledger.',
    'Other / additional deductions and employer "other" contributions go to payroll.other_deductions_payable '
    '(2450 Other Payroll Deductions Payable) - review and reclassify manually.',
    'No fields exist for employer PF admin charges / EDLI, gratuity or bonus provisions, LWF or salary advances '
    'paid out; none of these are posted.',
    'Payment of PF / ESI / PT / TDS to the government is not modelled (record it as a manual journal / bank entry).',
]


def payroll_key(period):
    return f'payroll_period:{period.pk}'


def payroll_lines(period):
    from employee_management.models import EmployeePayroll, PayrollDeduction
    rows = list(EmployeePayroll.objects.filter(period=period, status=EmployeePayroll.FINALIZED, is_current=True)
                .select_related('employee').order_by('id'))
    if not rows:
        return []
    gross = q2(sum((r.gross_earnings for r in rows), ZERO))
    rounding = q2(sum((r.rounding_adjustment for r in rows), ZERO))
    er_total = q2(sum((r.employer_contributions for r in rows), ZERO))
    lab = period.label
    lines = [L(m('payroll.salary_expense'), debit=gross, description=f'Gross salary {lab}'),
             L(m('payroll.salary_expense'), debit=rounding, description=f'Salary rounding {lab}'),
             L(m('payroll.employer_contribution'), debit=er_total, description=f'Employer contributions {lab}')]
    for r in rows:
        lines.append(L(m('payroll.salary_payable'), credit=r.net_salary,
                       description=f'Net salary {r.code} {r.snap_name} {lab}', party=('employee', r.employee_id)))
    agg = OrderedDict()
    by_id = {r.pk: r for r in rows}
    for pid, amt in (PayrollDeduction.objects.filter(payroll__in=rows, code=ADVANCE_RECOVERY_CODE)
                     .order_by('payroll_id', 'id').values_list('payroll_id', 'amount')):
        r = by_id[pid]  # recoveries reduce the employee's own advance balance (party-tagged)
        lines.append(L(m('payroll.advance'), credit=q2(amt), description=f'Advance recovery {r.code} {r.snap_name} {lab}',
                       party=('employee', r.employee_id)))
    for code, side, amt in (PayrollDeduction.objects.filter(payroll__in=rows).exclude(code=ADVANCE_RECOVERY_CODE)
                            .values_list('code', 'side', 'amount')):
        keys = EMPLOYEE_DEDUCTION_KEYS if side == PayrollDeduction.EMPLOYEE else EMPLOYER_KEYS
        key = keys.get(code, OTHER_DEDUCTIONS_KEY)
        agg[(key, side)] = agg.get((key, side), ZERO) + q2(amt)
    for (key, side), amt in agg.items():
        what = 'employee deductions' if side == PayrollDeduction.EMPLOYEE else 'employer contributions'
        lines.append(L(m(key), credit=amt, description=f'{key.split(".")[-1].replace("_", " ")} - {what} {lab}'))
    dr = sum((l['debit'] for l in _norm(lines)), ZERO)
    cr = sum((l['credit'] for l in _norm(lines)), ZERO)
    if dr != cr:
        raise PostingError(f'Payroll {lab}: stored deduction lines do not reconcile with the payroll totals '
                           f'(debits {dr}, credits {cr}).')
    return lines


def post_payroll(period, reason='Payroll revised'):
    """One entry per payroll month covering every current finalized record. Re-running is a no-op;
    after a revision the old entry is reversed and the new totals posted."""
    return sync(payroll_key(period), period.end, f'Payroll {period.label}', payroll_lines(period),
                'payroll', period.pk, reason=reason)


ADVANCE_RECOVERY_CODE = 'advance_recovery'


# ------------------------------------------------------------------ employee advances / loans (Stage 6)
def advance_key(a):
    return f'employee_advance:{a.pk}'


def advance_repayment_key(r):
    return f'advance_repayment:{r.pk}'


def advance_writeoff_key(a):
    return f'advance_writeoff:{a.pk}'


def post_advance(a):
    """Disbursement: Dr employee advances (party employee) principal + interest; Cr bank principal;
    Cr interest income for the flat simple interest. A cancelled advance posts nothing (reversal)."""
    e = a.employee
    desc = f'{a.get_kind_display()} {e.employee_id} {e.full_name} {a.reference}'.strip()
    interest = q2(a.interest_amount or ZERO)
    lines = [] if a.status == a.CANCELLED else [
        L(m('payroll.advance'), debit=q2(a.principal + interest), description=desc, party=('employee', e.pk)),
        L(bank_ledger(a.bank_account, 'payroll.payment_bank'), credit=q2(a.principal), description=desc)]
    if lines and interest:
        lines.append(L(m('payroll.advance_interest'), credit=interest, description=f'Interest - {desc}'))
    return sync(advance_key(a), a.date, f'{a.get_kind_display()} {e.employee_id} #{a.pk}', lines,
                'employee_advance', a.pk, reason=a.cancel_reason or 'Advance changed')


def post_advance_repayment(r):
    a = r.advance
    desc = f'Advance repayment {a.employee.employee_id} #{a.pk} {r.reference}'.strip()
    lines = [] if r.cancelled else [
        L(bank_ledger(r.bank_account, 'payroll.payment_bank'), debit=q2(r.amount), description=desc),
        L(m('payroll.advance'), credit=q2(r.amount), description=desc, party=('employee', a.employee_id))]
    return sync(advance_repayment_key(r), r.date, desc, lines, 'advance_repayment', r.pk,
                reason=r.cancel_reason or 'Repayment changed')


def post_advance_writeoff(a):
    amt = q2(a.written_off_amount or ZERO)
    desc = f'Advance write-off {a.employee.employee_id} #{a.pk}: {a.write_off_reason}'
    lines = [] if not amt else [
        L(m('payroll.advance_writeoff'), debit=amt, description=desc),
        L(m('payroll.advance'), credit=amt, description=desc, party=('employee', a.employee_id))]
    return sync(advance_writeoff_key(a), a.written_off_on or a.date, desc[:200], lines, 'advance_writeoff', a.pk,
                reason='Write-off changed')


def salary_payment_key(sp):
    return f'salary_payment:{sp.pk}'


def post_salary_payment(sp):
    p = sp.payroll
    bank = bank_ledger(sp.bank_account, 'payroll.payment_bank', cash=(sp.mode == 'cash' and not sp.bank_account_id))
    desc = f'Salary {p.code} {p.snap_name} {p.period.label} {sp.reference}'.strip()
    lines = [] if sp.cancelled else [
        L(m('payroll.salary_payable'), debit=sp.amount, description=desc, party=('employee', p.employee_id)),
        L(bank, credit=sp.amount, description=desc)]
    return sync(salary_payment_key(sp), sp.payment_date, f'Salary payment {p.code} {p.period.label}', lines,
                'salary_payment', sp.pk, reason=sp.cancel_reason or 'Salary payment changed')
