"""Customer and project analysis: customer movement, customer-wise earnings, projects and manpower.

Definitions used on the page (also shown as notes):
* customer added = ``Client.created_on`` inside the period;
* customer left = ``Client.inactive_since`` inside the period;
* revenue = taxable value of non-cancelled invoices by invoice date;
* receipts = invoice payments by receipt date;
* outstanding = positive invoice balances as at the period end (or today);
* live manpower = deployments with status active/on hold covering the reporting date;
* new customer revenue = revenue from customers whose first recorded activity is inside the period.
"""
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, Max, Min, Q
from django.urls import reverse

from invoice_app.models import Client, Invoice, InvoiceLine, InvoicePayment, ManpowerDeployment, ProjectTemplate

from . import charts, periods as P
from . import services as S
from .core import inr

Z = S.ZERO


def _months(period):
    return [(date(y, m, 1), (y, m)) for y, m in period.months()]


def _customer_report_url(period, client_id):
    params = dict(period.params)
    params['client'] = client_id
    return reverse('reporting:report', args=['rev_customer']) + '?' + urlencode(params)


def build(period):
    start, end = period.start, period.end
    as_at = min(end, P.today())
    months = _months(period)
    labels = [d.strftime('%b %y') for d, _ in months]
    index = {key: i for i, (_, key) in enumerate(months)}
    n = len(months)

    clients = list(Client.objects.values('id', 'name', 'active', 'created_on', 'inactive_since'))
    names = {c['id']: c['name'] for c in clients}
    created = {c['id']: c['created_on'] for c in clients}
    left_on = {c['id']: c['inactive_since'] for c in clients}
    active_now = sum(1 for c in clients if c['active'])

    revenue = defaultdict(lambda: Z)
    monthly_revenue = defaultdict(lambda: [Z for _ in range(n)])
    invoice_rows = list(Invoice.objects.filter(invoice_date__range=(start, end), cancelled=False)
                        .values_list('client_id', 'taxable_value', 'invoice_date'))
    for client_id, amount, inv_date in invoice_rows:
        amount = S.q2(amount)
        revenue[client_id] += amount
        if (inv_date.year, inv_date.month) in index:
            monthly_revenue[client_id][index[(inv_date.year, inv_date.month)]] += amount
    billed_customers = len(revenue)

    first_invoice = {cid: d for cid, d in Invoice.objects.filter(cancelled=False).order_by()
                     .values('client_id').annotate(d=Min('invoice_date')).values_list('client_id', 'd')}
    first_seen = {cid: created.get(cid) or first_invoice.get(cid) for cid in names}

    receipts = defaultdict(lambda: Z)
    for client_id, amount in (InvoicePayment.objects.filter(receipt_date__range=(start, end))
                              .values_list('invoice__client_id', 'amount')):
        receipts[client_id] += S.q2(amount)

    outstanding = defaultdict(lambda: Z)
    for row in S.invoice_balances(as_at):
        client_id = row['inv'].client_id
        if row['outstanding'] > 0:
            outstanding[client_id] += row['outstanding']

    last_invoice = {cid: d for cid, d in Invoice.objects.filter(cancelled=False, invoice_date__lte=as_at)
                    .order_by().values('client_id').annotate(d=Max('invoice_date'))
                    .values_list('client_id', 'd')}

    active_projects = {cid: count for cid, count in ProjectTemplate.objects.filter(active=True).order_by()
                       .values('client_id').annotate(count=Count('id')).values_list('client_id', 'count')}
    active_map = {c['id']: c['active'] for c in clients}
    billed_projects = defaultdict(set)
    for client_id, project_id, project_name in (InvoiceLine.objects
                                                .filter(invoice__invoice_date__range=(start, end),
                                                        invoice__cancelled=False)
                                                .values_list('invoice__client_id', 'project_id', 'project_name')):
        if project_id or project_name:
            billed_projects[client_id].add(project_id or project_name)

    live = (ManpowerDeployment.objects.filter(status__in=('active', 'on_hold'), start_date__lte=as_at)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=as_at)))
    manpower = {cid: count for cid, count in live.order_by().values('client_id')
                .annotate(count=Count('id')).values_list('client_id', 'count')}

    joined, released = [0] * n, [0] * n
    for (d,) in ManpowerDeployment.objects.filter(start_date__range=(start, end)).exclude(status='draft') \
            .values_list('start_date'):
        if (d.year, d.month) in index:
            joined[index[(d.year, d.month)]] += 1
    for (d,) in ManpowerDeployment.objects.filter(end_date__range=(start, end)).exclude(status='draft') \
            .values_list('end_date'):
        if d and (d.year, d.month) in index:
            released[index[(d.year, d.month)]] += 1

    added, departed = [0] * n, [0] * n
    for c in clients:
        for series, value in ((added, c['created_on']), (departed, c['inactive_since'])):
            if value and start <= value <= end and (value.year, value.month) in index:
                series[index[(value.year, value.month)]] += 1

    active_series = []
    for _, (y, m) in months:
        at = min(P.month_end(y, m), end)
        active_series.append(sum(1 for c in clients
                                 if (not c['created_on'] or c['created_on'] <= at)
                                 and (not c['inactive_since'] or c['inactive_since'] > at)))

    new_revenue, repeat_revenue = [Z] * n, [Z] * n
    for client_id, amount, inv_date in invoice_rows:
        key = (inv_date.year, inv_date.month)
        if key not in index:
            continue
        amount = S.q2(amount)
        seen = first_seen.get(client_id)
        if seen and start <= seen <= end:
            new_revenue[index[key]] += amount
        else:
            repeat_revenue[index[key]] += amount

    top_customers = sorted(((cid, amount) for cid, amount in revenue.items() if amount > 0),
                           key=lambda kv: (-kv[1], names.get(kv[0], '')))[:4]
    top_series = [monthly_revenue[cid] for cid, _ in top_customers]
    top_legend = [names.get(cid, 'Customer')[:18] for cid, _ in top_customers]

    total_revenue = S.dsum(revenue.values())
    total_received = S.dsum(receipts.values())
    total_outstanding = S.dsum(outstanding.values())
    live_manpower = sum(manpower.values())
    active_project_count = sum(active_projects.values())
    new_count, left_count = sum(added), sum(departed)
    undated_inactive = sum(1 for c in clients if not c['active'] and not c['inactive_since'])

    cards = [
        dict(label='Active customers', value=f'{active_now:,}', note='Currently marked active'),
        dict(label='Customers added', value=f'{new_count:,}', note=f'First recorded activity in {period.label}'),
        dict(label='Customers left', value=f'{left_count:,}',
             note='Marked inactive in this period' + (f' ({undated_inactive} inactive, date not recorded)'
                                                      if undated_inactive else '')),
        dict(label='Customers billed', value=f'{billed_customers:,}', note='With a non-cancelled invoice in this period'),
        dict(label='Revenue', value=inr(total_revenue), note='Taxable value, excluding GST'),
        dict(label='Amount received', value=inr(total_received), note='By receipt date'),
        dict(label='Outstanding receivables', value=inr(total_outstanding), note=f'As at {as_at:%d %b %Y}'),
        dict(label='Active projects', value=f'{active_project_count:,}', note='Currently active project records'),
        dict(label='Live manpower', value=f'{live_manpower:,}', note=f'Deployments active at {as_at:%d %b %Y}'),
    ]

    chart_list = [
        dict(title='Customers added vs left', svg=charts.bars(
            labels, [added, departed], 'Customers added and left', legend=['Added', 'Left'], money=False),
            note='Counts by the month of the recorded date.'),
        dict(title='Active customers at each month end', svg=charts.line(
            labels, [active_series], 'Active customers', money=False),
            note='Customers added up to the month end and not yet marked inactive.'),
        dict(title='Revenue trend by customer', svg=charts.line(
            labels, top_series, 'Revenue by customer', legend=top_legend),
            note='Top customers by revenue in the period; taxable value excluding GST.'),
        dict(title='New vs repeat customer revenue', svg=charts.bars(
            labels, [new_revenue, repeat_revenue], 'New vs repeat customer revenue',
            legend=['New customers', 'Existing customers']),
            note='New = first recorded activity inside this period.'),
        dict(title='Revenue by customer', svg=charts.hbars(
            [(names.get(cid, 'Customer'), amount) for cid, amount in
             sorted(((cid, amount) for cid, amount in revenue.items() if amount > 0),
                    key=lambda kv: -kv[1])[:10]], 'Revenue by customer'),
            note='Customers with revenue in this period.'),
        dict(title='Active projects by customer', svg=charts.hbars(
            [(names.get(cid, 'Customer'), count) for cid, count in
             sorted(active_projects.items(), key=lambda kv: -kv[1]) if count], 'Projects', money=False),
            note='Current active project records, regardless of the selected period.'),
        dict(title='Live manpower by customer', svg=charts.hbars(
            [(names.get(cid, 'Customer'), count) for cid, count in
             sorted(manpower.items(), key=lambda kv: -kv[1]) if count], 'Manpower', money=False),
            note='Deployments active on the reporting date.'),
        dict(title='Manpower joined vs released', svg=charts.bars(
            labels, [joined, released], 'Manpower movements', legend=['Joined', 'Released'], money=False),
            note='By deployment start and end dates.'),
        dict(title='Outstanding receivables by customer', svg=charts.hbars(
            [(names.get(cid, 'Customer'), amount) for cid, amount in
             sorted(((cid, amount) for cid, amount in outstanding.items() if amount > 0),
                    key=lambda kv: -kv[1])[:10]], 'Outstanding'),
            note=f'Positive invoice balances as at {as_at:%d %b %Y}.'),
    ]

    rows = []
    for cid, cname in names.items():
        added_in = bool(created.get(cid) and start <= created[cid] <= end)
        left_in = bool(left_on.get(cid) and start <= left_on[cid] <= end)
        billed = len(billed_projects.get(cid, ()))
        has_activity = bool(revenue.get(cid) or receipts.get(cid) or billed or manpower.get(cid)
                            or outstanding.get(cid) or added_in or left_in)
        if not has_activity:
            continue
        rows.append(dict(
            name=cname,
            url=_customer_report_url(period, cid),
            active=active_map.get(cid, False),
            added=created.get(cid),
            new=added_in,
            left=left_in,
            projects=active_projects.get(cid, 0),
            billed_projects=billed,
            manpower=manpower.get(cid, 0),
            revenue=inr(revenue.get(cid, Z)),
            received=inr(receipts.get(cid, Z)),
            outstanding=inr(outstanding.get(cid, Z)),
            last_invoice=last_invoice.get(cid),
            sort_key=revenue.get(cid, Z),
        ))
    rows.sort(key=lambda r: (-r['sort_key'], r['name']))

    notes = [
        'Customer added uses the first recorded activity date (earliest invoice, deployment, agreement or '
        'document); dates are recorded automatically from this release onward and were backfilled for existing records.',
        'Customer left is the date the customer was marked inactive. Customers already inactive before that date '
        'was tracked have no recorded leave date, so they appear in the current count but not the trend.',
        'Revenue is invoice-date taxable value excluding GST; receipts use the actual receipt date and are not revenue.',
        'Active projects are a current snapshot. Billed projects count distinct projects appearing on invoices in the period.',
        'Live manpower counts deployments with an active or on-hold status that cover the reporting date.',
    ]
    return dict(cards=cards, charts=chart_list, rows=rows, notes=notes, total_revenue=total_revenue)
