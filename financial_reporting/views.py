from django.contrib import messages
from django.core.paginator import Paginator
from invoice_app.pagination import paginate as qb_paginate
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.http import urlencode

from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort, apply_sequence_sort
from reporting import periods
from reporting.exports import period_workbook

from .forms import ReportingPeriodForm
from .models import (
    AuditChecklistResult,
    ExportPackage,
    ReportCategory,
    ReportDefinition,
    ReportRun,
)
from .services.report_registry import active_reports


@local_only
def dashboard(request):
    initial_period = {'period_mode': 'financial', 'fy': str(periods.fy_start_year(periods.today())),
                      'period_type': 'full_fy'}
    form = ReportingPeriodForm(request.GET or None)
    if request.GET and not form.is_valid():
        messages.error(request, 'The reporting period is invalid. Review the selected dates and try again.')
        form = ReportingPeriodForm(initial=initial_period)
    if form.is_bound and form.is_valid():
        period = periods.resolve(form.period_params())
    else:
        period = periods.resolve(initial_period)

    if request.GET.get('export') == 'xlsx' and form.is_bound and form.is_valid():
        response = HttpResponse(
            period_workbook(period),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = f'attachment; filename="{slugify(f"financial-report-{period.label}")}.xlsx"'
        return response

    return render(request, 'financial_reporting/dashboard.html', {
        'period_form': form,
        'period': period,
        'active_categories': ReportCategory.objects.filter(active_status=True),
        'report_count': ReportDefinition.objects.filter(active_status=True).count(),
        'recent_runs': ReportRun.objects.select_related('report_definition')[:5],
        'recent_packages': ExportPackage.objects.select_related('financial_period')[:5],
        'pending_checks': AuditChecklistResult.objects.filter(
            status__in=[AuditChecklistResult.PENDING, AuditChecklistResult.WARNING, AuditChecklistResult.FAILED],
        ).count(),
    })


@local_only
def catalogue(request):
    initial_period = {'period_mode': 'financial', 'fy': str(periods.fy_start_year(periods.today())),
                      'period_type': 'full_fy'}
    period_form = ReportingPeriodForm(request.GET or None)
    if request.GET and not period_form.is_valid():
        messages.error(request, 'The reporting period is invalid. Review the selected dates and try again.')
        period_form = ReportingPeriodForm(initial=initial_period)
    period = periods.resolve(period_form.period_params()) if period_form.is_bound and period_form.is_valid() \
        else periods.resolve(initial_period)
    period_type = {
        'month': 'month', 'quarter': 'quarter', 'half': 'half_year',
        'fy': 'financial_year', 'year': 'annual',
    }.get(period.kind, 'custom')

    q = request.GET.get('q', '').strip()[:100]
    category = request.GET.get('category', '').strip()[:40]
    report_rows = active_reports(query=q, category=category, period_type=period_type)
    fields = {
        'report': ('report_name', 'text'), 'category': ('category__name', 'text'),
        'source': ('source_application', 'text'), 'formats': ('supported_formats', 'text'),
    }
    if hasattr(report_rows, 'order_by'):
        report_rows, _, _ = apply_queryset_sort(report_rows, request, {k: v[0] for k, v in fields.items()})
    else:
        report_rows, _, _ = apply_sequence_sort(report_rows, request, fields)
    page = qb_paginate(request, report_rows)
    page_query = request.GET.copy()
    page_query.pop('page', None)

    for report in page.object_list:
        if report.applicability_rules.get('legacy_report_key') == report.report_code:
            query = urlencode(period.params)
            report.existing_report_url = (
                f'{reverse("reporting:report", args=[report.report_code])}?{query}'
            )
        else:
            report.existing_report_url = ''

    return render(request, 'financial_reporting/catalogue.html', {
        'period_form': period_form,
        'period': period,
        'page': page,
        'categories': ReportCategory.objects.filter(active_status=True),
        'query': q,
        'selected_category': category,
        'selected_period_type': period_type,
        'page_query': page_query.urlencode(),
    })
