"""Stage 4 Part B: ledger postings for company expenses, vendor payments, employee claims and
reimbursements. All accounts come from AccountMapping keys; bank lines use the chosen BankAccount's
ledger account (else the default BankAccount, else the mapping). Postings go through
accounting.postings.sync -> accounting.services.post_from_source with unique versioned source keys.

Source keys
* company_expense:<pk>   bill:        Dr expense account (expense.category.<category pk>, else expense.default;
                                       prior-period items: expense.prior_period, still on the actual expense date)
                                       Cr expense.ap (party vendor) -- full bill amount in INR (GST is not
                                       claimed automatically: gst.input.* is a manual claim, so GST stays in
                                       the expense line).
* expense_payment:<pk>   payment:     Dr expense.ap (amount + TDS, party vendor) / Cr bank (amount)
                                       / Cr expense.tds_payable (TDS)
* expense_claim:<pk>     approval:    Dr claim.category.<category pk> (else claim.expense_default)
                                       / Cr claim.reimbursement_payable (party employee), approved amount in INR
* reimbursement:<pk>     payout:      Dr claim.reimbursement_payable (party employee) / Cr bank
Cancelling an expense (or a claim / rejecting it) reverses its live entry; history is kept.
Amounts in a foreign currency are converted with the document's exchange_rate (q2).
"""
from accounting.money import ZERO, q2
from accounting.postings import L, bank_ledger, m, mapped_or, sync, unpost

UNTRACKED = ['Recurring expenses: expense_management has no recurring-expense model/schedule, so nothing to post',
             'GST input credit on bills (gst.input.* is a manual claim by design; GST is booked in the expense line)',
             'Vendor advances: payments on a cancelled bill remain as a debit balance on expense.ap for that vendor']


def expense_key(e):
    return f'company_expense:{e.pk}'


def payment_key(p):
    return f'expense_payment:{p.pk}'


def claim_key(c):
    return f'expense_claim:{c.pk}'


def reimbursement_key(r):
    return f'reimbursement:{r.pk}'


def _inr(v, rate):
    return q2((v or ZERO) * (rate or 1))


def _vendor(e):
    return ('vendor', e.vendor_id) if e.vendor_id else None


def expense_lines(e):
    if e.is_cancelled:
        return []
    total = _inr(e.total_amount, e.exchange_rate)
    desc = f'{e.expense_id} {e.invoice_number} {e.description}'.strip()
    acct = mapped_or('expense.prior_period', 'expense.default') if e.is_prior_period else \
        mapped_or(f'expense.category.{e.category_id}', 'expense.default')
    if e.is_prior_period and e.relates_to_fy_id:
        desc = f'{desc} [prior period {e.relates_to_fy.name}]'
    return [L(acct, debit=total, description=desc),
            L(m('expense.ap'), credit=total, description=desc, party=_vendor(e))]


def post_expense(e):
    if e.is_cancelled:
        return unpost(expense_key(e), e.cancel_reason or 'Expense cancelled')
    return sync(expense_key(e), e.expense_date, f'Expense {e.expense_id}', expense_lines(e), 'company_expense', e.pk)


def payment_lines(p):
    e = p.expense
    amt, tds = _inr(p.amount, e.exchange_rate), _inr(p.tds_amount, e.exchange_rate)
    bank = bank_ledger(p.bank, 'expense.payment_bank', cash=(p.method == 'cash' and not p.bank_id))
    desc = f'{e.expense_id} payment {p.reference}'.strip()
    return [L(m('expense.ap'), debit=amt + tds, description=desc, party=_vendor(e)),
            L(bank, credit=amt, description=desc),
            L(m('expense.tds_payable'), credit=tds, description=desc + ' TDS', party=_vendor(e))]


def post_payment(p):
    return sync(payment_key(p), p.payment_date, f'Vendor payment {p.expense.expense_id}', payment_lines(p),
                'expense_payment', p.pk)


def claim_lines(c):
    if c.status not in c.APPROVED_STATES or not c.approved_amount:
        return []
    amt = _inr(c.approved_amount, c.exchange_rate)
    desc = f'{c.claim_id} {c.snap_name} {c.description}'.strip()
    acct = mapped_or('expense.prior_period', 'claim.expense_default') if c.is_prior_period else \
        mapped_or(f'claim.category.{c.category_id}', 'claim.expense_default')
    if c.is_prior_period and c.relates_to_fy_id:
        desc = f'{desc} [prior period {c.relates_to_fy.name}]'
    return [L(acct, debit=amt, description=desc),
            L(m('claim.reimbursement_payable'), credit=amt, description=desc, party=('employee', c.employee_id))]


def post_claim(c):
    lines = claim_lines(c)
    if not lines:
        return unpost(claim_key(c), c.cancel_reason or c.rejection_reason or f'Claim {c.get_status_display()}')
    return sync(claim_key(c), c.expense_date, f'Claim {c.claim_id}', lines, 'expense_claim', c.pk,
                reason='Approved amount changed')


def reimbursement_lines(r):
    c = r.claim
    amt = _inr(r.amount, c.exchange_rate)
    bank = bank_ledger(r.bank, 'claim.payment_bank', cash=(r.method == 'cash' and not r.bank_id))
    desc = f'{c.claim_id} reimbursement {r.reference}'.strip()
    return [L(m('claim.reimbursement_payable'), debit=amt, description=desc, party=('employee', c.employee_id)),
            L(bank, credit=amt, description=desc)]


def post_reimbursement(r):
    return sync(reimbursement_key(r), r.reimbursement_date, f'Reimbursement {r.claim.claim_id}',
                reimbursement_lines(r), 'reimbursement', r.pk)
