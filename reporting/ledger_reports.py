"""Stage 5 report builders: ledger-based statements. Figures come only from reporting.ledger (JournalLine)."""
from datetime import date

from accounting.models import Account
from accounting.money import ZERO, q2

from . import ledger as LG
from . import services as S
from .core import Col, Report, Table

M, T, D = 'money', 'text', 'date'
SEC = 'Ledger'
BASIS = 'General ledger: posted journal entries (reversed originals with their reversal entries) by entry date'
ASOF = 'General ledger balances as of the period end date'
STMT = 'Management P&L (ledger-based), not statutory'


def _acct(a):
    return f'{a.code} {a.name}'


def _party(ln):
    if not ln.party_id:
        return ''
    n = LG.party_names(ln.party_type, [ln.party_id]).get(ln.party_id, f'{ln.party_type} #{ln.party_id}')
    return f'{LG.PARTY_TYPES.get(ln.party_type, ln.party_type)}: {n}'


def _new(key, title, period, basis=BASIS, **kw):
    r = Report(key, title, SEC, basis, period, **kw)
    r.notes.append('Source: accounting journal lines only (draft entries excluded). '
                   'Not a statutory financial statement.')
    return r


# ------------------------------------------------------------------ trial balance
def trial_balance(p, period):
    r = _new('ledger_tb', 'Trial Balance (ledger)', period, landscape=True)
    rows, tot = LG.trial_balance(period.start, period.end)
    t = Table('Trial balance', [Col('Code'), Col('Account'), Col('Type'), Col('Opening Dr', M), Col('Opening Cr', M),
                                Col('Debit', M), Col('Credit', M), Col('Closing Dr', M), Col('Closing Cr', M)])
    for x in rows:
        a = x['account']
        t.add([a.code, a.name, a.get_account_type_display(), x['opening_dr'], x['opening_cr'], x['debit'], x['credit'],
               x['closing_dr'], x['closing_cr']])
    t.total = ['Total', '', '', tot['opening_dr'], tot['opening_cr'], tot['debit'], tot['credit'], tot['closing_dr'],
               tot['closing_cr']]
    r.tables.append(t)
    chk = Table('Balance check', [Col('Check'), Col('Difference', M), Col('Result')])
    chk.add(['Period debits - credits', tot['period_difference'], 'OK' if not tot['period_difference'] else 'NOT BALANCED'],
            '' if not tot['period_difference'] else 'group')
    chk.add(['Closing Dr - Cr', tot['difference'], 'OK' if not tot['difference'] else 'NOT BALANCED'],
            '' if not tot['difference'] else 'group')
    r.tables.append(chk)
    if not tot['balanced']:
        r.statement = 'WARNING: TRIAL BALANCE IS NOT BALANCED - investigate before relying on any ledger report.'
    r.card('Total debits (period)', tot['debit'])
    r.card('Total credits (period)', tot['credit'])
    r.card('Balanced', 'Yes' if tot['balanced'] else 'NO - NOT BALANCED', 'text')
    return r


# ------------------------------------------------------------------ general ledger
def general_ledger(p, period):
    acct = None
    if str(p.get('account', '')).isdigit():
        acct = Account.objects.filter(pk=int(p['account'])).first()
    r = _new('ledger_gl', 'General Ledger', period, landscape=True)
    r.filters.append(('Account', _acct(acct) if acct else 'All accounts'))
    t = Table('General ledger', [Col('Account'), Col('Date', D), Col('Entry'), Col('Narration'), Col('Party'),
                                 Col('Debit', M), Col('Credit', M), Col('Balance (Dr+ / Cr-)', M)], paginate=True)
    td = tc = ZERO
    for g in LG.general_ledger(period.start, period.end, acct):
        a = _acct(g['account'])
        t.add([a, period.start, '', 'Opening balance', '', '', '', g['opening']], 'group')
        for x in g['lines']:
            ln = x['line']
            t.add([a, ln.entry.date, ln.entry.number or f'#{ln.entry_id}', ln.description or ln.entry.narration,
                   _party(ln), ln.debit, ln.credit, x['balance']])
        t.add([f'Closing: {a}', period.end, '', '', '', g['debit'], g['credit'], g['closing']], 'subtotal')
        td += g['debit']
        tc += g['credit']
    t.total = ['Total', '', '', '', '', q2(td), q2(tc), '']
    r.tables.append(t)
    r.card('Debits', q2(td))
    r.card('Credits', q2(tc))
    return r


# ------------------------------------------------------------------ P&L
def pnl(p, period):
    r = _new('ledger_pnl', 'Profit & Loss (ledger)', period)
    r.statement = STMT
    x = LG.profit_and_loss(period.start, period.end)
    t = Table(STMT, [Col('Code'), Col('Account'), Col('Amount (INR)', M)])
    t.add(['', 'Income', ''], 'group')
    for y in x['income']:
        t.add([y['account'].code, y['account'].name, y['amount']])
    t.add(['', 'Total income', x['total_income']], 'subtotal')
    t.add(['', 'Expenses', ''], 'group')
    for y in x['expenses']:
        t.add([y['account'].code, y['account'].name, y['amount']])
    t.add(['', 'Total expenses', x['total_expenses']], 'subtotal')
    if x['prior_period_total']:
        t.add(['', 'of which prior-period expenses', x['prior_period_total']], 'subtotal')
    t.total = ['', 'Net profit / (loss)', x['profit']]
    r.tables.append(t)
    r.card('Income', x['total_income'])
    r.card('Expenses', x['total_expenses'])
    r.card('Net profit / (loss)', x['profit'])
    return r


# ------------------------------------------------------------------ balance sheet
def balance_sheet(p, period):
    r = _new('ledger_bs', 'Balance Sheet (ledger)', period, ASOF)
    b = LG.balance_sheet(period.end)
    r.statement = f'Management Balance Sheet (ledger-based) as of {period.end:%d %b %Y}, not statutory'
    t = Table(f'Balance sheet as of {period.end:%d-%m-%Y}', [Col('Code'), Col('Account'), Col('Amount (INR)', M)])
    for lab, k, tk in (('Assets', 'assets', 'total_assets'), ('Liabilities', 'liabilities', 'total_liabilities'),
                       ('Equity', 'equity', 'total_equity')):
        t.add(['', lab, ''], 'group')
        for y in b[k]:
            t.add([y['account'].code, y['account'].name, y['amount']])
        if k == 'equity':
            t.add(['', f'Current year earnings (FY from {b["fy_start"]:%d-%m-%Y})', b['current_year_earnings']])
            t.add(['', 'Retained earnings - prior years (unclosed P&L)', b['prior_unclosed_earnings']])
            t.add(['', 'Total equity', q2(b['total_equity'] + b['current_year_earnings'] + b['prior_unclosed_earnings'])],
                  'subtotal')
        else:
            t.add(['', f'Total {lab.lower()}', b[tk]], 'subtotal')
    t.add(['', 'Total liabilities + equity', b['total_liabilities_equity']], 'subtotal')
    t.total = ['', 'Check: Assets - (Liabilities + Equity)', b['difference']]
    r.tables.append(t)
    if not b['balanced']:
        r.statement += ' - WARNING: NOT BALANCED'
    r.card('Total assets', b['total_assets'])
    r.card('Liabilities + equity', b['total_liabilities_equity'])
    r.card('Check (should be 0)', b['difference'])
    return r


# ------------------------------------------------------------------ cash flow
def cash_flow(p, period):
    r = _new('ledger_cash', 'Cash Flow (ledger, direct)', period)
    c = LG.cash_flow(period.start, period.end)
    r.filters.append(('Cash / bank accounts', ', '.join(_acct(a) for a in c['accounts']) or 'none'))
    t = Table('Cash flow by counter-account', [Col('Particulars'), Col('Inflow', M), Col('Outflow', M), Col('Net', M)])
    t.add(['Opening cash & bank', '', '', c['opening']], 'subtotal')
    for y in c['rows']:
        v = y['amount']
        t.add([_acct(y['account']), v if v > 0 else ZERO, -v if v < 0 else ZERO, v])
    if c['transfers']:
        t.add(['Transfers between cash / bank accounts', '', '', c['transfers']])
    t.add(['Net cash movement', c['inflow'], c['outflow'], c['net']], 'subtotal')
    t.total = ['Closing cash & bank', '', '', c['closing']]
    r.tables.append(t)
    r.notes.append(f"Check: opening + net - closing = {c['difference']} (should be 0).")
    r.card('Opening', c['opening'])
    r.card('Net movement', c['net'])
    r.card('Closing', c['closing'])
    return r


# ------------------------------------------------------------------ ageing
def _ageing(key, title, account_key, party_type, label, p, period):
    r = _new(key, title, period, ASOF + ' - FIFO ageing of party-tagged lines by entry date')
    a = LG.ageing(account_key, party_type, period.end)
    r.filters.append(('Account', _acct(a['account'])))
    t = Table(title, [Col(label)] + [Col(f'{b} days', M) for b in LG.BUCKETS] +
              [Col('Unapplied credits / advances', M), Col('Net balance', M)])
    for y in a['rows']:
        t.add([y['name']] + [y['buckets'][b] for b in LG.BUCKETS] + [y['advance'], y['total']])
    t.total = ['Total'] + [a['totals'][b] for b in LG.BUCKETS] + [a['advance'], a['total']]
    r.tables.append(t)
    if a['untagged']:
        r.limitations.append(f"{a['untagged']} on this account has no {party_type} tag and is not aged.")
    r.notes.append(f"Ledger account balance: {a['account_balance']}; aged total + untagged = "
                   f"{q2(a['total'] + a['untagged'])}.")
    r.card('Total outstanding', a['total'])
    r.card('Over 90 days', a['totals']['90+'])
    return r


def ar_ageing(p, period):
    return _ageing('ledger_ar', 'AR Ageing by Customer (ledger)', 'invoice.ar', 'customer', 'Customer', p, period)


def ap_ageing(p, period):
    return _ageing('ledger_ap', 'AP Ageing by Vendor (ledger)', 'expense.ap', 'vendor', 'Vendor', p, period)


# ------------------------------------------------------------------ payables
def payables(p, period):
    r = _new('ledger_payables', 'Payables Summary (ledger)', period, ASOF)
    t = Table('Statutory and employee payables', [Col('Group'), Col('Account'), Col('Mapping keys'),
                                                   Col('Balance (natural sign)', M)])
    for y in LG.payables(period.end):
        t.add([y['group'], _acct(y['account']), ', '.join(y['keys']), y['balance']])
    t.add_subtotals(0)
    r.tables.append(t)
    for k, lab in (('payroll.salary_payable', 'Salary payable by employee'),
                   ('claim.reimbursement_payable', 'Reimbursement payable by employee')):
        e = LG.employee_payable(k, period.end)
        et = Table(f"{lab} - {_acct(e['account'])}", [Col('Employee'), Col('Payable', M)])
        for y in e['rows']:
            et.add([y['name'], y['balance']])
        et.total = ['Total', e['total']]
        r.tables.append(et)
        r.card(lab.split(' by')[0], e['total'])
    r.notes.append('GST input accounts are assets (positive = credit available); a negative payable is a debit balance.')
    return r


# ------------------------------------------------------------------ day book
def day_book(p, period):
    r = _new('ledger_daybook', 'Day Book / Journal Register', period, landscape=True)
    t = Table('Journal register', [Col('Date', D), Col('Entry'), Col('Status'), Col('Source'), Col('Narration'),
                                   Col('Account'), Col('Party'), Col('Debit', M), Col('Credit', M)], paginate=True)
    for ln in LG.day_book(period.start, period.end):
        e = ln.entry
        t.add([e.date, e.number or f'#{e.pk}', e.get_status_display(), f'{e.source_type} {e.source_id}'.strip(),
               ln.description or e.narration, _acct(ln.account), _party(ln), ln.debit, ln.credit])
    t.sum_total(start=7)
    r.tables.append(t)
    return r


# ------------------------------------------------------------------ reconciliation
def reconciliation(p, period):
    r = _new('ledger_recon', 'Reconciliation: Management Reports vs Ledger', period,
             'Management figures (services.py) for the period / as of period end vs general ledger')
    s, e = period.start, period.end
    rows = []

    def add(label, mgmt, led, why):
        d = q2(mgmt - led)
        rows.append([label, mgmt, led, d, 'Ties' if not d else why])

    mrev = S.invoice_revenue(s, e)[0]
    ledger_revenue = q2(LG.account_movement('invoice.revenue', s, e) +
                        LG.account_movement('invoice.recruitment_revenue', s, e))
    add('Revenue (invoice taxable value)', q2(mrev), ledger_revenue,
        'Ledger posts only finalized INR invoices and nets credit notes; drafts / unposted or foreign-currency '
        'invoices count in the management report only.')
    add('Company expenses', S.company_expense_total(s, e),
        LG.source_movement(['company_expense'], [Account.EXPENSE], s, e),
        'Management uses expense date incl. any non-posted items; ledger posts by bill date (GST input stays in '
        'the expense line); check unposted / cancelled bills.')
    add('Employee claims expense', S.reimbursement_expense(s, e),
        LG.source_movement(['expense_claim'], [Account.EXPENSE], s, e),
        'Claims post on approval; management counts approved / partially approved / reimbursed states by claim date.')
    g, er = S.payroll_expense(s, e)
    add('Salary + employer cost', q2(g + er), LG.source_movement(['payroll'], [Account.EXPENSE], s, e),
        'Payroll posts on finalization per month; management includes finalized months whose 1st day is in range.')
    add('Accounts receivable (as of end)', S.receivables_outstanding(e), LG.account_balance('invoice.ar', e),
        'Management excludes overpayments (customer credits) and ignores TDS-receivable timing; ledger nets them.')
    add('Accounts payable (as of end)', S.dsum(x['outstanding_inr'] for x in S.vendor_payables(e)),
        LG.account_balance('expense.ap', e),
        'Management expense reports sum payment amounts WITHOUT TDS, so bills settled with TDS still show the TDS '
        'as outstanding there; the ledger clears AP by amount + TDS (TDS moves to TDS payable).')
    add('Salary payable (as of end)', S.dsum(x['outstanding'] for x in S.payroll_payable(e)),
        LG.account_balance('payroll.salary_payable', e),
        'Management uses current finalized payroll net salary less payments; ledger reflects posted payroll and '
        'salary payments by date (advances / adjustments may differ).')
    add('Reimbursement payable (as of end)', S.dsum(x['outstanding_inr'] for x in S.reimbursements_payable(e)),
        LG.account_balance('claim.reimbursement_payable', e),
        'Claims approved before the ledger went live and not backfilled appear only in the management report.')
    t = Table('Management vs ledger', [Col('Measure'), Col('Management report', M), Col('Ledger', M),
                                       Col('Difference', M), Col('Explanation')])
    for x in rows:
        t.add(x, '' if not x[3] else 'group')
    r.tables.append(t)
    r.card('Measures compared', len(rows), 'int')
    r.card('Measures with differences', sum(1 for x in rows if x[3]), 'int')
    return r


ITEMS = [('ledger_tb', 'Trial Balance', trial_balance), ('ledger_gl', 'General Ledger', general_ledger),
         ('ledger_pnl', 'Profit & Loss (ledger)', pnl), ('ledger_bs', 'Balance Sheet', balance_sheet),
         ('ledger_cash', 'Cash Flow (direct)', cash_flow), ('ledger_ar', 'AR Ageing', ar_ageing),
         ('ledger_ap', 'AP Ageing', ap_ageing), ('ledger_payables', 'Payables (salary / reimb. / GST / TDS / PF)', payables),
         ('ledger_daybook', 'Day Book', day_book), ('ledger_recon', 'Reconciliation vs Ledger', reconciliation)]
