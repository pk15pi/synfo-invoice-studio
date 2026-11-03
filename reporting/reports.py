"""Every report builder. Each returns a core.Report built only from services.* figures."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Max
from django.urls import reverse

from employee_management.models import Department, Employee, PayrollPeriod
from expense_management.models import CompanyExpense, CompanyExpensePayment, EmployeeExpenseClaim as C
from expense_management.models import ExpenseCategory, Vendor
from invoice_app.models import Client, ProjectTemplate

from . import hr_reports as HR
from . import ledger_reports as LR
from . import periods as P
from . import prior as PR
from . import services as S
from .core import NT, Col, Report, Table, inr

Z = S.ZERO
M, I, T, DT, PC = 'money', 'int', 'text', 'date', 'pct'


def pct(a, b):
    """a as % of b; 'n/a' when b is zero."""
    a, b = Decimal(a or 0), Decimal(b or 0)
    return (a / b * 100).quantize(Decimal('0.01')) if b else 'n/a'


def iparam(params, k):
    v = str(params.get(k) or '').strip()
    return int(v) if v.isdigit() else None


def link(key, period, **extra):
    q = dict(period.params)
    q.update({k: v for k, v in extra.items() if v not in (None, '')})
    return reverse('reporting:report', args=[key]) + '?' + urlencode(q)


def as_at(period):
    return min(period.end, P.today())


def mname(y, m):
    return date(y, m, 1).strftime('%b %Y')


def growth(cur, prev):
    return P.pct_change(cur, prev)[1]


def names(model, pk, attr='name'):
    if not pk:
        return ''
    o = model.objects.filter(pk=pk).first()
    return getattr(o, attr, '') if o else ''


def dept_name(p):
    return names(Department, iparam(p, 'department'))


# ============================================================ REVENUE
def rev_summary(p, period):
    r = Report('rev_summary', 'Revenue Summary', 'Revenue', S.BASIS['revenue'], period)
    inv = list(S.invoices(period.start, period.end).values('client__name', 'taxable_value', 'client_id'))
    tx = S.invoice_tax_totals(period.start, period.end)
    tot = tx['taxable']
    prev = period.previous()
    ptot, _ = S.invoice_revenue(prev.start, prev.end)
    projects = S.invoice_lines(period.start, period.end).values('project_id', 'project_name').distinct().count()
    received = S.invoice_receipts(period.start, period.end)
    r.card('Total Revenue (taxable, excl. GST)', tot, prev=ptot)
    r.card('Total Tax (GST)', tx['tax'])
    r.card('Grand Total Billed', tx['grand'])
    r.card('Total Amount Received', received, prev=S.invoice_receipts(prev.start, prev.end),
           note='By receipt date (cash basis)')
    r.card('Total Outstanding Amount', S.receivables_outstanding(as_at(period)), note=f'As at {as_at(period):%d %b %Y}')
    r.card('Number of Invoices', len(inv), 'int')
    r.card('Average Invoice Value', S.q2(tot / len(inv)) if inv else Z)
    r.card('Highest Invoice Value', max((S.q2(i['taxable_value']) for i in inv), default=Z))
    r.card('Lowest Invoice Value', min((S.q2(i['taxable_value']) for i in inv), default=Z))
    r.card('Number of Customers Billed', len({i['client_id'] for i in inv}), 'int')
    r.card('Number of Projects Billed', projects, 'int')
    tt = Table('Revenue, tax and grand total', [Col('Line'), Col('Amount (INR)', M)],
               rows=[['Taxable value (revenue)', tx['taxable']], ['CGST', tx['cgst']], ['SGST', tx['sgst']],
                     ['IGST', tx['igst']], ['Total tax', tx['tax']], ['Grand total (taxable + tax)', tx['grand']]],
               styles=['', '', '', '', 'subtotal', 'subtotal'])
    r.tables.append(tt)
    t = Table('Revenue by customer', [Col('Customer'), Col('Invoices', I), Col('Revenue (INR)', M), Col('% of Revenue', PC)])
    agg = defaultdict(lambda: [0, Z])
    for i in inv:
        agg[(i['client_id'], i['client__name'])][0] += 1
        agg[(i['client_id'], i['client__name'])][1] += S.q2(i['taxable_value'])
    for (cid, n), (c, v) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        t.add([(n, link('rev_customer', period, client=cid)), c, v, pct(v, tot)])
    t.sum_total()
    r.tables.append(t)
    cx = list(S.cancelled_invoices(period.start, period.end))
    if cx:
        ct = Table('Cancelled invoices (excluded from revenue and receivables)',
                   [Col('Invoice'), Col('Customer'), Col('Invoice Date', DT), Col('Cancelled On', DT),
                    Col('Grand Total (INR)', M)])
        for i in cx:
            ct.add([i.display_number, i.client.name, i.invoice_date, i.cancelled_date, S.q2(i.grand_total)])
        ct.sum_total()
        r.tables.append(ct)
    r.notes = ['Revenue = taxable value excluding GST. Receipts never add revenue.']
    return r


def rev_monthly(p, period):
    r = Report('rev_monthly', 'Monthly Revenue Report', 'Revenue', S.BASIS['revenue'], period)
    data = S.monthly_revenue(period.start, period.end)
    mm = S.monthly_matrix(period.start, period.end)
    b = period.months()[0]
    py, pm = P.add_months(b[0], b[1], -1)
    prev_val, _ = S.invoice_revenue(date(py, pm, 1), P.month_end(py, pm))
    t = Table('Revenue by month', [Col('Month'), Col('Invoices', I), Col('Revenue (INR)', M),
                                   Col('Amount Received (by receipt date)', M), Col('Growth vs previous month')])
    for y, m in period.months():
        n, v = data.get((y, m), (0, Z))
        t.add([mname(y, m), n, S.q2(v), mm.get((y, m), {}).get('received', Z), growth(v, prev_val)])
        prev_val = v
    t.sum_total()
    t.note = 'Revenue by Invoice Date (taxable value); Amount Received by Receipt Date - different bases.'
    r.tables.append(t)
    return r


def _register_table(period, client=None):
    t = Table('Invoice register', [Col('Invoice Number'), Col('Customer'), Col('Invoice Date', DT), Col('Due Date', DT),
              Col('Billing Month'), Col('Project'), Col('Taxable (INR)', M), Col('Tax (INR)', M),
              Col('Grand Total (INR)', M), Col('Amount Received', M), Col('TDS / Charges', M), Col('Outstanding', M),
              Col('Payment Status'), Col('Days Past Due')], paginate=True)
    a = as_at(period)
    rows = S.invoice_balances(a, client, start=period.start, end=period.end)
    projs = defaultdict(list)
    for iid, n in S.invoice_lines(period.start, period.end, client).values_list('invoice_id', 'project_name'):
        if n and n not in projs[iid]:
            projs[iid].append(n)
    for x in rows:
        i = x['inv']
        t.add([(i.display_number, reverse('invoice_detail', args=[i.pk])), i.client.name, i.invoice_date, i.due_date,
               i.billing_month or '', ', '.join(projs[i.pk]), S.q2(i.taxable_value), S.q2(i.total_tax), x['grand'],
               x['received'], x['deductions'], x['outstanding'], x['status'], x['days']])
    t.sum_total()
    t.total[13] = ''
    t.note = (f'Payments counted up to {a:%d %b %Y}. Outstanding = grand total - received - TDS/charges. '
              'Cancelled invoices are excluded.')
    return t


def _project_table(p, period, client=None):
    lines = S.invoice_lines(period.start, period.end, client or iparam(p, 'client'), iparam(p, 'project'),
                            (p.get('consultant') or '').strip())
    agg = {}
    for ln in lines.values('project_id', 'project_name', 'project__name', 'invoice__client__name', 'quantity_days',
                           'amount', 'invoice__billing_month', 'invoice__invoice_date', 'invoice_id'):
        k = (ln['invoice__client__name'], ln['project_id'] or ln['project_name'])
        a = agg.setdefault(k, dict(client=ln['invoice__client__name'], name=ln['project__name'] or ln['project_name'],
                                   pname=ln['project_name'], days=Z, rev=Z, months=set(), invs=set(), last=None,
                                   pid=ln['project_id']))
        a['days'] += S.q2(ln['quantity_days'])
        a['rev'] += S.q2(ln['amount'])
        a['months'].add(ln['invoice__billing_month'] or ln['invoice__invoice_date'].strftime('%Y-%m'))
        a['invs'].add(ln['invoice_id'])
        d = ln['invoice__invoice_date']
        a['last'] = max(a['last'] or d, d)
    t = Table('Project-wise revenue (invoice lines)', [Col('Client'), Col('Project'), Col('Consultant'),
              Col('Billing Months', I), Col('Total Days', M), Col('Total Revenue (INR)', M), Col('Average Daily Rate', M),
              Col('Invoices', I), Col('Last Billing Date', DT)])
    for a in sorted(agg.values(), key=lambda a: -a['rev']):
        name = (a['name'], link('project_profit', period, project=a['pid'])) if a['pid'] else a['name']
        t.add([a['client'], name, S.consultant_of(a['pname']), len(a['months']), a['days'], S.q2(a['rev']),
               S.q2(a['rev'] / a['days']) if a['days'] else Z, len(a['invs']), a['last']])
    t.sum_total()
    t.total[6] = ''
    t.total[7] = ''
    return t


def rev_customer(p, period):
    cid = iparam(p, 'client')
    r = Report('rev_customer', 'Customer-wise Revenue Report', 'Revenue', S.BASIS['revenue'], period, landscape=True)
    if cid:
        r.filters.append(('Customer', names(Client, cid)))
        r.tables.append(_register_table(period, client=cid))
        mt = Table('Monthly revenue for this customer', [Col('Month'), Col('Invoices', I), Col('Revenue (INR)', M)])
        agg = defaultdict(lambda: [0, Z])
        for d, tot in S.invoices(period.start, period.end, cid).values_list('invoice_date', 'taxable_value'):
            agg[(d.year, d.month)][0] += 1
            agg[(d.year, d.month)][1] += S.q2(tot)
        for (y, m), (n, v) in sorted(agg.items()):
            mt.add([mname(y, m), n, v])
        mt.sum_total()
        r.tables.append(mt)
        r.tables.append(_project_table(p, period, client=cid))
        return r
    t = Table('Revenue by customer', [Col('Customer'), Col('Invoices', I), Col('Total Revenue (INR)', M),
                                      Col('Amount Received', M), Col('Outstanding', M), Col('Last Invoice Date', DT),
                                      Col('Average Invoice Value', M)])
    agg = defaultdict(lambda: [0, Z, None, Z, Z])
    for x in S.invoice_balances(as_at(period), start=period.start, end=period.end):
        i = x['inv']
        a = agg[(i.client_id, i.client.name)]
        a[0] += 1
        a[1] += S.q2(i.taxable_value)
        a[2] = max(a[2] or i.invoice_date, i.invoice_date)
        a[3] += x['received']
        a[4] += x['outstanding']
    for (c, n), (cnt, v, last, rec, out) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        t.add([(n, link('rev_customer', period, client=c)), cnt, v, rec, out, last, S.q2(v / cnt)])
    t.sum_total()
    t.total[6] = ''
    t.note = ('Click a customer to drill down. Revenue = taxable value; Received / Outstanding are for the invoices '
              'of this period, payments up to the period end.')
    r.tables.append(t)
    return r


def rev_project(p, period):
    r = Report('rev_project', 'Project-wise Revenue Report', 'Revenue', S.BASIS['revenue'] + ' (invoice line amounts)',
               period, landscape=True)
    for k, mdl in (('client', Client), ('project', ProjectTemplate)):
        if iparam(p, k):
            r.filters.append((k.title(), names(mdl, iparam(p, k))))
    if p.get('consultant'):
        r.filters.append(('Consultant', p.get('consultant')))
    t = _project_table(p, period)
    r.tables.append(t)
    if not iparam(p, 'project') and not p.get('consultant'):
        inv_total, _ = S.invoice_revenue(period.start, period.end, iparam(p, 'client'))
        diff = S.q2(inv_total - t.total[5])
        r.notes.append(f'Reconciliation: invoice totals {S.q2(inv_total)} vs line amounts {t.total[5]}; difference '
                       f'{diff} (invoice rounding rule).')
    return r


def invoice_register(p, period):
    r = Report('invoice_register', 'Invoice Register', 'Revenue', S.BASIS['revenue'], period, landscape=True)
    if iparam(p, 'client'):
        r.filters.append(('Customer', names(Client, iparam(p, 'client'))))
    r.tables.append(_register_table(period, iparam(p, 'client')))
    return r


def receivables(p, period):
    a = as_at(period)
    cid = iparam(p, 'client')
    r = Report('receivables', 'Outstanding Receivables Report', 'Revenue', S.BASIS['asat'], period, landscape=True)
    if cid:
        r.filters.append(('Customer', names(Client, cid)))
    rows = S.invoice_balances(a, cid)
    open_ = [x for x in rows if x['outstanding'] > 0]
    credits = [x for x in rows if x['outstanding'] < 0]
    r.card('Total Outstanding Receivables', S.dsum(x['outstanding'] for x in open_), note=f'As at {a:%d %b %Y}')
    r.card('Open Invoices', len(open_), 'int')
    r.card('Customer Credits (overpaid)', S.dsum(-x['outstanding'] for x in credits))
    r.statement = ('Outstanding = invoice grand total - amount received - TDS/bank charges written off, counting '
                   f'receipts dated on/before {a:%d %b %Y}. Ageing from Due Date (Invoice Date when no due date). '
                   'Cancelled invoices are excluded.')
    sm = Table('Ageing summary', [Col('Age Bucket'), Col('Invoices', I), Col('Outstanding (INR)', M)])
    for b in S.AGE_BUCKETS:
        rs = [x for x in open_ if x['bucket'] == b]
        sm.add([b, len(rs), S.dsum(x['outstanding'] for x in rs)])
    sm.sum_total()
    buckets = defaultdict(lambda: defaultdict(lambda: Z))
    t = Table(f'Open invoices as at {a:%d %b %Y}',
              [Col('Customer'), Col('Invoice Number'), Col('Invoice Date', DT), Col('Due Date', DT),
               Col('Grand Total (INR)', M), Col('Amount Received', M), Col('TDS / Charges', M), Col('Outstanding', M),
               Col('Status'), Col('Days Past Due', I), Col('Age Bucket')], paginate=True)
    for x in open_:
        i = x['inv']
        t.add([i.client.name, (i.display_number, reverse('invoice_detail', args=[i.pk])), i.invoice_date, i.due_date,
               x['grand'], x['received'], x['deductions'], x['outstanding'], x['status'], x['days'], x['bucket']])
        buckets[i.client.name][x['bucket']] += x['outstanding']
    t.sum_total()
    t.total[9] = ''
    s2 = Table('Customer-wise outstanding by age bucket', [Col('Customer')] +
               [Col(b, M) for b in S.AGE_BUCKETS] + [Col('Total Outstanding', M)])
    for c, bb in sorted(buckets.items()):
        s2.add([c] + [bb[b] for b in S.AGE_BUCKETS] + [sum(bb.values(), Z)])
    s2.sum_total()
    r.tables += [sm, s2, t]
    if credits:
        ct = Table('Overpaid invoices (customer credits)', [Col('Customer'), Col('Invoice'), Col('Credit (INR)', M)])
        for x in credits:
            ct.add([x['inv'].client.name, x['inv'].display_number, -x['outstanding']])
        ct.sum_total()
        r.tables.append(ct)
    return r


def receipts_register(p, period):
    cid = iparam(p, 'client')
    r = Report('receipts_register', 'Receipts Register', 'Revenue', S.BASIS['receipts'], period, landscape=True)
    if cid:
        r.filters.append(('Customer', names(Client, cid)))
    t = Table('Customer receipts', [Col('Receipt Date', DT), Col('Invoice'), Col('Customer'), Col('Mode'),
              Col('Reference / UTR'), Col('Amount Received (INR)', M), Col('TDS / Charges (INR)', M)], paginate=True)
    for x in S.payments(period.start, period.end, cid):
        t.add([x.receipt_date, (x.invoice.display_number, reverse('invoice_detail', args=[x.invoice_id])),
               x.invoice.client.name, x.get_mode_display(), x.reference, S.q2(x.amount), S.q2(x.deductions)])
    t.sum_total()
    r.tables.append(t)
    r.card('Amount Received', S.invoice_receipts(period.start, period.end, cid))
    r.notes = ['Cash basis: every recorded receipt by Receipt Date. TDS / charges are written off, not cash.']
    return r


# ============================================================ EMPLOYEES
def emp_master(p, period):
    r = Report('emp_master', 'Employee Master Report', 'Employees', 'Current employee records (period not applied)',
               period, landscape=True)
    f = dict(department=iparam(p, 'department'), designation=p.get('designation', ''), status=p.get('status', ''))
    cur = S.current_structure_map(P.today())
    t = Table('Employees', [Col('Employee ID'), Col('Employee Name'), Col('Department'), Col('Designation'),
              Col('Employment Type'), Col('Date of Joining', DT), Col('Date of Leaving', DT), Col('Status'),
              Col('Salary Structure'), Col('Current Monthly Salary (INR)', M), Col('Work Location')], paginate=True)
    for e in S.employees(**f):
        s = cur.get(e.pk)
        t.add([e.employee_id, (e.full_name, link('emp_earnings', period, employee=e.pk)),
               e.department.name if e.department else '', e.designation, e.get_employment_type_display(),
               e.date_of_joining, e.date_of_leaving, e.get_status_display(),
               s.get_structure_type_display() if s else 'None', S.salary_of(s) if s else Z, e.work_location])
    t.total = [f'{len(t.rows)} employees'] + [''] * 10
    t.note = 'Aadhaar, PAN, passport and bank details are intentionally excluded.'
    r.tables.append(t)
    return r


def emp_earnings(p, period):
    eid = iparam(p, 'employee')
    r = Report('emp_earnings', 'Employee Earnings Report', 'Employees', S.BASIS['payroll'], period, landscape=True)
    qs = S.payrolls(period.start, period.end, eid, dept_name(p))
    comp = S.payroll_components(qs)
    if eid:
        e = Employee.objects.filter(pk=eid).first()
        r.filters.append(('Employee', f'{e.employee_id} {e.full_name}' if e else eid))
        t = Table('Monthly earnings', [Col('Month'), Col('Basic', M), Col('HRA', M), Col('Allowances', M),
                  Col('Bonus / Incentive', M), Col('Overtime', M), Col('Other Earnings', M), Col('Gross', M),
                  Col('Deductions', M), Col('Net Salary', M), Col('Employer Contribution', M)])
        for x in qs:
            c = comp[x.pk]
            t.add([x.period.label, c['basic'], c['hra'], c['allowances'], c['bonus'], c['overtime'], c['other'],
                   S.q2(x.gross_earnings), S.q2(x.total_deductions), S.q2(x.net_salary), S.q2(x.employer_contributions)])
        t.sum_total()
        r.tables.append(t)
        allq = S.payrolls(date(2000, 1, 1), date(2100, 12, 31), eid)
        buckets = defaultdict(lambda: [Z, Z, Z])
        for y, m, g, d, n in allq.values_list('period__year', 'period__month', 'gross_earnings', 'total_deductions',
                                               'net_salary'):
            dd = date(y, m, 1)
            fy = P.fy_label(P.fy_start_year(dd))
            for k in (f'{fy} {P.fy_quarter_name(dd)}', f"{fy} {'H1' if 4 <= m <= 9 else 'H2'}", fy, 'Lifetime'):
                buckets[k][0] += S.q2(g)
                buckets[k][1] += S.q2(d)
                buckets[k][2] += S.q2(n)
        s = Table('Quarterly / half-yearly / FY / lifetime earnings (all finalized payroll)',
                  [Col('Period'), Col('Gross', M), Col('Deductions', M), Col('Net', M)])
        for k in sorted(buckets, key=lambda k: (k == 'Lifetime', k)):
            s.add([k] + buckets[k])
        r.tables.append(s)
        return r
    agg = {}
    for x in qs:
        a = agg.setdefault(x.employee_id, dict(code=x.snap_employee_code, name=x.snap_name, dept=x.snap_department,
                                               desig=x.snap_designation, n=0, v=defaultdict(lambda: Z)))
        a['n'] += 1
        c = comp[x.pk]
        for k in ('basic', 'hra', 'allowances', 'bonus', 'overtime', 'other'):
            a['v'][k] += c[k]
        a['v']['gross'] += S.q2(x.gross_earnings)
        a['v']['ded'] += S.q2(x.total_deductions)
        a['v']['net'] += S.q2(x.net_salary)
        a['v']['er'] += S.q2(x.employer_contributions)
    t = Table('Earnings by employee', [Col('Employee ID'), Col('Employee Name'), Col('Department'), Col('Designation'),
              Col('Months Paid', I), Col('Basic', M), Col('Allowances (incl. HRA)', M), Col('Bonus / Incentive', M),
              Col('Other Earnings', M), Col('Gross Earnings', M), Col('Total Deductions', M), Col('Net Salary', M),
              Col('Employer Contribution', M), Col('Total Employee Cost', M)], paginate=True)
    for eid_, a in sorted(agg.items(), key=lambda kv: kv[1]['code']):
        v = a['v']
        t.add([a['code'], (a['name'], link('emp_earnings', period, employee=eid_)), a['dept'], a['desig'], a['n'],
               v['basic'], v['hra'] + v['allowances'], v['bonus'], v['overtime'] + v['other'], v['gross'], v['ded'],
               v['net'], v['er'], v['gross'] + v['er']])
    t.sum_total()
    t.note = 'Finalized payroll only. Click an employee for monthly, quarterly, half-yearly, FY and lifetime totals.'
    r.tables.append(t)
    return r


def emp_department(p, period):
    r = Report('emp_department', 'Department-wise Employee Report', 'Employees', S.BASIS['mixed'], period, landscape=True)
    agg = defaultdict(lambda: dict(emps=set(), gross=Z, ded=Z, er=Z, net=Z, claims=Z))
    for e, d, g, de, er, n in S.payrolls(period.start, period.end).values_list(
            'employee_id', 'snap_department', 'gross_earnings', 'total_deductions', 'employer_contributions', 'net_salary'):
        a = agg[d or 'Unassigned']
        a['emps'].add(e)
        a['gross'] += S.q2(g)
        a['ded'] += S.q2(de)
        a['er'] += S.q2(er)
        a['net'] += S.q2(n)
    for c in S.claims(period.start, period.end, C.APPROVED_STATES):
        agg[c['dept']]['claims'] += c['approved_inr']
    t = Table('Department summary', [Col('Department'), Col('Employees Paid', I), Col('Gross Salary', M),
              Col('Total Deductions', M), Col('Employer Contributions', M), Col('Net Salary', M),
              Col('Employee Reimbursements', M), Col('Total Employee Cost', M), Col('Average Cost per Employee', M)])
    for d, a in sorted(agg.items()):
        cost = a['gross'] + a['er'] + a['claims']
        t.add([d, len(a['emps']), a['gross'], a['ded'], a['er'], a['net'], a['claims'], cost,
               S.q2(cost / len(a['emps'])) if a['emps'] else Z])
    t.sum_total()
    t.total[8] = ''
    t.note = 'Payroll by snapshot department of each payroll row; reimbursements by claim department.'
    r.tables.append(t)
    return r


def _active_on(d):
    return Employee.objects.filter(date_of_joining__lte=d).exclude(date_of_leaving__lt=d)


def emp_joining(p, period):
    r = Report('emp_joining', 'Employee Joining and Leaving Report', 'Employees', 'Date of Joining / Date of Leaving',
               period)
    s, e = period.start, period.end
    joined = list(Employee.objects.filter(date_of_joining__range=(s, e)).select_related('department'))
    left = list(Employee.objects.filter(date_of_leaving__range=(s, e)).select_related('department'))
    at_start, at_end = _active_on(s).count(), _active_on(e).count()
    avg = Decimal(at_start + at_end) / 2
    r.card('Employees Joined', len(joined), 'int')
    r.card('Employees Left', len(left), 'int')
    r.card('Active at Start', at_start, 'int')
    r.card('Active at End', at_end, 'int')
    r.card('Attrition %', pct(len(left), avg), 'pct', note='Left / average of start and end headcount')
    for title, rows, f in (('Joined', joined, 'date_of_joining'), ('Left', left, 'date_of_leaving')):
        t = Table(f'Employees {title.lower()}', [Col('Employee ID'), Col('Name'), Col('Department'), Col('Designation'),
                                               Col(f'Date {title}', DT), Col('Status')])
        for x in rows:
            t.add([x.employee_id, x.full_name, x.department.name if x.department else '', x.designation, getattr(x, f),
                   x.get_status_display()])
        r.tables.append(t)
    dt = Table('Department-wise movement', [Col('Department'), Col('Active at End', I), Col('Joined', I), Col('Left', I)])
    d = defaultdict(lambda: [0, 0, 0])
    for i, rows in enumerate((_active_on(e).select_related('department'), joined, left)):
        for x in rows:
            d[x.department.name if x.department else 'Unassigned'][i] += 1
    for k, v in sorted(d.items()):
        dt.add([k] + v)
    dt.sum_total()
    r.tables.append(dt)
    return r


def salary_history(p, period):
    eid = iparam(p, 'employee')
    r = Report('salary_history', 'Employee Salary History Report', 'Employees', 'Salary structure effective dates',
               period, landscape=True)
    t = Table('Salary structures', [Col('Employee ID'), Col('Employee Name'), Col('Effective From', DT),
              Col('Effective To', DT), Col('Structure Type'), Col('Rate', M), Col('Monthly Salary (INR)', M),
              Col('Change (INR)', M), Col('Increment %', PC)], paginate=True)
    prev = {}
    for s in S.structures(eid):
        cur = S.salary_of(s)
        old = prev.get(s.employee_id)
        diff = S.q2(cur - old) if old is not None else ''
        t.add([s.employee.employee_id, (s.employee.full_name, link('salary_history', period, employee=s.employee_id)),
               s.effective_from, s.effective_to, s.get_structure_type_display(), S.q2(s.rate), cur, diff,
               pct(diff, old) if old else ''])
        prev[s.employee_id] = cur
    t.note = 'Monthly salary = total earnings of the structure.'
    r.tables.append(t)
    if eid:
        r.filters.append(('Employee', names(Employee, eid, 'full_name')))
        h = Table('Payroll history (finalized)', [Col('Month'), Col('Gross', M), Col('Deductions', M), Col('Net', M),
                                                   Col('Employer Contribution', M)])
        for x in S.payrolls(date(2000, 1, 1), date(2100, 12, 31), eid):
            h.add([x.period.label, S.q2(x.gross_earnings), S.q2(x.total_deductions), S.q2(x.net_salary),
                   S.q2(x.employer_contributions)])
        h.sum_total()
        r.tables.append(h)
    return r


def emp_cost(p, period):
    r = Report('emp_cost', 'Employee Cost Report', 'Employees', S.BASIS['mixed'], period, landscape=True)
    pq = S.payrolls(period.start, period.end)
    comp = S.payroll_components(pq)
    agg = {}

    def row(eid, code='', name='', dept=''):
        return agg.setdefault(eid, dict(code=code, name=name, dept=dept, v=defaultdict(lambda: Z)))
    for x in pq:
        a = row(x.employee_id, x.snap_employee_code, x.snap_name, x.snap_department)
        c = comp[x.pk]
        a['v']['gross'] += S.q2(x.gross_earnings)
        a['v']['pf'] += c['er_pf']
        a['v']['esi'] += c['er_esi']
        a['v']['oth'] += S.q2(x.employer_contributions) - c['er_pf'] - c['er_esi']
    for c in S.claims(period.start, period.end, C.APPROVED_STATES):
        row(c['employee_id'], c['snap_employee_code'], c['snap_name'], c['dept'])['v']['claims'] += c['approved_inr']
    for x in S.employee_related_other(period.start, period.end):
        row(x['employee_id'], x['employee__employee_id'], x['employee_name'],
            x['department__name'] or '')['v']['other'] += x['total_inr']
    t = Table('Employee cost', [Col('Employee ID'), Col('Employee Name'), Col('Department'), Col('Gross Salary', M),
              Col('Employer PF', M), Col('Employer ESI', M), Col('Other Employer Contributions', M),
              Col('Employee Reimbursements', M), Col('Other Employee-Related Expenses', M), Col('Total Employee Cost', M)],
              paginate=True)
    for a in sorted(agg.values(), key=lambda a: a['code'] or ''):
        v = a['v']
        t.add([a['code'], a['name'], a['dept'], v['gross'], v['pf'], v['esi'], v['oth'], v['claims'], v['other'],
               v['gross'] + v['pf'] + v['esi'] + v['oth'] + v['claims'] + v['other']])
    tot = t.sum_total()
    for lab, i in (('Total Gross Salary', 3), ('Total Employer PF', 4), ('Total Employer ESI', 5),
                   ('Total Reimbursements', 7), ('Other Employee-Related Expenses', 8), ('Total Employee Cost', 9)):
        r.card(lab, tot[i])
    t.note = ('Employee deductions are part of gross salary and are not added again. "Other employee-related" = company '
              'expenses linked to the employee (also part of company expenses - do not add the two reports together).')
    r.tables.append(t)
    return r


# ============================================================ PAYROLL
def payroll_summary(p, period):
    r = Report('payroll_summary', 'Monthly Payroll Summary', 'Payroll', S.BASIS['payroll'], period, landscape=True)
    qs = S.payrolls(period.start, period.end)
    split = S.employer_split(qs)
    agg = defaultdict(lambda: dict(emps=set(), g=Z, d=Z, er=Z, n=Z))
    for y, m, e, g, d, er, n in qs.values_list('period__year', 'period__month', 'employee_id', 'gross_earnings',
                                               'total_deductions', 'employer_contributions', 'net_salary'):
        a = agg[(y, m)]
        a['emps'].add(e)
        a['g'] += S.q2(g)
        a['d'] += S.q2(d)
        a['er'] += S.q2(er)
        a['n'] += S.q2(n)
    status = {(x.year, x.month): x.get_status_display() for x in PayrollPeriod.objects.all()}
    t = Table('Payroll by month', [Col('Payroll Month'), Col('Period Status'), Col('Employees Paid', I),
              Col('Gross Salary', M), Col('Employee Deductions', M), Col('Employer Contributions', M),
              Col('Net Salary', M), Col('Total Company Payroll Cost', M)])
    for y, m in period.months():
        if (y, m) not in agg and (y, m) not in status:
            continue
        a = agg[(y, m)]
        t.add([mname(y, m), status.get((y, m), '-'), len(a['emps']), a['g'], a['d'], a['er'], a['n'], a['g'] + a['er']])
    tt = t.sum_total()
    r.card('Gross Salary', tt[3])
    r.card('Employee Deductions', tt[4])
    r.card('Employer Contributions', tt[5], note=f"PF {S.q2(split['pf'])} | ESI {S.q2(split['esi'])}")
    r.card('Net Salary', tt[6])
    r.card('Total Company Payroll Cost', tt[7])
    t.note = 'Only finalized (current revision) payroll rows are counted; a draft month shows 0.'
    r.tables.append(t)
    return r


def payroll_register(p, period):
    r = Report('payroll_register', 'Payroll Register', 'Payroll', S.BASIS['payroll'], period, landscape=True)
    dep = dept_name(p)
    if dep:
        r.filters.append(('Department', dep))
    qs = S.payrolls(period.start, period.end, iparam(p, 'employee'), dep)
    comp = S.payroll_components(qs)
    t = Table('Payroll register', [Col('Emp ID'), Col('Name'), Col('Department'), Col('Designation'), Col('Month'),
              Col('Working Days', M), Col('Payable Days', M), Col('LWP Days', M), Col('Basic', M), Col('HRA', M),
              Col('Other Allowances', M), Col('Bonus / Incentive', M), Col('Overtime', M), Col('Other Earnings', M),
              Col('Gross', M), Col('PF', M), Col('ESI', M), Col('TDS', M), Col('Prof. Tax', M), Col('Other Ded.', M),
              Col('Total Ded.', M), Col('Rounding', M), Col('Net Salary', M), Col('Employer Contribution', M)],
              paginate=True)
    for x in qs:
        c = comp[x.pk]
        t.add([x.snap_employee_code, x.snap_name, x.snap_department, x.snap_designation, x.period.label,
               S.q2(x.working_days), S.q2(x.payable_days), S.q2(x.lwp_days), c['basic'], c['hra'], c['allowances'],
               c['bonus'], c['overtime'], c['other'], S.q2(x.gross_earnings), c['pf'], c['esi'], c['tds'], c['pt'],
               c['other_ded'], S.q2(x.total_deductions), S.q2(x.rounding_adjustment), S.q2(x.net_salary),
               S.q2(x.employer_contributions)])
    t.sum_total()
    t.note = 'Net = Gross - Total deductions + Rounding. Snapshot values from the finalized payroll are shown.'
    r.tables.append(t)
    return r


def payroll_payment(p, period):
    r = Report('payroll_payment', 'Payroll Payment Report', 'Payroll', S.BASIS['payroll'], period, landscape=True)
    r.statement = ('Amount paid = recorded salary payments (all dates, across revisions) against each finalized '
                   'payroll. The expected payment date is the legacy bank-file date, not a confirmed payment.')
    paid = S.salary_paid_upto()
    t = Table('Net salary payable per payroll', [Col('Employee'), Col('Payroll Month'),
              Col('Net Salary Payable', M), Col('Expected Payment Date', DT), Col('Amount Paid', M),
              Col('Pending Amount', M), Col('Payment Status')], paginate=True)
    for x in S.payrolls(period.start, period.end):
        net, pd = S.q2(x.net_salary), paid[(x.period_id, x.employee_id)]
        st = 'Paid' if pd > 0 and pd >= net else 'Partially Paid' if pd > 0 else 'Unpaid'
        t.add([f'{x.snap_employee_code} {x.snap_name}', x.period.label, net,
               x.period.payment_date or x.period.end + timedelta(days=1), pd, S.q2(net - pd), st])
    t.sum_total()
    r.tables.append(t)
    pt = Table('Salary payments recorded in the period', [Col('Payment Date', DT), Col('Employee'), Col('Payroll Month'),
               Col('Bank'), Col('Mode'), Col('Reference'), Col('Amount (INR)', M)], paginate=True)
    for s in S.payroll_payments(period.start, period.end):
        pt.add([s.payment_date, f'{s.payroll.snap_employee_code} {s.payroll.snap_name}', s.payroll.period.label,
                str(s.bank_account or 'Default'), s.get_mode_display(), s.reference, S.q2(s.amount)])
    pt.sum_total()
    r.tables.append(pt)
    r.limitations = [S.LIMITATIONS[1]]
    return r


def payroll_department(p, period):
    r = Report('payroll_department', 'Department-wise Payroll Cost', 'Payroll', S.BASIS['payroll'], period, landscape=True)
    agg = defaultdict(lambda: dict(e=set(), g=Z, d=Z, er=Z, n=Z))
    for e, dep, g, d, er, n in S.payrolls(period.start, period.end).values_list(
            'employee_id', 'snap_department', 'gross_earnings', 'total_deductions', 'employer_contributions', 'net_salary'):
        a = agg[dep or 'Unassigned']
        a['e'].add(e)
        a['g'] += S.q2(g)
        a['d'] += S.q2(d)
        a['er'] += S.q2(er)
        a['n'] += S.q2(n)
    t = Table('Payroll cost by department', [Col('Department'), Col('Number of Employees', I), Col('Gross Salary', M),
              Col('Deductions', M), Col('Employer Contributions', M), Col('Net Salary', M), Col('Total Payroll Cost', M)])
    for d, a in sorted(agg.items()):
        t.add([d, len(a['e']), a['g'], a['d'], a['er'], a['n'], a['g'] + a['er']])
    t.sum_total()
    r.tables.append(t)
    return r


def increments(p, period):
    r = Report('increments', 'Salary Increment Report', 'Payroll', 'Effective date of the revised salary structure',
               period, landscape=True)
    t = Table('Salary changes', [Col('Employee ID'), Col('Employee Name'), Col('Department'), Col('Effective Date', DT),
              Col('Previous Structure'), Col('Previous Salary', M), Col('Revised Structure'), Col('Revised Salary', M),
              Col('Increase Amount', M), Col('Increase %', PC)])
    for c in S.salary_changes(period.start, period.end):
        e = c['employee']
        t.add([e.employee_id, e.full_name, e.department.name if e.department else '', c['effective'], c['old_type'],
               c['old'], c['new_type'], c['new'], c['diff'], c['pct'] if c['pct'] is not None else 'n/a'])
    t.sum_total()
    t.total[5] = t.total[7] = ''
    r.tables.append(t)
    return r


# ============================================================ EXPENSES
def _exp_filters(p, r=None):
    f = {k: iparam(p, k) for k in ('vendor', 'category', 'department', 'client', 'project', 'employee')}
    lab = dict(vendor=Vendor, category=ExpenseCategory, department=Department, client=Client, project=ProjectTemplate)
    if r is not None:
        for k, v in f.items():
            if v and k in lab:
                r.filters.append((k.title(), names(lab[k], v)))
            elif v and k == 'employee':
                r.filters.append(('Employee', names(Employee, v, 'full_name')))
    return {k: v for k, v in f.items() if v}


def exp_summary(p, period):
    r = Report('exp_summary', 'Expense Summary', 'Expenses', S.BASIS['company'] + '; ' + S.BASIS['claims'], period)
    s, e, a = period.start, period.end, as_at(period)
    comp = S.company_expenses(s, e, as_at=a)
    canc = [x for x in S.company_expenses(s, e, include_cancelled=True) if x['cancelled']]
    cl = S.claims(s, e, C.APPROVED_STATES, as_at=a)
    pr = period.previous()
    total_c = S.dsum(x['total_inr'] for x in comp)
    total_cl = S.dsum(x['approved_inr'] for x in cl)
    r.card('Company Expenses', total_c, prev=S.company_expense_total(pr.start, pr.end))
    r.card('Approved Employee Claims', total_cl, prev=S.reimbursement_expense(pr.start, pr.end))
    r.card('Total Expenses (company + claims)', total_c + total_cl)
    r.card('Vendor Payments Made (cash)', S.dsum(x['inr'] for x in S.vendor_payments(s, e)))
    r.card('Reimbursements Paid (cash)', S.dsum(x['inr'] for x in S.reimbursements(s, e)))
    r.card('Vendor Outstanding (period expenses)', S.dsum(x['outstanding_inr'] for x in comp), note=f'as at {a:%d %b %Y}')
    r.card('Reimbursements Outstanding', S.dsum(x['outstanding_inr'] for x in cl), note=f'as at {a:%d %b %Y}')
    r.card('Cancelled Expenses (excluded)', S.dsum(x['total_inr'] for x in canc), note=f'{len(canc)} records')
    t = Table('Expenses by group', [Col('Expense Group'), Col('Records', I), Col('Amount (INR)', M), Col('% of Total', PC)])
    g = defaultdict(lambda: [0, Z])
    for x in comp:
        g[x['group']][0] += 1
        g[x['group']][1] += x['total_inr']
    grand = total_c + total_cl
    for k in S.GROUPS:
        if k in g:
            t.add([k, g[k][0], g[k][1], pct(g[k][1], grand)])
    t.add(['Employee Reimbursements (approved claims)', len(cl), total_cl, pct(total_cl, grand)])
    t.sum_total()
    pp = [x for x in comp if x['is_prior_period']] + [x for x in cl if x['is_prior_period']]
    if pp:
        amt = S.dsum(x.get('total_inr', x.get('approved_inr')) for x in pp)
        t.add(['of which prior-period expenses', len(pp), amt, pct(amt, grand)], 'subtotal')
    t.note = S.GROUP_RULES_TEXT
    r.tables.append(t)
    return r


def company_expenses(p, period):
    r = Report('company_expenses', 'Company Expense Report', 'Expenses', S.BASIS['company'], period, landscape=True)
    f = _exp_filters(p, r)
    if p.get('status'):
        f['status'] = p['status']
        r.filters.append(('Payment status', p['status']))
    a = as_at(period)
    rows = S.company_expenses(period.start, period.end, include_cancelled=f.get('status') == 'cancelled', as_at=a, **f)
    t = Table('Company expenses', [Col('Expense ID'), Col('Expense Date', DT), Col('Vendor'), Col('Vendor Invoice No.'),
              Col('Category'), Col('Subcategory'), Col('Department'), Col('Client'), Col('Project'),
              Col('Amount (INR)', M), Col('Paid (INR)', M), Col('Outstanding (INR)', M), Col('Payment Status'),
              Col('Due Date', DT)], paginate=True)
    for x in rows:
        t.add([x['expense_id'], x['expense_date'], x['vendor__name'] or '', x['invoice_number'], x['category__name'],
               x['subcategory__name'] or '', x['department__name'] or '', x['client__name'] or '',
               x['project__name'] or '', x['total_inr'], x['paid_inr'], x['outstanding_inr'],
               CompanyExpense(payment_status=x['payment_status']).get_payment_status_display(), x['due_date']])
    if p.get('group_by') in ('category', 'vendor'):
        t.add_subtotals(4 if p['group_by'] == 'category' else 2)
    t.sum_total()
    t.note = (f'Paid / outstanding use payments dated on or before {a:%d %b %Y}. '
              'Cancelled expenses are excluded unless the Cancelled status is selected.')
    r.tables.append(t)
    return r


def employee_claims(p, period):
    r = Report('employee_claims', 'Employee Expense Claim Report', 'Expenses',
               'Claim Expense Date (all statuses listed; only approved states count as expense)', period, landscape=True)
    f = _exp_filters(p, r)
    st = p.get('status') or ''
    if st:
        r.filters.append(('Status', st))
    rows = S.claims(period.start, period.end, [st] if st else None, as_at=as_at(period), **f)
    t = Table('Claims', [Col('Claim ID'), Col('Employee'), Col('Department'), Col('Expense Date', DT), Col('Category'),
              Col('Amount Claimed (INR)', M), Col('Approved Amount (INR)', M), Col('Reimbursed (INR)', M),
              Col('Outstanding (INR)', M), Col('Status')], paginate=True)
    for x in rows:
        t.add([x['claim_id'], f"{x['snap_employee_code']} {x['snap_name']}", x['dept'], x['expense_date'],
               x['category__name'], x['claimed_inr'], x['approved_inr'], x['reimbursed_inr'], x['outstanding_inr'],
               C(status=x['status']).get_status_display()])
    t.sum_total()
    t.note = 'Approved amount counts only for approved, partially approved and reimbursed claims.'
    r.tables.append(t)
    return r


def exp_category(p, period):
    r = Report('exp_category', 'Category-wise Expense Report', 'Expenses', S.BASIS['company'] + '; ' + S.BASIS['claims'],
               period)
    agg = defaultdict(lambda: [Z, Z])
    for x in S.company_expenses(period.start, period.end):
        agg[(x['category__name'], x['subcategory__name'] or '')][0] += x['total_inr']
    for x in S.claims(period.start, period.end, C.APPROVED_STATES):
        agg[(x['category__name'], x['subcategory__name'] or '')][1] += x['approved_inr']
    grand = sum((a + b for a, b in agg.values()), Z)
    t = Table('Expenses by category', [Col('Category'), Col('Subcategory'), Col('Company Expenses', M),
              Col('Employee Claims (approved)', M), Col('Total', M), Col('% of Total', PC)])
    for (c, s), (a, b) in sorted(agg.items()):
        t.add([c, s, a, b, a + b, pct(a + b, grand)])
    t.add_subtotals(0)
    t.sum_total()
    r.tables.append(t)
    return r


def exp_vendor(p, period):
    r = Report('exp_vendor', 'Vendor-wise Expense Report', 'Expenses', S.BASIS['company'], period)
    a = as_at(period)
    agg = defaultdict(lambda: dict(n=0, t=Z, p=Z, o=Z))
    for x in S.company_expenses(period.start, period.end, as_at=a):
        v = agg[(x['vendor_id'], x['vendor__name'] or '(no vendor)')]
        v['n'] += 1
        v['t'] += x['total_inr']
        v['p'] += x['paid_inr']
        v['o'] += x['outstanding_inr']
    last = dict(CompanyExpensePayment.objects.filter(payment_date__lte=a).values('expense__vendor_id')
                .annotate(m=Max('payment_date')).values_list('expense__vendor_id', 'm'))
    t = Table('Expenses by vendor', [Col('Vendor'), Col('Expenses', I), Col('Total Expenses (INR)', M),
              Col('Amount Paid', M), Col('Outstanding', M), Col('Last Payment Date', DT)])
    for (vid, n), v in sorted(agg.items(), key=lambda kv: -kv[1]['t']):
        t.add([(n, link('company_expenses', period, vendor=vid)) if vid else n,
               v['n'], v['t'], v['p'], v['o'], last.get(vid)])
    t.sum_total()
    t.note = f'Paid / outstanding as at {a:%d %b %Y} for expenses dated in the period.'
    r.tables.append(t)
    return r


def exp_department(p, period):
    r = Report('exp_department', 'Department-wise Expense Report', 'Expenses', S.BASIS['mixed'], period)
    agg = defaultdict(lambda: [Z, Z, Z])
    for d, g, er in S.payrolls(period.start, period.end).values_list('snap_department', 'gross_earnings',
                                                                      'employer_contributions'):
        agg[d or 'Unassigned'][0] += S.q2(g) + S.q2(er)
    for x in S.claims(period.start, period.end, C.APPROVED_STATES):
        agg[x['dept']][1] += x['approved_inr']
    for x in S.company_expenses(period.start, period.end):
        agg[x['department__name'] or 'Unassigned'][2] += x['total_inr']
    t = Table('Expenses by department', [Col('Department'), Col('Payroll Cost (gross + employer)', M),
              Col('Employee Reimbursements', M), Col('Company Expenses', M), Col('Total Department Expense', M)])
    for d, (a, b, c) in sorted(agg.items()):
        t.add([d, a, b, c, a + b + c])
    t.sum_total()
    r.tables.append(t)
    return r


def _bucket_key(d, by):
    if by == 'quarter':
        return (P.fy_start_year(d), 'Q1Q2Q3Q4'.index(P.fy_quarter_name(d))), \
            f'{P.fy_quarter_name(d)} {P.fy_label(P.fy_start_year(d))}'
    if by == 'year':
        return (P.fy_start_year(d),), P.fy_label(P.fy_start_year(d))
    return (d.year, d.month), d.strftime('%b %Y')


TREND_COLS = ['Software and IT', 'Travel', 'Rent and Utilities', 'Professional Fees', 'Marketing',
              'Employee Reimbursements', 'Other Company Expenses']


def exp_trends(p, period):
    by = p.get('by') if p.get('by') in ('month', 'quarter', 'year') else 'month'
    r = Report('exp_trends', 'Expense Trend Report', 'Expenses', S.BASIS['company'] + '; ' + S.BASIS['claims'], period,
               landscape=True)
    r.filters.append(('Granularity', {'month': 'Monthly', 'quarter': 'Quarterly (FY)', 'year': 'Yearly (FY)'}[by]))
    agg, labels = defaultdict(lambda: defaultdict(lambda: Z)), {}
    for x in S.company_expenses(period.start, period.end):
        k, lab = _bucket_key(x['expense_date'], by)
        labels[k] = lab
        agg[k][S.TREND_GROUPS[x['group']]] += x['total_inr']
    for x in S.claims(period.start, period.end, C.APPROVED_STATES):
        k, lab = _bucket_key(x['expense_date'], by)
        labels[k] = lab
        agg[k]['Employee Reimbursements'] += x['approved_inr']
    t = Table(f'Expense trend by {by}', [Col(by.title())] + [Col(c, M) for c in TREND_COLS] + [Col('Total', M)])
    for k in sorted(agg):
        v = agg[k]
        t.add([labels[k]] + [v[c] for c in TREND_COLS] + [sum(v.values(), Z)])
    t.sum_total()
    r.tables.append(t)
    return r


# ============================================================ FINANCIAL
def pnl(p, period):
    r = Report('pnl', 'Management Profit and Loss', 'Financial', S.BASIS['mixed'], period)
    r.statement = 'Management Profit and Loss - not a statutory financial statement.'
    cur, pr = S.pnl(period.start, period.end), period.previous()
    old = S.pnl(pr.start, pr.end)
    t = Table('Profit and Loss', [Col('Particulars'), Col(f'{period.label} (INR)', M), Col(f'Previous: {pr.label}', M),
                                  Col('Change (INR)', M), Col('Change %')])

    def line(lab, k, sub=None, style=''):
        a = cur[k] if sub is None else cur[k][sub]
        b = old[k] if sub is None else old[k][sub]
        t.add([lab, a, b, a - b, growth(a, b)], style)
    line('Revenue (invoice taxable value, excl. GST)', 'revenue', style='subtotal')
    t.add(['Less: Expenses', '', '', '', ''], 'group')
    line('Salary (gross earnings)', 'gross')
    line('Employer contributions (PF / ESI / other)', 'employer')
    line('Employee reimbursements (approved claims)', 'claims')
    for g in S.GROUPS:
        line(g, 'groups', g)
    line('Total Expenses', 'total_expenses', style='subtotal')
    line('Net Profit / Loss', 'profit', style='subtotal')
    mc, mo = pct(cur['profit'], cur['revenue']), pct(old['profit'], old['revenue'])
    t.add(['Profit Margin', f'{mc}%' if not isinstance(mc, str) else mc, f'{mo}%' if not isinstance(mo, str) else mo,
           '', ''], 'subtotal')
    r.tables.append(t)
    r.notes = ['Employee deductions (PF, ESI, PT, TDS) are inside gross salary and are not added again.',
               'Revenue = invoice taxable value excluding GST (cancelled invoices excluded); GST is not revenue. '
               'Customer receipts are cash and never add revenue.', S.GROUP_RULES_TEXT]
    r.card('Revenue', cur['revenue'], prev=old['revenue'])
    r.card('Total Expenses', cur['total_expenses'], prev=old['total_expenses'])
    r.card('Net Profit / Loss', cur['profit'], prev=old['profit'])
    r.card('Profit Margin', mc, 'pct')
    return r


def rev_vs_exp(p, period):
    r = Report('rev_vs_exp', 'Revenue vs Expense Report', 'Financial', S.BASIS['mixed'], period, landscape=True)
    t = Table('Revenue vs expense by month', [Col('Month'), Col('Revenue', M), Col('Salary Expense', M),
              Col('Employee Reimbursements', M), Col('Company Expenses', M), Col('Total Expenses', M),
              Col('Profit / Loss', M), Col('Profit Margin %', PC)])
    for (y, m), v in S.monthly_matrix(period.start, period.end).items():
        t.add([mname(y, m), v['revenue'], v['salary'], v['claims'], v['company'], v['total_expenses'], v['profit'],
               pct(v['profit'], v['revenue'])])
    tot = t.sum_total()
    tot[7] = pct(tot[6], tot[1])
    t.note = 'Salary expense = gross salary + employer contributions.'
    r.tables.append(t)
    return r


def monthly_perf(p, period):
    r = Report('monthly_perf', 'Monthly Business Performance', 'Financial', S.BASIS['mixed'], period, landscape=True)
    t = Table('Monthly performance', [Col('Month'), Col('Invoices', I), Col('Revenue', M), Col('Amount Received', M),
              Col('Payroll Cost', M), Col('Reimbursements', M), Col('Company Expenses', M), Col('Total Expenses', M),
              Col('Profit / Loss', M), Col('Margin %', PC), Col('Recorded Cash Out', M)])
    for (y, m), v in S.monthly_matrix(period.start, period.end).items():
        t.add([mname(y, m), v['invoices'], v['revenue'], v['received'], v['salary'], v['claims'], v['company'],
               v['total_expenses'], v['profit'], pct(v['profit'], v['revenue']), v['cash_out']])
    tot = t.sum_total()
    tot[9] = pct(tot[8], tot[2])
    t.note = 'Recorded cash out = vendor payments + reimbursements + salary payments by payment date.'
    r.tables.append(t)
    return r


def fy_metrics(s, e):
    x = S.pnl(s, e)
    custs = S.invoices(s, e).values('client_id').distinct().count()
    emps = S.payrolls(s, e).values('employee_id').distinct().count()
    cf = S.cash_flow(s, e)
    a = min(e, P.today())
    out = S.dsum(v['outstanding_inr'] for v in S.vendor_payables(a)) + \
        S.dsum(v['outstanding_inr'] for v in S.reimbursements_payable(a))
    return [('Total Revenue', x['revenue']), ('Total Amount Received', cf['receipts']),
            ('Total Salary Expense (gross + employer)', x['gross'] + x['employer']),
            ('Total Employee Reimbursements', x['claims']), ('Total Company Expenses', x['company']),
            ('Total Expenses', x['total_expenses']), ('Net Profit / Loss', x['profit']),
            ('Profit Margin %', pct(x['profit'], x['revenue'])), ('Number of Invoices', x['invoices']),
            ('Number of Customers', custs), ('Number of Employees Paid', emps),
            ('Recorded Payments Made (vendor + reimbursements)', cf['outflow']),
            ('Outstanding Payables (vendor + reimbursements, as at FY end)', S.q2(out)),
            ('Outstanding Receivables (as at FY end)', S.receivables_outstanding(a))]


def fy_compare(p, period):
    cur = P.fy_start_year(P.today())
    a = int(p['fy_a']) if str(p.get('fy_a') or '').isdigit() else cur - 1
    b = int(p['fy_b']) if str(p.get('fy_b') or '').isdigit() else cur
    period = P.Period(P.fy_range(a)[0], P.fy_range(b)[1], f'{P.fy_label(a)} vs {P.fy_label(b)}', 'custom', 'days',
                      {'fy_a': a, 'fy_b': b})
    r = Report('fy_compare', 'Financial Year Comparison', 'Financial', S.BASIS['mixed'], period)
    r.filters.append(('Compared', f'{P.fy_label(a)} vs {P.fy_label(b)}'))
    t = Table('FY comparison', [Col('Metric'), Col(P.fy_label(a)), Col(P.fy_label(b)), Col('Difference'), Col('Change %')])
    for (lab, va), (_, vb) in zip(fy_metrics(*P.fy_range(a)), fy_metrics(*P.fy_range(b))):
        if va is None or vb is None:
            t.add([lab, NT, NT, NT, ''])
        elif isinstance(va, str) or isinstance(vb, str) or lab.endswith('%'):
            fa = va if isinstance(va, str) else f'{va}%'
            fb = vb if isinstance(vb, str) else f'{vb}%'
            t.add([lab, fa, fb, '', ''])
        elif isinstance(va, int):
            t.add([lab, str(va), str(vb), str(vb - va), growth(vb, va)])
        else:
            t.add([lab, inr(va), inr(vb), inr(vb - va), growth(vb, va)])
    r.tables.append(t)
    return r


def ratios(p, period):
    r = Report('ratios', 'Financial Ratios', 'Financial', S.BASIS['mixed'], period)
    x = S.pnl(period.start, period.end)
    custs = S.invoices(period.start, period.end).values('client_id').distinct().count()
    emps = S.payrolls(period.start, period.end).values('employee_id').distinct().count()
    emp_cost = x['gross'] + x['employer'] + x['claims']
    t = Table('Ratios', [Col('Ratio'), Col('Value'), Col('Formula')])
    for lab, v, f in [
            ('Profit Margin', pct(x['profit'], x['revenue']), 'Net profit / revenue'),
            ('Salary Cost as % of Revenue', pct(x['gross'] + x['employer'], x['revenue']),
             '(Gross + employer contributions) / revenue'),
            ('Expense Ratio', pct(x['total_expenses'], x['revenue']), 'Total expenses / revenue'),
            ('Reimbursement Ratio', pct(x['claims'], x['revenue']), 'Approved claims / revenue'),
            ('Average Revenue per Invoice', inr(x['revenue'] / x['invoices']) if x['invoices'] else 'n/a',
             'Revenue / invoices'),
            ('Average Revenue per Customer', inr(x['revenue'] / custs) if custs else 'n/a', 'Revenue / customers billed'),
            ('Average Cost per Employee', inr(emp_cost / emps) if emps else 'n/a',
             '(Gross + employer + approved claims) / employees paid'),
            ('Revenue per Employee', inr(x['revenue'] / emps) if emps else 'n/a', 'Revenue / employees paid'),
            ('Collection Efficiency', pct(S.invoice_receipts(period.start, period.end), S.invoice_tax_totals(
                period.start, period.end)['grand']), 'Receipts in period / grand total billed in period')]:
        t.add([lab, v if isinstance(v, str) else f'{v}%', f])
    r.tables.append(t)
    return r


def emp_cost_vs_rev(p, period):
    r = Report('emp_cost_vs_rev', 'Employee Cost vs Revenue', 'Financial', S.BASIS['mixed'], period, landscape=True)
    heads = defaultdict(set)
    for y, m, e in S.payrolls(period.start, period.end).values_list('period__year', 'period__month', 'employee_id'):
        heads[(y, m)].add(e)
    t = Table('Monthly', [Col('Month'), Col('Revenue', M), Col('Employee Cost', M), Col('Employee Cost as % of Revenue', PC),
              Col('Employees Paid', I), Col('Revenue per Employee', M), Col('Cost per Employee', M)])
    for k, v in S.monthly_matrix(period.start, period.end).items():
        n = len(heads[k])
        cost = v['salary'] + v['claims']
        t.add([mname(*k), v['revenue'], cost, pct(cost, v['revenue']), n, S.q2(v['revenue'] / n) if n else Z,
               S.q2(cost / n) if n else Z])
    tot = t.sum_total()
    tot[3] = pct(tot[2], tot[1])
    tot[4] = len({e for s in heads.values() for e in s})
    tot[5] = tot[6] = ''
    t.note = 'Employee cost = gross salary + employer contributions + approved reimbursements.'
    r.tables.append(t)
    return r


# ============================================================ CASH FLOW
def cash_flow(p, period):
    r = Report('cash_flow', 'Cash Flow Report', 'Cash Flow', S.BASIS['cash'], period, landscape=True)
    cf = S.cash_flow(period.start, period.end)
    r.statement = ('Cash flow uses actual recorded dates only: customer receipts by Receipt Date, vendor payments and '
                   'reimbursements and salary payments by payment date.')
    t = Table('Cash flow summary', [Col('Line'), Col('Amount (INR)', M)])
    for lab, v, st in (('Cash inflow: invoice payments received', cf['receipts'], ''),
                       ('Cash inflow: other business receipts', None, ''),
                       ('Total recorded cash inflow', cf['inflow'], 'subtotal'),
                       ('Cash outflow: salary payments', cf['salary_paid'], ''),
                       ('Cash outflow: employee reimbursements', cf['reimbursed'], ''),
                       ('Cash outflow: vendor payments', cf['vendor_paid'], ''),
                       ('Total recorded cash outflow', cf['outflow'], 'subtotal'),
                       ('Net recorded cash flow', cf['net'], 'subtotal')):
        t.add([lab, v], st)
    r.tables.append(t)
    r.card('Recorded Cash Inflow', cf['inflow'], note='Customer receipts by receipt date')
    r.card('Recorded Cash Outflow', cf['outflow'])
    r.card('Net Recorded Cash Flow', cf['net'])
    mt = Table('Monthly recorded cash flow', [Col('Month'), Col('Receipts', M), Col('Vendor Payments', M),
               Col('Reimbursements', M), Col('Total Outflow', M), Col('Net Recorded', M)])
    for k, v in S.monthly_matrix(period.start, period.end).items():
        mt.add([mname(*k), v['received'], v['vendor_paid'], v['reimbursed'], v['cash_out'], v['received'] - v['cash_out']])
    mt.sum_total()
    vt = Table('Vendor payments', [Col('Payment Date', DT), Col('Expense ID'), Col('Vendor'), Col('Category'),
               Col('Method'), Col('Reference'), Col('Amount (INR)', M)], paginate=True)
    for x in S.vendor_payments(period.start, period.end):
        vt.add([x['payment_date'], x['expense__expense_id'], x['expense__vendor__name'] or '',
                x['expense__category__name'], x['method'], x['reference'], x['inr']])
    vt.sum_total()
    rt = Table('Employee reimbursements', [Col('Reimbursement Date', DT), Col('Claim ID'), Col('Employee'),
               Col('Method'), Col('Reference'), Col('Amount (INR)', M)], paginate=True)
    for x in S.reimbursements(period.start, period.end):
        rt.add([x['reimbursement_date'], x['claim__claim_id'],
                f"{x['claim__snap_employee_code']} {x['claim__snap_name']}", x['method'], x['reference'], x['inr']])
    rt.sum_total()
    ct = Table('Customer receipts', [Col('Receipt Date', DT), Col('Invoice'), Col('Customer'), Col('Mode'),
               Col('Reference'), Col('Amount (INR)', M)], paginate=True)
    for x in S.payments(period.start, period.end):
        ct.add([x.receipt_date, x.invoice.display_number, x.invoice.client.name, x.get_mode_display(), x.reference,
                S.q2(x.amount)])
    ct.sum_total()
    r.tables += [mt, ct, vt, rt]
    r.limitations = S.LIMITATIONS[1:3]
    return r


# ============================================================ OUTSTANDING
def _aged(title, rows, cols, fn):
    s = Table(f'{title} - ageing summary', [Col('Age Bucket'), Col('Items', I), Col('Outstanding (INR)', M)])
    for b in S.AGE_BUCKETS:
        rs = [x for x in rows if x['bucket'] == b]
        s.add([b, len(rs), S.dsum(x['outstanding_inr'] for x in rs)])
    s.sum_total()
    d = Table(f'{title} - detail', cols, paginate=True)
    for x in rows:
        d.add(fn(x))
    d.sum_total()
    if d.total:
        d.total[-2] = ''
    return s, d


def outstanding(p, period):
    a = as_at(period)
    r = Report('outstanding', 'Outstanding Amounts', 'Outstanding', f'As at {a:%d %b %Y} (period end, capped at today)',
               period, landscape=True)
    vp, rp = S.vendor_payables(a), S.reimbursements_payable(a)
    cr = [x for x in S.invoice_balances(a) if x['outstanding'] > 0]
    r.card('Customer Receivables', S.dsum(x['outstanding'] for x in cr), link=link('receivables', period))
    r.card('Vendor Payables', S.dsum(x['outstanding_inr'] for x in vp))
    r.card('Employee Reimbursements Payable', S.dsum(x['outstanding_inr'] for x in rp))
    pp = S.payroll_payable(a)
    r.card('Payroll Payable', S.dsum(x['outstanding'] for x in pp if x['outstanding'] > 0), note='Net salary less recorded payments')
    sm = Table('1. Customer Receivables - ageing summary', [Col('Age Bucket'), Col('Invoices', I),
               Col('Outstanding (INR)', M)])
    for b in S.AGE_BUCKETS:
        rs = [x for x in cr if x['bucket'] == b]
        sm.add([b, len(rs), S.dsum(x['outstanding'] for x in rs)])
    sm.sum_total()
    r.tables.append(sm)
    r.tables += _aged('2. Vendor Payables', vp, [Col('Expense ID'), Col('Vendor'), Col('Expense Date', DT),
                      Col('Due Date', DT), Col('Total (INR)', M), Col('Paid (INR)', M), Col('Outstanding (INR)', M),
                      Col('Days Overdue', I), Col('Age Bucket')],
                      lambda x: [x['expense_id'], x['vendor__name'] or '', x['expense_date'], x['due_date'],
                                 x['total_inr'], x['paid_inr'], x['outstanding_inr'], x['days'], x['bucket']])
    r.tables += _aged('3. Employee Reimbursements Payable', rp, [Col('Claim ID'), Col('Employee'), Col('Claim Date', DT),
                      Col('Approved (INR)', M), Col('Reimbursed (INR)', M), Col('Outstanding (INR)', M),
                      Col('Days Since Claim', I), Col('Age Bucket')],
                      lambda x: [x['claim_id'], f"{x['snap_employee_code']} {x['snap_name']}", x['claim_date'],
                                 x['approved_inr'], x['reimbursed_inr'], x['outstanding_inr'], x['days'], x['bucket']])
    pt4 = Table('4. Payroll Payable', [Col('Employee'), Col('Payroll Month'), Col('Net Salary (INR)', M),
                Col('Paid (INR)', M), Col('Outstanding (INR)', M)], paginate=True)
    for x in pp:
        if x['outstanding'] != 0:
            pt4.add([f"{x['p'].snap_employee_code} {x['p'].snap_name}", x['p'].period.label, x['net'], x['paid'],
                     x['outstanding']])
    pt4.sum_total()
    r.tables.append(pt4)
    r.notes = ['Vendor ageing uses Due Date (Expense Date when blank); reimbursement ageing uses Claim Date. '
               'Balances include all earlier records, not only those dated in the selected period.']
    r.limitations = S.LIMITATIONS[:2]
    return r


# ============================================================ PROFITABILITY
def client_profit(p, period):
    r = Report('client_profit', 'Client Profitability', 'Profitability',
               'Revenue by Invoice Date; directly linked company expenses (Expense Date) and approved claims '
               '(Claim Expense Date)', period, landscape=True)
    agg = defaultdict(lambda: dict(name='', rev=Z, exp=Z, cl=Z))
    for cid, n, t in S.invoices(period.start, period.end).values_list('client_id', 'client__name', 'total'):
        agg[cid]['name'] = n
        agg[cid]['rev'] += S.q2(t)
    for x in S.company_expenses(period.start, period.end):
        if x['client_id']:
            agg[x['client_id']]['name'] = x['client__name']
            agg[x['client_id']]['exp'] += x['total_inr']
    for x in S.claims(period.start, period.end, C.APPROVED_STATES):
        if x['client_id']:
            agg[x['client_id']]['name'] = x['client__name']
            agg[x['client_id']]['cl'] += x['approved_inr']
    t = Table('Client profitability (direct costs only)', [Col('Client'), Col('Revenue', M),
              Col('Direct Company Expenses', M), Col('Direct Employee Claims', M), Col('Total Direct Cost', M),
              Col('Profit / Loss', M), Col('Margin %', PC)])
    for cid, a in sorted(agg.items(), key=lambda kv: -kv[1]['rev']):
        cost = a['exp'] + a['cl']
        t.add([(a['name'], link('rev_customer', period, client=cid)), a['rev'], a['exp'], a['cl'], cost,
               a['rev'] - cost, pct(a['rev'] - cost, a['rev'])])
    tot = t.sum_total()
    tot[6] = pct(tot[5], tot[1])
    t.note = ('Only expenses directly linked to a client or project are allocated. Salary cost is NOT allocated '
              '(no timesheet allocation exists). See Unallocated Expenses.')
    r.tables.append(t)
    return r


def project_profit(p, period):
    pid = iparam(p, 'project')
    r = Report('project_profit', 'Project Profitability', 'Profitability',
               'Revenue = invoice line amounts by Invoice Date; directly linked expenses only', period, landscape=True)
    agg = defaultdict(lambda: dict(name='', client='', rev=Z, days=Z, exp=Z, cl=Z))
    for ln in S.invoice_lines(period.start, period.end, project=pid).values(
            'project_id', 'project__name', 'project_name', 'invoice__client__name', 'amount', 'quantity_days'):
        k = ln['project_id'] or f"unlinked:{ln['project_name']}"
        a = agg[k]
        a['name'] = ln['project__name'] or f"{ln['project_name']} (not linked to a project)"
        a['client'] = ln['invoice__client__name']
        a['rev'] += S.q2(ln['amount'])
        a['days'] += S.q2(ln['quantity_days'])
    exps = [x for x in S.company_expenses(period.start, period.end, project=pid) if x['project_id']]
    cls = [x for x in S.claims(period.start, period.end, C.APPROVED_STATES, project=pid) if x['project_id']]
    for x in exps:
        a = agg[x['project_id']]
        a['exp'] += x['total_inr']
        a['name'] = a['name'] or x['project__name']
        a['client'] = a['client'] or (x['client__name'] or '')
    for x in cls:
        a = agg[x['project_id']]
        a['cl'] += x['approved_inr']
        a['name'] = a['name'] or x['project__name']
        a['client'] = a['client'] or (x['client__name'] or '')
    t = Table('Project profitability (direct costs only)', [Col('Client'), Col('Project'), Col('Days Billed', M),
              Col('Revenue', M), Col('Direct Company Expenses', M), Col('Direct Employee Claims', M),
              Col('Profit / Loss', M), Col('Margin %', PC)])
    for k, a in sorted(agg.items(), key=lambda kv: -kv[1]['rev']):
        nm = (a['name'], link('project_profit', period, project=k)) if isinstance(k, int) else a['name']
        prof = a['rev'] - a['exp'] - a['cl']
        t.add([a['client'], nm, a['days'], a['rev'], a['exp'], a['cl'], prof, pct(prof, a['rev'])])
    tot = t.sum_total()
    tot[7] = pct(tot[6], tot[3])
    r.tables.append(t)
    if pid:
        r.filters.append(('Project', names(ProjectTemplate, pid)))
        lt = Table('Invoice lines', [Col('Invoice'), Col('Invoice Date', DT), Col('Description'), Col('Days', M),
                   Col('Amount (INR)', M)])
        for ln in S.invoice_lines(period.start, period.end, project=pid).order_by('invoice__invoice_date'):
            lt.add([(ln.invoice.display_number, reverse('invoice_pdf', args=[ln.invoice_id])), ln.invoice.invoice_date,
                    ln.description, S.q2(ln.quantity_days), S.q2(ln.amount)])
        lt.sum_total()
        et = Table('Linked expenses', [Col('Type'), Col('ID'), Col('Date', DT), Col('Category'), Col('Amount (INR)', M)])
        for x in exps:
            et.add(['Company expense', x['expense_id'], x['expense_date'], x['category__name'], x['total_inr']])
        for x in cls:
            et.add(['Employee claim', x['claim_id'], x['expense_date'], x['category__name'], x['approved_inr']])
        et.sum_total()
        r.tables += [lt, et]
    r.notes = ['Salary cost is not allocated to projects (no allocation data exists).']
    return r


def unallocated(p, period):
    r = Report('unallocated', 'Unallocated Expenses', 'Profitability', S.BASIS['company'] + '; ' + S.BASIS['claims'],
               period, landscape=True)
    t = Table('Expenses not linked to any department, client or project', [Col('Type'), Col('ID'), Col('Date', DT),
              Col('Vendor / Employee'), Col('Category'), Col('Amount (INR)', M)], paginate=True)
    for x in S.company_expenses(period.start, period.end):
        if not (x['department_id'] or x['client_id'] or x['project_id']):
            t.add(['Company expense', x['expense_id'], x['expense_date'], x['vendor__name'] or '', x['category__name'],
                   x['total_inr']])
    for x in S.claims(period.start, period.end, C.APPROVED_STATES):
        if not (x['department_id'] or x['client_id'] or x['project_id']):
            t.add(['Employee claim', x['claim_id'], x['expense_date'], x['snap_name'], x['category__name'],
                   x['approved_inr']])
    t.sum_total()
    r.tables.append(t)
    return r


# ============================================================ registry
SECTIONS = [
    ('Revenue', [('rev_summary', 'Revenue Summary', rev_summary), ('rev_monthly', 'Monthly Revenue', rev_monthly),
                 ('rev_customer', 'Customer-wise Revenue', rev_customer),
                 ('rev_project', 'Project-wise Revenue', rev_project),
                 ('invoice_register', 'Invoice Register', invoice_register),
                 ('receivables', 'Outstanding Receivables', receivables),
                 ('receipts_register', 'Receipts Register', receipts_register)]),
    ('Employees', [('emp_master', 'Employee Master', emp_master), ('emp_earnings', 'Employee Earnings', emp_earnings),
                   ('emp_department', 'Department-wise Employees', emp_department),
                   ('emp_joining', 'Joining and Leaving', emp_joining),
                   ('salary_history', 'Salary History', salary_history), ('emp_cost', 'Employee Cost', emp_cost)]
     + HR.EMPLOYEE_ITEMS),
    ('Payroll', [('payroll_summary', 'Monthly Payroll Summary', payroll_summary),
                 ('payroll_register', 'Payroll Register', payroll_register),
                 ('payroll_payment', 'Payroll Payment', payroll_payment),
                 ('payroll_department', 'Department-wise Payroll Cost', payroll_department),
                 ('increments', 'Salary Increments', increments)] + HR.PAYROLL_ITEMS),
    ('Expenses', [('exp_summary', 'Expense Summary', exp_summary),
                  ('company_expenses', 'Company Expenses', company_expenses),
                  ('employee_claims', 'Employee Expense Claims', employee_claims),
                  ('exp_category', 'Category-wise Expenses', exp_category),
                  ('exp_vendor', 'Vendor-wise Expenses', exp_vendor),
                  ('exp_department', 'Department-wise Expenses', exp_department),
                  ('exp_trends', 'Expense Trends', exp_trends)]),
    ('Financial', [('pnl', 'Management Profit and Loss', pnl), ('rev_vs_exp', 'Revenue vs Expense', rev_vs_exp),
                   ('monthly_perf', 'Monthly Business Performance', monthly_perf),
                   ('fy_compare', 'Financial Year Comparison', fy_compare), ('ratios', 'Financial Ratios', ratios),
                   ('emp_cost_vs_rev', 'Employee Cost vs Revenue', emp_cost_vs_rev)]),
    ('Cash Flow', [('cash_flow', 'Cash Flow', cash_flow)]),
    ('Outstanding', [('outstanding', 'Outstanding Amounts', outstanding)]),
    ('Profitability', [('client_profit', 'Client Profitability', client_profit),
                       ('project_profit', 'Project Profitability', project_profit),
                       ('unallocated', 'Unallocated Expenses', unallocated)]),
    ('Ledger', LR.ITEMS),
]
REGISTRY = {k: (t, sec, fn) for sec, items in SECTIONS for k, t, fn in items}
SECTION_SLUGS = {'revenue': 'Revenue', 'employees': 'Employees', 'payroll': 'Payroll', 'expenses': 'Expenses',
                 'financial': 'Financial', 'cash-flow': 'Cash Flow', 'outstanding': 'Outstanding',
                 'profitability': 'Profitability', 'ledger': 'Ledger'}
# Which filters each report accepts (drives the filter bar).
FILTERS = {
    'rev_customer': ['client'], 'rev_project': ['client', 'project', 'consultant'], 'invoice_register': ['client'],
    'receivables': ['client'], 'receipts_register': ['client'],
    'emp_master': ['department', 'designation', 'emp_status'], 'emp_earnings': ['employee', 'department'],
    'salary_history': ['employee'], 'payroll_register': ['employee', 'department'],
    'company_expenses': ['vendor', 'category', 'department', 'client', 'project', 'exp_status', 'group_by'],
    'employee_claims': ['employee', 'department', 'category', 'claim_status'], 'exp_trends': ['by'],
    'project_profit': ['project'], 'fy_compare': ['fy_ab'], 'ledger_gl': ['account'],
    'advances_outstanding': ['employee', 'department'], 'advance_schedule': ['employee', 'department'],
    'leave_dept': ['department'],
}
NO_PERIOD = {'emp_master', 'fy_compare'}


PRIOR_SUBTOTAL_KEYS = {'pnl', 'rev_vs_exp', 'ledger_pnl'}


def build(key, params):
    m = PR.mode(params)
    with PR.scope(m):
        period = P.resolve(params)
        r = REGISTRY[key][2](params, period)
        if m != PR.INCLUDE:
            r.filters.append(('Prior-period expenses', PR.LABELS[m]))
        if r.section == 'Expenses' or key in PRIOR_SUBTOTAL_KEYS:
            r.card('Prior-period expenses (subtotal)', S.prior_period_subtotal(period.start, period.end),
                   note='booked this period, relating to an earlier FY; included in the totals above'
                   if m == PR.INCLUDE else PR.LABELS[m])
    return r
