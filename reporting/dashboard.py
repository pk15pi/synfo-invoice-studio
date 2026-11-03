"""Executive dashboard: 10 KPI cards + 8 inline SVG charts, all from services.*"""
from collections import Counter, defaultdict

from employee_management.models import Employee
from expense_management.models import EmployeeExpenseClaim as C

from . import charts
from . import periods as P
from . import services as S
from .reports import as_at, link, mname, pct


def executive(period):
    cur, pr = S.pnl(period.start, period.end), period.previous()
    old = S.pnl(pr.start, pr.end)
    a = as_at(period)
    vp = S.dsum(x['outstanding_inr'] for x in S.vendor_payables(a))
    rp = S.dsum(x['outstanding_inr'] for x in S.reimbursements_payable(a))
    active = Employee.objects.filter(status__in=Employee.EMPLOYED).count()
    sal = lambda d: S.q2(d['gross'] + d['employer'])
    mc, mo = pct(cur['profit'], cur['revenue']), pct(old['profit'], old['revenue'])

    def card(label, value, prev='_', kind='money', note='', key=''):
        from .periods import pct_change
        c = dict(label=label, value=value, kind=kind, note=note, prev=None, change='',
                 link=link(key, period) if key else '')
        if prev != '_' and value is not None and prev is not None and not isinstance(value, str) \
                and not isinstance(prev, str):
            c['prev'] = prev
            c['change'] = pct_change(value, prev)[1]
            c['up'] = value >= prev
        return c
    cards = [card('Total Revenue', cur['revenue'], old['revenue'], key='rev_summary'),
             card('Amount Received', S.invoice_receipts(period.start, period.end),
                  S.invoice_receipts(pr.start, pr.end), key='receipts_register'),
             card('Outstanding Receivables', S.receivables_outstanding(a), note=f'Customers, as at {a:%d %b %Y}',
                  key='receivables'),
             card('Total Salary Cost', sal(cur), sal(old), key='payroll_summary'),
             card('Employee Reimbursements', cur['claims'], old['claims'], key='employee_claims'),
             card('Company Expenses', cur['company'], old['company'], key='company_expenses'),
             card('Total Expenses', cur['total_expenses'], old['total_expenses'], key='rev_vs_exp'),
             card('Net Profit / Loss', cur['profit'], old['profit'], key='pnl'),
             card('Profit Margin', mc, mo, kind='pct', key='ratios'),
             card('Outstanding Payables', S.q2(vp + rp), note=f'Vendor + reimbursements as at {a:%d %b %Y}',
                  key='outstanding'),
             card('Active Employees', active, kind='int', key='emp_master')]
    mm = S.monthly_matrix(period.start, period.end)
    labels = [f'{P.date(k[0], k[1], 1):%b %y}' for k in mm]
    v = list(mm.values())
    top = defaultdict(lambda: S.ZERO)
    for n, t in S.invoices(period.start, period.end).values_list('client__name', 'taxable_value'):
        top[n] += S.q2(t)
    st = Counter()
    for x in S.claims(period.start, period.end, None):
        st[x['status']] += 1
    dept = Counter(Employee.objects.filter(status__in=Employee.EMPLOYED).values_list('department__name', flat=True))
    sd = dict(C.STATUSES)
    out = [
        ('Monthly Revenue vs Received', charts.bars(labels, [[x['revenue'] for x in v], [x['received'] for x in v]],
                                                    'Monthly revenue', legend=['Revenue', 'Received']), 'rev_monthly'),
        ('Revenue vs Expenses', charts.bars(labels, [[x['revenue'] for x in v], [x['total_expenses'] for x in v]],
                                            'Revenue vs expenses', legend=['Revenue', 'Expenses']), 'rev_vs_exp'),
        ('Monthly Profit / Loss', charts.bars(labels, [[x['profit'] for x in v]], 'Profit'), 'monthly_perf'),
        ('Salary Cost Trend', charts.line(labels, [[x['salary'] for x in v]], 'Salary'), 'payroll_summary'),
        ('Expenses by Group', charts.hbars(sorted(cur['groups'].items(), key=lambda kv: -kv[1]) +
                                           [('Salary + employer', sal(cur)), ('Reimbursements', cur['claims'])],
                                           'Expenses'), 'exp_summary'),
        ('Top Clients by Revenue', charts.hbars(sorted(top.items(), key=lambda kv: -kv[1])[:8], 'Clients'),
         'rev_customer'),
        ('Expense Claims by Status', charts.hbars([(sd.get(k, k), n) for k, n in st.most_common()], 'Claims',
                                                  money=False), 'employee_claims'),
        ('Employees by Department', charts.hbars([(k or 'No department', n) for k, n in dept.most_common()], 'Dept',
                                                 money=False), 'emp_department'),
    ]
    return dict(cards=cards, charts=[dict(title=t, svg=s, link=link(k, period)) for t, s, k in out], pnl=cur,
                prev_period=pr)
