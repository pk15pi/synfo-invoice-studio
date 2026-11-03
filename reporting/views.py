from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from employee_management.models import Department, Employee
from employee_management.security import local_only
from expense_management.models import CompanyExpense, EmployeeExpenseClaim, ExpenseCategory, Vendor
from invoice_app.models import Client, ProjectTemplate

from . import builder as B
from . import exports
from . import periods as P
from . import reports as R
from . import services as S
from .core import text
from .dashboard import executive

PAGE = 50


def _ctx(request, **kw):
    q = request.GET.copy()
    q.pop('page', None)
    q.pop('format', None)
    # The selector must reflect the period actually applied (e.g. ?fy=2025 alone resolves to 'fy', not the first
    # option 'Today'), so the form state is rebuilt from the shared resolver.
    period = P.resolve(request.GET)
    g = request.GET.dict()
    g.update(period.params)
    g['period'] = period.key
    c = dict(sections=R.SECTIONS, period_options=P.OPTIONS, fy_parts=P.FY_PARTS, fy_choices=P.fy_choices(),
             g=g, qs=q.urlencode(), period_label=period.label, period_range=period.range_text)
    c.update(kw)
    return c


def _respond(request, rep, template='reporting/report.html', **extra):
    fmt = request.GET.get('format')
    name = slugify(f'{rep.title} {rep.period.label}')
    if fmt == 'xlsx':
        r = HttpResponse(exports.report_xlsx(rep),
                         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = f'attachment; filename="{name}.xlsx"'
        return r
    if fmt == 'csv':
        r = HttpResponse(exports.report_csv(rep), content_type='text/csv; charset=utf-8')
        r['Content-Disposition'] = f'attachment; filename="{name}.csv"'
        return r
    if fmt == 'pdf':
        r = HttpResponse(exports.report_pdf(rep), content_type='application/pdf')
        r['Content-Disposition'] = f'attachment; filename="{name}.pdf"'
        return r
    page_no = request.GET.get('page') or 1
    tables = []
    for t in rep.tables:
        rows = [dict(cells=[dict(v=text(c, col.kind), url=c[1] if isinstance(c, tuple) else '',
                                 num=col.kind in ('money', 'int', 'pct'))
                            for c, col in zip(r_, t.cols)], style=s) for r_, s in zip(t.rows, t.styles)]
        page = None
        if t.paginate and len(rows) > PAGE:
            page = Paginator(rows, PAGE).get_page(page_no)
            rows = list(page.object_list)
        tables.append(dict(t=t, rows=rows, page=page,
                           total=[dict(v=text(c, col.kind), num=col.kind in ('money', 'int', 'pct'))
                                  for c, col in zip(t.total, t.cols)] if t.total else None))
    cards = [dict(c, text=text(c['value'], c['kind']) if c['value'] is not None else 'Not tracked',
                  prev_text=text(c['prev'], c['kind']) if c.get('prev') is not None else '') for c in rep.cards]
    return render(request, template, _ctx(request, rep=rep, tables=tables, cards=cards, **extra))


def _choices(key):
    f = R.FILTERS.get(key, [])
    out = {}
    if 'client' in f:
        out['clients'] = Client.objects.order_by('name').values_list('id', 'name')
    if 'project' in f:
        out['projects'] = ProjectTemplate.objects.order_by('name').values_list('id', 'name')
    if 'department' in f:
        out['departments'] = Department.objects.order_by('name').values_list('id', 'name')
    if 'employee' in f:
        out['employees'] = [(e.pk, f'{e.employee_id} {e.full_name}') for e in Employee.objects.order_by('first_name')]
    if 'account' in f:
        from accounting.models import Account
        out['accounts'] = Account.objects.filter(is_group=False).order_by('code').values_list('id', 'code', 'name')
    if 'vendor' in f:
        out['vendors'] = Vendor.objects.order_by('name').values_list('id', 'name')
    if 'category' in f:
        out['categories'] = ExpenseCategory.objects.order_by('name').values_list('id', 'name')
    out['emp_statuses'] = Employee.STATUSES
    out['exp_statuses'] = CompanyExpense._meta.get_field('payment_status').choices
    out['claim_statuses'] = EmployeeExpenseClaim.STATUSES
    out['filters'] = f
    out['group_opts'] = ['vendor', 'category']
    out['trend_opts'] = ['month', 'quarter', 'year']
    return out


@local_only
def dashboard(request):
    period = P.resolve(request.GET)
    ex = executive(period)
    if request.GET.get('format') == 'xlsx':
        r = HttpResponse(exports.fy_workbook(P.fy_start_year(period.start)),
                         content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = f'attachment; filename="fy-report-{P.fy_label(P.fy_start_year(period.start))}.xlsx"'
        return r
    for c in ex['cards']:
        c['text'] = text(c['value'], c['kind']) if c['value'] is not None else 'Not tracked'
        c['prev_text'] = text(c['prev'], c['kind']) if c.get('prev') is not None else ''
    return render(request, 'reporting/dashboard.html', _ctx(request, period=period, ex=ex))


@local_only
def section(request, slug):
    name = R.SECTION_SLUGS.get(slug)
    if not name:
        raise Http404
    items = dict(R.SECTIONS)[name]
    return render(request, 'reporting/section.html', _ctx(request, name=name, items=items))


@local_only
def report(request, key):
    if key not in R.REGISTRY:
        raise Http404
    rep = R.build(key, request.GET)
    return _respond(request, rep, key=key, no_period=key in R.NO_PERIOD, **_choices(key))


@local_only
def custom(request):
    rep, ds, fields, picked, group, texts = B.build(request.GET)
    return _respond(request, rep, 'reporting/builder.html', datasets=[(k, v[0]) for k, v in B.DATASETS.items()],
                    ds=ds, fields=fields, picked=picked, group=group, texts=texts)


@local_only
def fy_workbook(request):
    y = request.GET.get('fy')
    y = int(y) if str(y).isdigit() else P.fy_start_year(P.today())
    r = HttpResponse(exports.fy_workbook(y),
                     content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    r['Content-Disposition'] = f'attachment; filename="fy-report-{P.fy_label(y)}.xlsx"'
    return r


@local_only
def sources(request):
    return render(request, 'reporting/sources.html', _ctx(request, sources_=S.SOURCES, limitations=S.LIMITATIONS,
                                                          group_rules=S.GROUP_RULES_TEXT))
