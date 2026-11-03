"""Report builders. All sums are done in Python with Decimal (amounts converted to INR with
each record's stored exchange rate) so results are exact and currency-safe."""
from collections import OrderedDict, defaultdict
from datetime import date

from django.db.models import Q

from .fy import fy_label, fy_start_year, months_between, resolve_period
from .models import ZERO, CompanyExpense, EmployeeExpenseClaim, EmployeeReimbursement, q2

C = EmployeeExpenseClaim
GST_NOTE = ('For accounting reference only. Recorded GST is NOT assumed to be eligible for input tax credit; '
            'ITC eligibility must be reviewed by your accountant.')

REPORTS = OrderedDict([
    ('monthly', 'Monthly expense report'),
    ('category', 'Category-wise expense report'),
    ('department', 'Department-wise expense report'),
    ('employee', 'Employee-wise reimbursement report'),
    ('vendor', 'Vendor-wise expense report'),
    ('project', 'Client / project-wise expense report'),
    ('gst', 'GST report'),
])


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_filters(params, default_period='current_fy'):
    start, end, label, key = resolve_period(params, default_period)
    f = dict(start=start, end=end, label=label, period=key)
    for k in ('category', 'vendor', 'employee', 'department', 'client', 'project'):
        f[k] = _int(params.get(k))
    return f


def company_qs(f, include_cancelled=False):
    qs = CompanyExpense.objects.select_related('vendor', 'category', 'subcategory', 'department', 'client', 'project',
                                               'employee').filter(expense_date__range=(f['start'], f['end']))
    if not include_cancelled:
        qs = qs.exclude(payment_status=CompanyExpense.CANCELLED)
    for k in ('category', 'vendor', 'employee', 'department', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'{k}_id': f[k]})
    return qs


def claims_qs(f, statuses=C.APPROVED_STATES, date_field='expense_date'):
    qs = C.objects.select_related('employee', 'department', 'category', 'client', 'project').filter(
        **{f'{date_field}__range': (f['start'], f['end'])})
    if statuses:
        qs = qs.filter(status__in=statuses)
    for k in ('category', 'employee', 'department', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'{k}_id': f[k]})
    if f.get('vendor'):
        qs = qs.none()
    return qs


def reimb_qs(f):
    qs = EmployeeReimbursement.objects.select_related('claim', 'claim__department', 'claim__employee').filter(
        reimbursement_date__range=(f['start'], f['end']))
    for k in ('category', 'employee', 'department', 'client', 'project'):
        if f.get(k):
            qs = qs.filter(**{f'claim__{k}_id': f[k]})
    if f.get('vendor'):
        qs = qs.none()
    return qs


def r_inr(r):
    return q2(r.amount * r.claim.exchange_rate)


def _report(key, headers, rows, totals=None, note='', money_cols=()):
    return dict(key=key, title=REPORTS.get(key, key), headers=headers, rows=rows, totals=totals, note=note,
                money_cols=set(money_cols))


def monthly(f):
    comp, reimb = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for e in company_qs(f):
        comp[(e.expense_date.year, e.expense_date.month)] += e.total_inr
    for r in reimb_qs(f):
        reimb[(r.reimbursement_date.year, r.reimbursement_date.month)] += r_inr(r)
    rows = []
    for y, m in months_between(f['start'], f['end']):
        a, b = q2(comp[(y, m)]), q2(reimb[(y, m)])
        rows.append([date(y, m, 1).strftime('%b %Y'), a, b, q2(a + b)])
    tot = ['Total', q2(sum((r[1] for r in rows), ZERO)), q2(sum((r[2] for r in rows), ZERO)),
           q2(sum((r[3] for r in rows), ZERO))]
    return _report('monthly', ['Month', 'Company Expenses (INR)', 'Employee Reimbursements (INR)', 'Total (INR)'],
                   rows, tot, 'Company expenses by expense date; reimbursements by payment date.', (1, 2, 3))


def category(f):
    data = OrderedDict()
    for e in company_qs(f):
        d = data.setdefault(e.category.name, [0, ZERO])
        d[0] += 1
        d[1] += e.total_inr
    for c in claims_qs(f):
        d = data.setdefault(c.category.name, [0, ZERO])
        d[0] += 1
        d[1] += c.approved_inr
    rows = sorted(([k, v[0], q2(v[1])] for k, v in data.items()), key=lambda r: -r[2])
    tot = ['Total', sum(r[1] for r in rows), q2(sum((r[2] for r in rows), ZERO))]
    return _report('category', ['Category', 'Number of Transactions', 'Total Amount (INR)'], rows, tot,
                   'Company expenses (not cancelled) plus approved employee claims (approved amount).', (2,))


def department(f):
    data = defaultdict(lambda: [ZERO, ZERO])
    for e in company_qs(f):
        data[e.department.name if e.department_id else 'Unassigned (company overhead)'][0] += e.total_inr
    for c in claims_qs(f):
        data[c.department.name if c.department_id else (c.snap_department or 'Unassigned')][1] += c.approved_inr
    rows = sorted(([k, q2(a), q2(b), q2(a + b)] for k, (a, b) in data.items()), key=lambda r: -r[3])
    tot = ['Total'] + [q2(sum((r[i] for r in rows), ZERO)) for i in (1, 2, 3)]
    return _report('department', ['Department', 'Company Expenses (INR)', 'Employee Claims (INR)', 'Total (INR)'],
                   rows, tot, 'Employee claims are counted at their approved amount.', (1, 2, 3))


def employee(f):
    data = OrderedDict()
    for c in claims_qs(f, statuses=None).exclude(status__in=[C.DRAFT, C.CANCELLED]).order_by('employee__employee_id'):
        d = data.setdefault(c.employee_id, [f'{c.employee.employee_id} - {c.employee.full_name}', 0, ZERO, ZERO, ZERO,
                                            ZERO])
        d[1] += 1
        d[2] += q2(c.amount_claimed * c.exchange_rate)
        if c.status in C.APPROVED_STATES:
            d[3] += c.approved_inr
            paid = q2(c.reimbursed_amount * c.exchange_rate)
            d[4] += paid
            d[5] += q2(c.approved_inr - paid)
    rows = [[v[0], v[1], q2(v[2]), q2(v[3]), q2(v[4]), q2(v[5])] for v in data.values()]
    tot = ['Total', sum(r[1] for r in rows)] + [q2(sum((r[i] for r in rows), ZERO)) for i in (2, 3, 4, 5)]
    return _report('employee', ['Employee', 'Claims Submitted', 'Claimed (INR)', 'Approved (INR)', 'Reimbursed (INR)',
                                'Outstanding (INR)'], rows, tot,
                   'Claims by expense date (excluding drafts and cancelled). Reimbursed = all payments recorded so far.',
                   (2, 3, 4, 5))


def vendor(f):
    data = OrderedDict()
    for e in company_qs(f).order_by('vendor__name'):
        name = e.vendor.name if e.vendor_id else '(No vendor)'
        d = data.setdefault(name, [0, ZERO, ZERO, ZERO])
        d[0] += 1
        d[1] += e.total_inr
        paid = q2(e.paid_amount * e.exchange_rate)
        d[2] += paid
        d[3] += q2(e.total_inr - paid)
    rows = [[k, v[0], q2(v[1]), q2(v[2]), q2(v[3])] for k, v in data.items()]
    tot = ['Total', sum(r[1] for r in rows)] + [q2(sum((r[i] for r in rows), ZERO)) for i in (2, 3, 4)]
    return _report('vendor', ['Vendor', 'Number of Bills', 'Total Amount (INR)', 'Paid (INR)', 'Outstanding (INR)'],
                   rows, tot, '', (2, 3, 4))


def project(f):
    rows = []
    for e in company_qs(f).filter(Q(client__isnull=False) | Q(project__isnull=False)):
        rows.append([e.client.name if e.client_id else '', e.project.name if e.project_id else '', e.expense_id,
                     e.expense_date, 'Company Expense', e.category.name, e.description, e.total_inr])
    for c in claims_qs(f).filter(client__isnull=False):
        rows.append([c.client.name, c.project.name if c.project_id else '', c.claim_id, c.expense_date,
                     'Employee Claim', c.category.name, c.description, c.approved_inr])
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    tot = ['Total', '', '', '', '', '', '', q2(sum((r[7] for r in rows), ZERO))]
    return _report('project', ['Client', 'Project', 'ID', 'Date', 'Type', 'Category', 'Description', 'Amount (INR)'],
                   rows, tot, 'Only expenses allocated to a client or project. General overheads are excluded.', (7,))


def gst(f):
    rows = []
    for e in company_qs(f).filter(gst_amount__gt=0).order_by('invoice_date', 'expense_date'):
        rows.append([e.vendor.name if e.vendor_id else '', e.vendor.gstin if e.vendor_id else '', e.invoice_number,
                     e.invoice_date or e.expense_date, e.amount_before_tax, e.cgst_amount, e.sgst_amount,
                     e.igst_amount, e.gst_amount, e.total_amount, e.currency])
    tot = ['Total', '', '', ''] + [q2(sum((r[i] for r in rows), ZERO)) for i in range(4, 10)] + ['']
    return _report('gst', ['Vendor Name', 'Vendor GSTIN', 'Invoice Number', 'Invoice Date', 'Taxable Amount', 'CGST',
                           'SGST', 'IGST', 'Total GST', 'Invoice Total', 'Currency'], rows, tot, GST_NOTE,
                   (4, 5, 6, 7, 8, 9))


BUILDERS = dict(monthly=monthly, category=category, department=department, employee=employee, vendor=vendor,
                project=project, gst=gst)


def build(key, f):
    return BUILDERS[key](f)


# --------------------------------------------------------------------------- dashboard
def dashboard(f):
    from .fy import fy_range, month_range, today
    t = today()
    ms, me = month_range(t.year, t.month)
    fs, fe = fy_range(fy_start_year(t))

    def total_between(s, e):
        g = dict(f, start=s, end=e)
        return q2(sum((x.total_inr for x in company_qs(g)), ZERO) + sum((c.approved_inr for c in claims_qs(g)), ZERO))

    period_claims = list(claims_qs(f, statuses=None, date_field='claim_date'))

    def bucket(states):
        items = [c for c in period_claims if c.status in states]
        return len(items), q2(sum((q2((c.approved_amount if c.approved_amount is not None else c.amount_claimed)
                                      * c.exchange_rate) for c in items), ZERO))

    comp = list(company_qs(f))
    reimb = list(reimb_qs(f))
    cards = dict(
        month_total=total_between(ms, me), month_label=t.strftime('%B %Y'),
        fy_total=total_between(fs, fe), fy_label=fy_label(fy_start_year(t)),
        pending=bucket(C.PENDING), approved=bucket((C.APPROVED, C.PARTIALLY_APPROVED)),
        reimbursed=bucket((C.REIMBURSED,)),
        company_total=q2(sum((e.total_inr for e in comp), ZERO)), company_count=len(comp),
        company_outstanding=q2(sum((q2(e.outstanding_amount * e.exchange_rate) for e in comp), ZERO)),
        reimb_total=q2(sum((r_inr(r) for r in reimb), ZERO)), reimb_count=len(reimb),
    )
    return dict(cards=cards, monthly=monthly(f), category=category(f), department=department(f),
                employee=employee(f))
