import csv
import json
from datetime import datetime

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort

from .models import OPERATOR_TEXT, AuditEvent

FILTERS = ('app', 'model', 'object_id', 'action', 'source', 'date_from', 'date_to', 'q')


def _filtered(request):
    g = {k: request.GET.get(k, '').strip() for k in FILTERS}
    qs = AuditEvent.objects.all()
    if g['app']:
        qs = qs.filter(app_label=g['app'])
    if g['model']:
        qs = qs.filter(model__iexact=g['model'])
    if g['object_id']:
        qs = qs.filter(object_id=g['object_id'])
    if g['action']:
        qs = qs.filter(action=g['action'])
    if g['source']:
        qs = qs.filter(source=g['source'])
    for k, op in (('date_from', 'gte'), ('date_to', 'lte')):
        if g[k]:
            try:
                qs = qs.filter(**{f'timestamp__date__{op}': datetime.strptime(g[k], '%Y-%m-%d').date()})
            except ValueError:
                pass
    if g['q']:
        qs = qs.filter(object_repr__icontains=g['q']) | qs.filter(reason__icontains=g['q'])
    return qs, g


def _ctx(qs, g, request, **extra):
    qs, _, _ = apply_queryset_sort(qs, request, {
        'timestamp': 'timestamp', 'source': 'source', 'object': 'object_repr',
        'action': 'action', 'reason': 'reason', 'path': 'request_path',
    })
    page = Paginator(qs, 100).get_page(request.GET.get('page'))
    qd = request.GET.copy()
    qd.pop('page', None)
    return dict(page=page, f=g, operator=OPERATOR_TEXT, actions=AuditEvent.ACTIONS, sources=AuditEvent.SOURCES,
                apps=AuditEvent.objects.order_by().values_list('app_label', flat=True).distinct(),
                qs=qd.urlencode(), **extra)


@local_only
def log(request):
    qs, g = _filtered(request)
    return render(request, 'audit/log.html', _ctx(qs, g, request))


@local_only
def history(request, app_label, model, object_id):
    qs = AuditEvent.objects.filter(app_label=app_label, model__iexact=model, object_id=object_id)
    g = {k: '' for k in FILTERS}
    return render(request, 'audit/log.html', _ctx(qs, g, request, obj=f'{app_label}.{model} #{object_id}'))


@local_only
def export_csv(request):
    qs, _ = _filtered(request)
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="audit-log-{timezone.localtime():%Y%m%d-%H%M%S}.csv"'
    w = csv.writer(resp)
    w.writerow(['timestamp', 'operator', 'source', 'app', 'model', 'object_id', 'object', 'action', 'changes',
                'reason', 'request_path'])
    for e in qs.iterator():
        w.writerow([timezone.localtime(e.timestamp).isoformat(), OPERATOR_TEXT, e.source, e.app_label, e.model,
                    e.object_id, e.object_repr, e.action, json.dumps(e.changes, ensure_ascii=False), e.reason,
                    e.request_path])
    return resp
