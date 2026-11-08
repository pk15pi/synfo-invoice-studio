"""Centralised, Decimal-exact financial calculations for every report (the single source of truth).

Rules
* Each figure comes from exactly one model/field and one date basis (see SOURCES below).
* One-to-many data (invoice lines, payroll earnings/deductions, expense payments, reimbursements) is
  aggregated in SEPARATE queries and joined in Python by id, so a join can never multiply a total.
* Foreign-currency expense records are converted with each record's own stored exchange rate and
  rounded per record (q2), exactly like Expense Management does, so both modules agree to the paisa.
* Nothing is estimated: figures with no source (payroll payments) are returned as None and shown as "Not tracked".
* Revenue (P&L) = invoice TAXABLE value (excluding GST). GST is shown separately and is never revenue.
  Cancelled invoices are excluded from revenue and receivables and listed separately.
* Customer receipts come from invoice_app.InvoicePayment by receipt date (cash basis) and never add revenue.
* OUTSTANDING POLICY: outstanding = invoice grand total - amount received - TDS/bank charges written off."""
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Q, Sum

from . import prior as PR

from employee_management.models import (Employee, EmployeePayroll, EmployeeSalaryStructure, PayrollDeduction,
                                        PayrollEarning)
from expense_management.models import (CompanyExpense, CompanyExpensePayment, EmployeeExpenseClaim,
                                       EmployeeReimbursement)
from invoice_app.models import BusinessProfile, Invoice, InvoiceLine, InvoicePayment

ZERO = Decimal('0.00')
NOT_TRACKED = None
C = EmployeeExpenseClaim


def q2(v):
    return Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def dsum(it):
    return q2(sum((Decimal(x or 0) for x in it), ZERO))


# --------------------------------------------------------------------------------------------- policy
SOURCES = [
    # (figure, model.field, date basis, status rule)
    ('Invoice revenue', 'invoice_app.Invoice.taxable_value (excluding GST; = line total when no tax)',
     'Invoice Date', 'Cancelled invoices (Invoice.cancelled) are excluded and listed separately.'),
    ('GST / grand total', 'invoice_app.Invoice cgst_amount + sgst_amount + igst_amount = total_tax; grand_total',
     'Invoice Date', 'Shown separately; tax is never revenue.'),
    ('Project revenue / days', 'invoice_app.InvoiceLine.amount and quantity_days', 'Invoice Date of the parent invoice',
     'Lines grouped by linked project (or the free-text project name when not linked). Cancelled invoices excluded.'),
    ('Customer receipts / amount received', 'invoice_app.InvoicePayment.amount', 'Receipt Date (cash basis)',
     'Every recorded receipt. TDS / bank charges (InvoicePayment.deductions) are written off, not cash.'),
    ('Customer receivables', 'Invoice.grand_total - InvoicePayment.amount - InvoicePayment.deductions (receipts dated '
     'on/before the as-at date)', 'As at period end', 'Cancelled invoices excluded. Ageing from Due Date '
     '(Invoice Date when no due date): Not Due, 1-30, 31-60, 61-90, >90 days.'),
    ('Payroll gross / deductions / net', 'employee_management.EmployeePayroll gross_earnings, total_deductions, '
     'net_salary (status=finalized, is_current=True)', 'Payroll month (included when the 1st of the month is in range)',
     'Draft and superseded revisions are excluded. Snapshot name/department/designation are used.'),
    ('Earnings components', 'employee_management.PayrollEarning.amount by code', 'Payroll month', 'Finalized rows only.'),
    ('Employee deductions (PF/ESI/PT/TDS/other)', 'PayrollDeduction side=employee', 'Payroll month',
     'Part of gross salary - never added again as a company expense.'),
    ('Employer contributions', 'EmployeePayroll.employer_contributions (= PayrollDeduction side=employer: PF, ESI, other)',
     'Payroll month', 'Company cost in addition to gross salary.'),
    ('Salary payments', 'SalaryPayment (not cancelled) amount', 'Payment Date',
     'Partial payments allowed. PayrollPeriod.payment_date is only the legacy expected/bank-file date.'),
    ('Company expense', 'expense_management.CompanyExpense.total_amount x exchange_rate', 'Expense Date',
     'payment_status=cancelled excluded (listed separately).'),
    ('Vendor payments', 'expense_management.CompanyExpensePayment.amount x expense exchange_rate', 'Actual Payment Date',
     'Every recorded payment.'),
    ('Vendor payables', 'CompanyExpense total - payments dated on/before the as-at date', 'As at period end',
     'Cancelled expenses excluded. Ageing from Due Date (Expense Date when no due date).'),
    ('Employee reimbursement expense', 'EmployeeExpenseClaim.approved_amount x exchange_rate '
     '(status approved / partially approved / reimbursed)', 'Claim Expense Date',
     'Draft, submitted, under review, rejected and cancelled claims are never an expense.'),
    ('Reimbursement cash', 'expense_management.EmployeeReimbursement.amount x claim exchange_rate',
     'Actual Reimbursement Date', 'Every recorded reimbursement.'),
    ('Reimbursements payable', 'approved amount - reimbursements dated on/before the as-at date', 'As at period end',
     'Approved states only. Ageing from Claim Date.'),
]
LIMITATIONS = [
    'Customer receipts are recorded per invoice (Receipts). Amounts received before this feature existed are not '
    'in the app unless entered; their invoices show as outstanding until a payment is recorded.',
    'Salary payments are recorded only from ledger integration onwards (Salary payments on each payslip); salaries '
    'paid before that show as payable until a payment is recorded.',
    'Other business receipts (non-invoice income) are not supported by any module.',
    'Employer contributions are available only as calculated on finalized payroll (Statutory Rules, employer side).',
]
BASIS = {
    'revenue': 'Invoice Date (accrual: revenue = taxable value excluding GST; cancelled invoices excluded)',
    'receipts': 'Receipt Date (cash basis: customer payments recorded against invoices)',
    'payroll': 'Payroll month (finalized payroll; month included when its 1st day is in range)',
    'company': 'Expense Date (company expenses, cancelled excluded)',
    'claims': 'Claim Expense Date (approved amount of approved / partially approved / reimbursed claims)',
    'cash': 'Actual recorded payment / reimbursement date',
    'asat': 'As at the period end date (balances) - ageing from due date',
    'mixed': 'Management accrual basis: revenue = invoice taxable value (excluding GST) by Invoice Date, payroll by payroll month, company expenses by '
             'Expense Date, reimbursements by Claim Expense Date (approved amount)',
}


def profile():
    return BusinessProfile.current()


# --------------------------------------------------------------------------------------------- revenue
def invoices(start, end, client=None, include_cancelled=False):
    qs = Invoice.objects.filter(invoice_date__range=(start, end)).select_related('client')
    if not include_cancelled:
        qs = qs.filter(cancelled=False)
    if client:
        qs = qs.filter(client_id=client)
    return qs.order_by('invoice_date', 'invoice_number')


def cancelled_invoices(start, end, client=None):
    return invoices(start, end, client, include_cancelled=True).filter(cancelled=True)


def invoice_revenue(start, end, client=None):
    """Revenue = taxable value (excluding GST) of non-cancelled invoices by Invoice Date."""
    a = invoices(start, end, client).aggregate(s=Sum('taxable_value'), n=Count('id'))
    return q2(a['s']), a['n']


def invoice_tax_totals(start, end, client=None):
    a = invoices(start, end, client).aggregate(t=Sum('taxable_value'), c=Sum('cgst_amount'), s=Sum('sgst_amount'),
                                               i=Sum('igst_amount'), x=Sum('total_tax'), g=Sum('grand_total'),
                                               n=Count('id'))
    return dict(taxable=q2(a['t']), cgst=q2(a['c']), sgst=q2(a['s']), igst=q2(a['i']), tax=q2(a['x']),
                grand=q2(a['g']), n=a['n'])


def payments(start, end, client=None):
    """Receipts register: every InvoicePayment with receipt_date in range (cash basis)."""
    qs = InvoicePayment.objects.filter(receipt_date__range=(start, end)).select_related('invoice', 'invoice__client')
    if client:
        qs = qs.filter(invoice__client_id=client)
    return qs.order_by('receipt_date', 'id')


def invoice_receipts(start, end, client=None):
    a = payments(start, end, client).aggregate(s=Sum('amount'))
    return q2(a['s'])


def _settled_upto(as_at, ids):
    out = defaultdict(lambda: [ZERO, ZERO])
    qs = InvoicePayment.objects.filter(invoice_id__in=ids)
    if as_at:
        qs = qs.filter(receipt_date__lte=as_at)
    for iid, a, d in qs.values('invoice_id').annotate(a=Sum('amount'), d=Sum('deductions')).values_list(
            'invoice_id', 'a', 'd'):
        out[iid] = [q2(a), q2(d)]
    return out


def _credited_upto(as_at, ids):
    from invoice_app.models import CreditNote
    qs = CreditNote.objects.filter(invoice_id__in=ids, status=CreditNote.ACTIVE)
    if as_at:
        qs = qs.filter(date__lte=as_at)
    return {iid: q2(t) for iid, t in qs.values('invoice_id').annotate(t=Sum('total')).values_list('invoice_id', 't')}


def pay_status(grand, settled):
    if settled <= 0:
        return 'Unpaid'
    out = grand - settled
    return 'Partially Paid' if out > 0 else 'Paid' if out == 0 else 'Overpaid'


def invoice_balances(as_at, client=None, start=date(2000, 1, 1), end=None):
    """Per non-cancelled invoice dated start..(end or as_at): grand, received, deductions, outstanding,
    status, age bucket - payments counted only when dated on/before ``as_at``."""
    invs = list(invoices(start, end or as_at, client))
    paid = _settled_upto(as_at, [i.pk for i in invs])
    credited = _credited_upto(as_at, [i.pk for i in invs])
    rows = []
    for i in invs:
        rec, ded = paid[i.pk]
        g = q2(i.grand_total)
        o = q2(g - rec - ded - credited.get(i.pk, ZERO))
        b, d = age_bucket(i.due_date or i.invoice_date, as_at) if o > 0 else ('-', 0)
        rows.append(dict(inv=i, grand=g, received=rec, deductions=ded, outstanding=o,
                         status=pay_status(g, rec + ded), bucket=b, days=d))
    return rows


def receivables_outstanding(as_at, client=None):
    """Sum of positive invoice balances (overpayments are customer credits, reported separately)."""
    return dsum(r['outstanding'] for r in invoice_balances(as_at, client) if r['outstanding'] > 0)


def invoice_lines(start, end, client=None, project=None, consultant=''):
    qs = InvoiceLine.objects.filter(invoice__invoice_date__range=(start, end), invoice__cancelled=False)
    if client:
        qs = qs.filter(invoice__client_id=client)
    if project:
        qs = qs.filter(project_id=project)
    if consultant:
        qs = qs.filter(project_name__icontains=consultant)
    return qs.select_related('invoice', 'invoice__client', 'project')


def consultant_of(name):
    return (name or '').split(' - ')[0].strip()


def monthly_revenue(start, end):
    out = defaultdict(lambda: [0, ZERO])
    for d, t in invoices(start, end).values_list('invoice_date', 'taxable_value'):
        k = (d.year, d.month)
        out[k][0] += 1
        out[k][1] += q2(t)
    return out


# --------------------------------------------------------------------------------------------- payroll
def _ym_bounds(start, end):
    s = (start.year, start.month) if start.day == 1 else ((start.year + 1, 1) if start.month == 12 else
                                                          (start.year, start.month + 1))
    return s[0] * 100 + s[1], end.year * 100 + end.month


def payrolls(start, end, employee=None, department=''):
    lo, hi = _ym_bounds(start, end)
    qs = (EmployeePayroll.objects.filter(status=EmployeePayroll.FINALIZED, is_current=True)
          .annotate(_ym=F('period__year') * 100 + F('period__month')).filter(_ym__gte=lo, _ym__lte=hi)
          .select_related('period'))
    if employee:
        qs = qs.filter(employee_id=employee)
    if department:
        qs = qs.filter(snap_department=department)
    return qs.order_by('period__year', 'period__month', 'snap_employee_code')


def payroll_totals(qs):
    a = qs.aggregate(gross=Sum('gross_earnings'), ded=Sum('total_deductions'), er=Sum('employer_contributions'),
                     net=Sum('net_salary'), rnd=Sum('rounding_adjustment'), n=Count('id'),
                     emps=Count('employee', distinct=True))
    return {k: (q2(v) if k not in ('n', 'emps') else v) for k, v in a.items()}


EARN_GROUP = {'basic': 'basic', 'hra': 'hra', 'conveyance': 'allowances', 'medical': 'allowances',
              'special': 'allowances', 'other_allowance': 'allowances', 'bonus': 'bonus', 'incentive': 'bonus',
              'incentive_input': 'bonus', 'overtime': 'overtime', 'overtime_input': 'overtime'}


def earn_group(code):
    return EARN_GROUP.get(code, 'other')


def payroll_components(qs):
    """{payroll_id: {'basic':..,'hra':..,'allowances':..,'bonus':..,'overtime':..,'other':..,
    'pf':..,'esi':..,'tds':..,'pt':..,'other_ded':..,'er_pf':..,'er_esi':..,'er_other':..}} - separate queries."""
    ids = list(qs.values_list('id', flat=True))
    out = defaultdict(lambda: defaultdict(lambda: ZERO))
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for pid, code, amt in PayrollEarning.objects.filter(payroll_id__in=chunk).values_list('payroll_id', 'code', 'amount'):
            out[pid][earn_group(code)] += q2(amt)
        for pid, side, code, amt in PayrollDeduction.objects.filter(payroll_id__in=chunk).values_list(
                'payroll_id', 'side', 'code', 'amount'):
            if side == 'employer':
                out[pid]['er_pf' if code == 'pf' else 'er_esi' if code == 'esi' else 'er_other'] += q2(amt)
            else:
                k = {'pf': 'pf', 'esi': 'esi', 'tds': 'tds', 'professional_tax': 'pt'}.get(code, 'other_ded')
                out[pid][k] += q2(amt)
    return out


def employer_split(qs):
    t = defaultdict(lambda: ZERO)
    for code, amt in PayrollDeduction.objects.filter(payroll__in=qs, side='employer').values_list('code', 'amount'):
        t['pf' if code == 'pf' else 'esi' if code == 'esi' else 'other'] += q2(amt)
    return t


def payroll_expense(start, end):
    t = payroll_totals(payrolls(start, end))
    return t['gross'], t['er']


def payroll_payments(start, end):
    """Live (not cancelled) salary payments dated start..end."""
    from employee_management.models import SalaryPayment
    return SalaryPayment.objects.filter(cancelled=False, payment_date__range=(start, end)).select_related(
        'payroll', 'payroll__period', 'bank_account')


def salary_paid_upto(as_at=None):
    """{(period_id, employee_id): paid} for live salary payments dated on/before ``as_at``."""
    from employee_management.models import SalaryPayment
    qs = SalaryPayment.objects.filter(cancelled=False)
    if as_at:
        qs = qs.filter(payment_date__lte=as_at)
    out = defaultdict(lambda: ZERO)
    for pid, eid, a in qs.values('payroll__period_id', 'payroll__employee_id').annotate(a=Sum('amount')).values_list(
            'payroll__period_id', 'payroll__employee_id', 'a'):
        out[(pid, eid)] = q2(a)
    return out


def payroll_payable(as_at):
    """Net salary of current finalized payroll for months started on/before ``as_at`` minus payments to date."""
    paid = salary_paid_upto(as_at)
    rows = []
    qs = EmployeePayroll.objects.filter(status=EmployeePayroll.FINALIZED, is_current=True).select_related('period')
    for x in qs.order_by('period__year', 'period__month', 'snap_employee_code'):
        if x.period.start > as_at:
            continue
        p = paid[(x.period_id, x.employee_id)]
        rows.append(dict(p=x, net=q2(x.net_salary), paid=p, outstanding=q2(x.net_salary - p)))
    return rows


def salary_of(structure):
    return q2(structure.gross_monthly)


def structures(employee=None):
    qs = EmployeeSalaryStructure.objects.select_related('employee', 'employee__department').prefetch_related('components')
    if employee:
        qs = qs.filter(employee_id=employee)
    return qs.order_by('employee_id', 'effective_from', 'id')


def salary_changes(start=None, end=None, employee=None):
    """Consecutive salary structures per employee; a change is effective when the later one starts."""
    rows, prev = [], {}
    for s in structures(employee):
        p = prev.get(s.employee_id)
        cur = salary_of(s)
        if p is not None:
            old = salary_of(p)
            if (start is None or start <= s.effective_from <= end) and (cur != old or p.structure_type != s.structure_type):
                diff = q2(cur - old)
                pct = (diff / old * 100).quantize(Decimal('0.01')) if old else None
                rows.append(dict(structure=s, employee=s.employee, effective=s.effective_from, old=old, new=cur,
                                 diff=diff, pct=pct, old_type=p.get_structure_type_display(),
                                 new_type=s.get_structure_type_display()))
        prev[s.employee_id] = s
    return rows


def current_structure_map(as_at=None):
    as_at = as_at or date.today()
    m = {}
    for s in structures():
        if s.effective_from <= as_at and (s.effective_to is None or s.effective_to >= as_at):
            m[s.employee_id] = s
    return m


# --------------------------------------------------------------------------------------------- expenses
GROUPS = ['Software and IT', 'Office Rent', 'Internet and Utilities', 'Travel', 'Professional Fees', 'Marketing',
          'Other Expenses']
TREND_GROUPS = {'Software and IT': 'Software and IT', 'Office Rent': 'Rent and Utilities',
                'Internet and Utilities': 'Rent and Utilities', 'Travel': 'Travel',
                'Professional Fees': 'Professional Fees', 'Marketing': 'Marketing', 'Other Expenses': 'Other Company Expenses'}
GROUP_RULES_TEXT = ('Category mapping: Technology -> Software and IT; Office/Rent -> Office Rent; '
                    'Office/Electricity, Internet, water or utility subcategories -> Internet and Utilities; Travel -> Travel; '
                    'Professional Services -> Professional Fees; Marketing and Sales -> Marketing; everything else -> Other Expenses.')


def group_of(category, subcategory=''):
    c, s = (category or '').lower(), (subcategory or '').lower()
    if 'technolog' in c or 'software' in c or c == 'it':
        return 'Software and IT'
    if 'rent' in s and 'office' in c:
        return 'Office Rent'
    if any(k in s for k in ('electric', 'internet', 'utilit', 'water', 'phone', 'broadband')):
        return 'Internet and Utilities'
    if 'travel' in c:
        return 'Travel'
    if 'professional' in c:
        return 'Professional Fees'
    if 'marketing' in c:
        return 'Marketing'
    return 'Other Expenses'


EXP_FIELDS = ('id', 'expense_id', 'expense_date', 'due_date', 'invoice_number', 'invoice_date', 'description',
              'total_amount', 'exchange_rate', 'currency', 'payment_status', 'vendor_id', 'vendor__name', 'category_id',
              'category__name', 'subcategory__name', 'department_id', 'department__name', 'client_id', 'client__name',
              'project_id', 'project__name', 'employee_id', 'employee__employee_id', 'employee__first_name',
              'employee__last_name', 'is_prior_period', 'relates_to_fy__start_year')


def company_expenses(start, end, include_cancelled=False, as_at=None, **f):
    """List of dicts (one per expense) with INR total, INR paid (payments dated <= as_at) and outstanding."""
    qs = PR.apply(CompanyExpense.objects.filter(expense_date__range=(start, end)))
    qs = qs.filter(is_draft=False)
    if not include_cancelled:
        qs = qs.exclude(payment_status=CompanyExpense.CANCELLED)
    for k in ('vendor', 'category', 'department', 'client', 'project', 'employee'):
        if f.get(k):
            qs = qs.filter(**{f'{k}_id': f[k]})
    if f.get('status'):
        qs = qs.filter(payment_status=f['status'])
    rows = [dict(zip(EXP_FIELDS, r)) for r in qs.order_by('expense_date', 'expense_id').values_list(*EXP_FIELDS)]
    paid = defaultdict(lambda: ZERO)
    pq = CompanyExpensePayment.objects.filter(expense_id__in=[r['id'] for r in rows])
    if as_at:
        pq = pq.filter(payment_date__lte=as_at)
    for eid, s in pq.values('expense_id').annotate(s=Sum('amount')).values_list('expense_id', 's'):
        paid[eid] = q2(s)
    for r in rows:
        rate = r['exchange_rate'] or Decimal('1')
        r['total_inr'] = q2(r['total_amount'] * rate)
        r['paid_inr'] = q2(paid[r['id']] * rate)
        r['cancelled'] = r['payment_status'] == CompanyExpense.CANCELLED
        r['outstanding_inr'] = ZERO if r['cancelled'] else q2(r['total_inr'] - r['paid_inr'])
        r['group'] = group_of(r['category__name'], r['subcategory__name'])
        r['employee_name'] = ' '.join(x for x in (r['employee__first_name'], r['employee__last_name']) if x)
    return rows


def company_expense_total(start, end, **f):
    return dsum(r['total_inr'] for r in company_expenses(start, end, **f))


def vendor_payments(start, end, **f):
    qs = PR.apply(CompanyExpensePayment.objects.filter(payment_date__range=(start, end)), 'expense__')
    for k in ('vendor', 'category', 'department', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'expense__{k}_id': f[k]})
    rows = []
    for r in qs.order_by('payment_date', 'id').values('id', 'payment_date', 'amount', 'method', 'reference',
                                                     'expense__expense_id', 'expense__exchange_rate',
                                                     'expense__vendor__name', 'expense__category__name',
                                                     'expense__subcategory__name', 'expense__payment_status'):
        r['inr'] = q2(r['amount'] * (r['expense__exchange_rate'] or 1))
        rows.append(r)
    return rows


CLAIM_FIELDS = ('id', 'claim_id', 'claim_date', 'expense_date', 'status', 'amount_claimed', 'approved_amount',
                'exchange_rate', 'currency', 'description', 'employee_id', 'snap_employee_code', 'snap_name',
                'snap_department', 'department_id', 'department__name', 'category_id', 'category__name',
                'subcategory__name', 'client_id', 'client__name', 'project_id', 'project__name', 'is_prior_period',
                'relates_to_fy__start_year')


def claims(start, end, statuses=None, as_at=None, date_field='expense_date', **f):
    qs = PR.apply(C.objects.filter(**{f'{date_field}__range': (start, end)}))
    if statuses:
        qs = qs.filter(status__in=statuses)
    for k in ('employee', 'department', 'category', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'{k}_id': f[k]})
    rows = [dict(zip(CLAIM_FIELDS, r)) for r in qs.order_by('expense_date', 'claim_id').values_list(*CLAIM_FIELDS)]
    paid = defaultdict(lambda: ZERO)
    rq = EmployeeReimbursement.objects.filter(claim_id__in=[r['id'] for r in rows])
    if as_at:
        rq = rq.filter(reimbursement_date__lte=as_at)
    for cid, s in rq.values('claim_id').annotate(s=Sum('amount')).values_list('claim_id', 's'):
        paid[cid] = q2(s)
    for r in rows:
        rate = r['exchange_rate'] or Decimal('1')
        r['claimed_inr'] = q2(r['amount_claimed'] * rate)
        approved = r['status'] in C.APPROVED_STATES and r['approved_amount'] is not None
        r['approved_inr'] = q2(r['approved_amount'] * rate) if approved else ZERO
        r['reimbursed_inr'] = q2(paid[r['id']] * rate)
        r['outstanding_inr'] = q2(r['approved_inr'] - r['reimbursed_inr']) if approved else ZERO
        r['dept'] = r['department__name'] or r['snap_department'] or 'Unassigned'
        r['group'] = group_of(r['category__name'], r['subcategory__name'])
    return rows


def reimbursement_expense(start, end, **f):
    return dsum(r['approved_inr'] for r in claims(start, end, C.APPROVED_STATES, **f))


def reimbursements(start, end, **f):
    qs = PR.apply(EmployeeReimbursement.objects.filter(reimbursement_date__range=(start, end)), 'claim__')
    for k in ('employee', 'department', 'category', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'claim__{k}_id': f[k]})
    rows = []
    for r in qs.order_by('reimbursement_date', 'id').values('id', 'reimbursement_date', 'amount', 'method', 'reference',
                                                           'claim__claim_id', 'claim__exchange_rate', 'claim__snap_name',
                                                           'claim__snap_employee_code', 'claim__snap_department',
                                                           'claim__employee_id'):
        r['inr'] = q2(r['amount'] * (r['claim__exchange_rate'] or 1))
        rows.append(r)
    return rows


# --------------------------------------------------------------------------------------------- combined
def pnl(start, end):
    """Management P&L (accrual, mixed basis - see BASIS['mixed']). Deductions are inside gross salary and are never
    added again; only approved claims count; invoice receipts never add revenue."""
    rev, n_inv = invoice_revenue(start, end)
    pq = payrolls(start, end)
    pt = payroll_totals(pq)
    claims_exp = reimbursement_expense(start, end)
    groups = {g: ZERO for g in GROUPS}
    for r in company_expenses(start, end):
        groups[r['group']] += r['total_inr']
    company = dsum(groups.values())
    total_exp = q2(pt['gross'] + pt['er'] + claims_exp + company)
    return dict(revenue=rev, invoices=n_inv, gross=pt['gross'], employer=pt['er'], deductions=pt['ded'],
                net=pt['net'], claims=claims_exp, groups={k: q2(v) for k, v in groups.items()}, company=company,
                total_expenses=total_exp, profit=q2(rev - total_exp))


def monthly_matrix(start, end):
    """Per month: revenue, invoices, gross salary, employer contrib, claims, company expenses, totals, plus recorded
    cash (vendor payments, reimbursements). Each source is read once for the whole range."""
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    z = lambda: {k: ZERO for k in ('revenue', 'gross', 'employer', 'claims', 'company', 'vendor_paid', 'reimbursed',
                                   'received', 'salary_paid')}
    out = {k: z() for k in months}
    cnt = defaultdict(int)
    for (yy, mm), (n, t) in monthly_revenue(start, end).items():
        out[(yy, mm)]['revenue'] += t
        cnt[(yy, mm)] = n
    for yy, mm, g, er in payrolls(start, end).values_list('period__year', 'period__month', 'gross_earnings',
                                                          'employer_contributions'):
        if (yy, mm) in out:
            out[(yy, mm)]['gross'] += q2(g)
            out[(yy, mm)]['employer'] += q2(er)
    for r in claims(start, end, C.APPROVED_STATES):
        out[(r['expense_date'].year, r['expense_date'].month)]['claims'] += r['approved_inr']
    for r in company_expenses(start, end):
        out[(r['expense_date'].year, r['expense_date'].month)]['company'] += r['total_inr']
    for r in vendor_payments(start, end):
        out[(r['payment_date'].year, r['payment_date'].month)]['vendor_paid'] += r['inr']
    for r in reimbursements(start, end):
        out[(r['reimbursement_date'].year, r['reimbursement_date'].month)]['reimbursed'] += r['inr']
    for d, a in payroll_payments(start, end).values_list('payment_date', 'amount'):
        out[(d.year, d.month)]['salary_paid'] += q2(a)
    for d, a in payments(start, end).values_list('receipt_date', 'amount'):
        if (d.year, d.month) in out:
            out[(d.year, d.month)]['received'] += q2(a)
    for k, v in out.items():
        v['invoices'] = cnt[k]
        v['salary'] = q2(v['gross'] + v['employer'])
        v['total_expenses'] = q2(v['gross'] + v['employer'] + v['claims'] + v['company'])
        v['profit'] = q2(v['revenue'] - v['total_expenses'])
        v['cash_out'] = q2(v['vendor_paid'] + v['reimbursed'] + v['salary_paid'])
    return out


def cash_flow(start, end):
    vp = dsum(r['inr'] for r in vendor_payments(start, end))
    rb = dsum(r['inr'] for r in reimbursements(start, end))
    sp = dsum(q2(a) for a in payroll_payments(start, end).values_list('amount', flat=True))
    out_total = q2(vp + rb + sp)
    rc = invoice_receipts(start, end)
    return dict(receipts=rc, other_receipts=NOT_TRACKED, inflow=rc, salary_paid=sp,
                vendor_paid=vp, reimbursed=rb, outflow=out_total, net=q2(rc - out_total))


AGE_BUCKETS = ['Not Due', '1-30 Days', '31-60 Days', '61-90 Days', 'More Than 90 Days']


def age_bucket(due, as_at):
    if due is None or due >= as_at:
        return 'Not Due', 0
    d = (as_at - due).days
    return (AGE_BUCKETS[1] if d <= 30 else AGE_BUCKETS[2] if d <= 60 else AGE_BUCKETS[3] if d <= 90 else AGE_BUCKETS[4]), d


def vendor_payables(as_at, start=date(2000, 1, 1), **f):
    rows = [r for r in company_expenses(start, as_at, as_at=as_at, **f) if r['outstanding_inr'] > 0]
    for r in rows:
        r['bucket'], r['days'] = age_bucket(r['due_date'] or r['expense_date'], as_at)
    return rows


def reimbursements_payable(as_at, start=date(2000, 1, 1), **f):
    rows = [r for r in claims(start, as_at, C.APPROVED_STATES, as_at=as_at, **f) if r['outstanding_inr'] > 0]
    for r in rows:
        r['bucket'], r['days'] = age_bucket(r['claim_date'], as_at)
    return rows


def employee_related_other(start, end, employee=None):
    """Company expenses linked to an employee (already part of company expenses - shown, never added twice)."""
    return [r for r in company_expenses(start, end, employee=employee) if r['employee_id']]


def employees(**f):
    qs = Employee.objects.select_related('department')
    if f.get('department'):
        qs = qs.filter(department_id=f['department'])
    if f.get('designation'):
        qs = qs.filter(designation__icontains=f['designation'])
    if f.get('status'):
        qs = qs.filter(status=f['status'])
    return qs.order_by('employee_id')


def prior_period_subtotal(start, end):
    """Prior-period company expenses + approved claims in start..end, within the active Include/Exclude/Only mode
    (so it is 0 when prior-period items are excluded)."""
    if PR.current() == PR.EXCLUDE:
        return ZERO
    with PR.scope(PR.ONLY):
        return q2(company_expense_total(start, end) + reimbursement_expense(start, end))
