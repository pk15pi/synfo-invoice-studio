"""Custom report builder: pick a dataset, period, columns and an optional group-by. Data comes from services.* so the
figures match the standard reports."""
from collections import OrderedDict
from decimal import Decimal

from expense_management.models import EmployeeExpenseClaim as C

from . import periods as P
from . import services as S
from .core import Col, Report, Table


def _inv(p):
    for i in S.invoices(p.start, p.end).select_related('client').order_by('invoice_date', 'pk'):
        yield dict(number=i.display_number, date=i.invoice_date, due=i.due_date, client=i.client.name,
                   month=f'{i.invoice_date:%Y-%m}', fy=P.fy_label(P.fy_start_year(i.invoice_date)),
                   currency=i.currency, total=S.q2(i.total))


def _lines(p):
    for ln in S.invoice_lines(p.start, p.end).select_related('invoice__client', 'project'):
        d = ln.invoice.invoice_date
        yield dict(number=ln.invoice.display_number, date=d, client=ln.invoice.client.name,
                   project=ln.project.name if ln.project else ln.project_name, consultant=S.consultant_of(ln.description),
                   month=f'{d:%Y-%m}', fy=P.fy_label(P.fy_start_year(d)), days=S.q2(ln.quantity_days),
                   amount=S.q2(ln.amount))


def _payroll(p):
    # Use the values frozen on the payroll row, so later employee edits never rewrite history.
    for r in S.payrolls(p.start, p.end).select_related('employee', 'period'):
        yield dict(employee=f'{r.code} {r.snap_name or r.employee.full_name}',
                   department=r.snap_department,
                   month=f'{r.period.year}-{r.period.month:02d}',
                   fy=P.fy_label(P.fy_start_year(P.date(r.period.year, r.period.month, 1))),
                   gross=S.q2(r.gross_earnings), deductions=S.q2(r.total_deductions), net=S.q2(r.net_salary),
                   employer=S.q2(r.employer_contributions))


def _company(p):
    for x in S.company_expenses(p.start, p.end):
        yield dict(expense_id=x['expense_id'], date=x['expense_date'], vendor=x['vendor__name'] or '',
                   category=x['category__name'], group=x['group'], department=x.get('department__name') or '',
                   client=x['client__name'] or '', project=x['project__name'] or '', month=f"{x['expense_date']:%Y-%m}",
                   fy=P.fy_label(P.fy_start_year(x['expense_date'])), total=x['total_inr'], paid=x['paid_inr'],
                   outstanding=x['outstanding_inr'])


def _claims(p):
    sd = dict(C.STATUSES)
    for x in S.claims(p.start, p.end, None):
        yield dict(claim_id=x['claim_id'], date=x['expense_date'], employee=x['snap_name'], department=x['dept'],
                   category=x['category__name'], status=sd.get(x['status'], x['status']),
                   month=f"{x['expense_date']:%Y-%m}", fy=P.fy_label(P.fy_start_year(x['expense_date'])),
                   claimed=x['claimed_inr'], approved=x['approved_inr'] or S.ZERO, reimbursed=x['reimbursed_inr'])


DATASETS = OrderedDict([
    ('invoices', ('Invoices (Invoice Date)', _inv, [('number', 'Invoice', 'text'), ('date', 'Invoice Date', 'date'),
                  ('due', 'Due Date', 'date'), ('client', 'Client', 'text'), ('month', 'Month', 'text'),
                  ('fy', 'FY', 'text'), ('currency', 'Currency', 'text'), ('total', 'Invoice Total', 'money')])),
    ('invoice_lines', ('Invoice lines (Invoice Date)', _lines, [('number', 'Invoice', 'text'), ('date', 'Date', 'date'),
                       ('client', 'Client', 'text'), ('project', 'Project', 'text'),
                       ('consultant', 'Consultant', 'text'), ('month', 'Month', 'text'), ('fy', 'FY', 'text'),
                       ('days', 'Days', 'money'), ('amount', 'Amount', 'money')])),
    ('payroll', ('Finalized payroll (Payroll Month)', _payroll, [('employee', 'Employee', 'text'),
                 ('department', 'Department', 'text'), ('month', 'Month', 'text'), ('fy', 'FY', 'text'),
                 ('gross', 'Gross', 'money'), ('deductions', 'Deductions', 'money'), ('net', 'Net', 'money'),
                 ('employer', 'Employer Contributions', 'money')])),
    ('company_expenses', ('Company expenses (Expense Date)', _company, [('expense_id', 'Expense ID', 'text'),
                          ('date', 'Expense Date', 'date'), ('vendor', 'Vendor', 'text'), ('category', 'Category', 'text'),
                          ('group', 'P&L Group', 'text'), ('department', 'Department', 'text'),
                          ('client', 'Client', 'text'), ('project', 'Project', 'text'), ('month', 'Month', 'text'),
                          ('fy', 'FY', 'text'), ('total', 'Total (INR)', 'money'), ('paid', 'Paid (INR)', 'money'),
                          ('outstanding', 'Outstanding (INR)', 'money')])),
    ('claims', ('Employee claims (Claim Expense Date)', _claims, [('claim_id', 'Claim ID', 'text'),
                ('date', 'Expense Date', 'date'), ('employee', 'Employee', 'text'), ('department', 'Department', 'text'),
                ('category', 'Category', 'text'), ('status', 'Status', 'text'), ('month', 'Month', 'text'),
                ('fy', 'FY', 'text'), ('claimed', 'Claimed (INR)', 'money'), ('approved', 'Approved (INR)', 'money'),
                ('reimbursed', 'Reimbursed (INR)', 'money')])),
])


def build(params):
    ds = params.get('dataset') if params.get('dataset') in DATASETS else 'invoices'
    title, fn, fields = DATASETS[ds]
    period = P.resolve(params)
    picked = [f for f in fields if f[0] in (params.getlist('cols') if hasattr(params, 'getlist') else
                                           params.get('cols', []))] or fields
    group = params.get('group_by') or ''
    texts = [f for f in fields if f[2] == 'text']
    rep = Report('builder', f'Custom Report: {title}', 'Custom', title.split('(')[-1].rstrip(')') + ' basis', period,
                 landscape=len(picked) > 6)
    rows = list(fn(period))
    if group and group in dict((f[0], 1) for f in texts):
        glab = dict((f[0], f[1]) for f in fields)[group]
        money = [f for f in picked if f[2] == 'money']
        agg = OrderedDict()
        for r in sorted(rows, key=lambda r: str(r[group])):
            a = agg.setdefault(r[group], {f[0]: S.ZERO for f in money} | {'_n': 0})
            a['_n'] += 1
            for f in money:
                a[f[0]] += r[f[0]] or S.ZERO
        t = Table(f'Grouped by {glab}', [Col(glab), Col('Records', 'int')] + [Col(f[1], 'money') for f in money])
        for k, a in agg.items():
            t.add([k or '(blank)', a['_n']] + [a[f[0]] for f in money])
        rep.filters.append(('Group by', glab))
    else:
        t = Table(title, [Col(f[1], f[2]) for f in picked], paginate=True)
        for r in rows:
            t.add([r[f[0]] for f in picked])
    if t.cols[0].kind == 'money':
        t.cols.insert(0, Col('#'))
        t.rows = [[i + 1] + r for i, r in enumerate(t.rows)]
    t.sum_total(start=1)
    rep.tables.append(t)
    rep.filters.append(('Dataset', title))
    return rep, ds, fields, [f[0] for f in picked], group, texts
