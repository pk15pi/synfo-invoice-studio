from django.contrib import messages
from django.core.paginator import Paginator
from invoice_app.pagination import paginate as qb_paginate
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort, apply_sequence_sort
from reporting import periods
from reporting.exports import period_workbook

from .forms import ComplianceSubmissionForm, ReportingPeriodForm, RunReportForm
from .models import (
    AuditChecklist,
    AuditChecklistResult,
    ComplianceSubmission,
    ExportPackage,
    ReconciliationRun,
    ReportCategory,
    ReportDefinition,
    ReportRun,
)
from .services.audit_service import evaluate_audit_readiness, get_readiness_for_period
from .services.reconciliation_service import RECONCILIATION_TYPES, execute_reconciliation
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

    # Resolve the FinancialPeriod ORM row for audit readiness
    from .services.period_service import get_or_create_period
    fp = get_or_create_period(period)
    readiness = get_readiness_for_period(fp)

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
        'readiness_score': readiness['score'],
        'readiness_label': readiness['label'],
        'financial_period': fp,
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


# ---------------------------------------------------------------------------
# Report runs
# ---------------------------------------------------------------------------

@local_only
def run_report(request):
    """GET: show form to pick report + period + format.  POST: execute and redirect."""
    initial_fy = str(periods.fy_start_year(periods.today()))
    if request.method == 'POST':
        form = RunReportForm(request.POST)
        if form.is_valid():
            from reporting import periods as P
            from .services.run_service import execute_report_run
            params = form.period_params()
            period = P.resolve(params)
            run = execute_report_run(
                form.cleaned_data['report_definition'],
                period,
                form.cleaned_data['output_format'],
                request.user,
            )
            if run.status == ReportRun.FAILED:
                messages.error(request, f'Report generation failed: {run.error_details[:200]}')
            elif run.status == ReportRun.INCOMPLETE:
                messages.warning(request, f'Report incomplete: {run.error_details[:200]}')
            else:
                messages.success(request, 'Report generated successfully.')
            return redirect(reverse('financial_reporting:report_run_detail', args=[run.pk]))
    else:
        form = RunReportForm(initial={'period_mode': 'financial', 'fy': initial_fy, 'period_type': 'full_fy'})
    return render(request, 'financial_reporting/run_report.html', {'form': form})


@local_only
def report_run_list(request):
    runs = ReportRun.objects.select_related('report_definition').order_by('-generated_at', '-id')
    status_filter = request.GET.get('status', '').strip()
    report_filter = request.GET.get('report', '').strip()
    if status_filter:
        runs = runs.filter(status=status_filter)
    if report_filter and report_filter.isdigit():
        runs = runs.filter(report_definition_id=int(report_filter))
    sort_fields = {
        'report': 'report_definition__report_name',
        'date': 'generated_at',
        'status': 'status',
        'format': 'output_format',
    }
    runs, sort_key, sort_dir = apply_queryset_sort(runs, request, sort_fields)
    page = qb_paginate(request, runs)
    definitions = ReportDefinition.objects.filter(active_status=True).order_by('report_name')
    return render(request, 'financial_reporting/report_run_list.html', {
        'page': page,
        'status_choices': ReportRun.STATUSES,
        'status_filter': status_filter,
        'report_filter': report_filter,
        'definitions': definitions,
        'sort_key': sort_key,
        'sort_dir': sort_dir,
    })


@local_only
def report_run_detail(request, pk):
    run = get_object_or_404(ReportRun.objects.select_related('report_definition', 'financial_period'), pk=pk)
    return render(request, 'financial_reporting/report_run_detail.html', {'run': run})


@local_only
def report_run_download(request, pk):
    run = get_object_or_404(ReportRun, pk=pk)
    if not run.generated_file or not run.generated_file.name:
        raise Http404('No file is available for this report run.')
    ext = run.output_format or 'bin'
    content_types = {
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'pdf': 'application/pdf',
        'csv': 'text/csv',
        'zip': 'application/zip',
    }
    ct = content_types.get(ext, 'application/octet-stream')
    fname = slugify(f'{run.report_definition.report_code}-{run.from_date}-{run.to_date}') + f'.{ext}'
    response = FileResponse(run.generated_file.open('rb'), content_type=ct)
    response['Content-Disposition'] = f'attachment; filename="{fname}"'
    return response


# ---------------------------------------------------------------------------
# Export packages
# ---------------------------------------------------------------------------

@local_only
def package_list(request):
    packages = ExportPackage.objects.select_related('financial_period').order_by('-created_at', '-id')
    page = qb_paginate(request, packages)
    return render(request, 'financial_reporting/package_list.html', {'page': page})


@local_only
def package_detail(request, pk):
    package = get_object_or_404(
        ExportPackage.objects.select_related('financial_period').prefetch_related(
            'items__report_run__report_definition', 'items__source_document',
        ),
        pk=pk,
    )
    return render(request, 'financial_reporting/package_detail.html', {'package': package})


@local_only
@require_POST
def package_generate(request, pk):
    package = get_object_or_404(ExportPackage, pk=pk)
    from .services.package_service import generate_package
    package = generate_package(package, request.user)
    if package.generation_status == ExportPackage.FAILED:
        messages.error(request, 'Package generation failed. See manifest for details.')
    elif package.generation_status == ExportPackage.INCOMPLETE:
        messages.warning(request, 'Package generated with some errors. See manifest for details.')
    else:
        messages.success(request, 'Export package generated successfully.')
    return redirect(reverse('financial_reporting:package_detail', args=[pk]))


@local_only
def package_download(request, pk):
    package = get_object_or_404(ExportPackage, pk=pk)
    if not package.zip_file or not package.zip_file.name:
        raise Http404('No ZIP file is available for this package.')
    fname = slugify(f'export-package-{package.package_number}') + '.zip'
    response = FileResponse(package.zip_file.open('rb'), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{fname}"'
    return response


# ---------------------------------------------------------------------------
# Reconciliations
# ---------------------------------------------------------------------------

@local_only
def reconciliation_list(request):
    runs = ReconciliationRun.objects.select_related('financial_period').order_by('-generated_at', '-id')
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        runs = runs.filter(status=status_filter)
    page = qb_paginate(request, runs)
    return render(request, 'financial_reporting/reconciliation_list.html', {
        'page': page,
        'status_choices': ReconciliationRun.STATUSES,
        'status_filter': status_filter,
        'reconciliation_types': RECONCILIATION_TYPES,
    })


@local_only
@require_POST
def reconciliation_run(request):
    """Execute a new reconciliation run and redirect to the list."""
    rec_type = request.POST.get('reconciliation_type', '').strip()
    valid_types = {k for k, _ in RECONCILIATION_TYPES}
    if rec_type not in valid_types:
        messages.error(request, 'Invalid reconciliation type.')
        return redirect(reverse('financial_reporting:reconciliation_list'))

    initial_period = {'period_mode': 'financial', 'fy': str(periods.fy_start_year(periods.today())),
                      'period_type': 'full_fy'}
    form = ReportingPeriodForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid reporting period — could not run reconciliation.')
        return redirect(reverse('financial_reporting:reconciliation_list'))

    period = periods.resolve(form.period_params())
    run = execute_reconciliation(rec_type, period, request.user)

    if run.status == ReconciliationRun.FAILED:
        messages.error(request, f'Reconciliation failed: {run.error_details[:300]}')
    elif run.unmatched_records:
        messages.warning(
            request,
            f'Reconciliation complete — {run.unmatched_records} of {run.total_records} '
            f'check{"s" if run.total_records != 1 else ""} have differences.',
        )
    else:
        messages.success(request, 'Reconciliation complete — all checks passed.')

    return redirect(reverse('financial_reporting:reconciliation_list'))


# ---------------------------------------------------------------------------
# Audit checklist
# ---------------------------------------------------------------------------

@local_only
def audit_checklist(request):
    results = (
        AuditChecklistResult.objects
        .select_related('checklist', 'financial_period')
        .order_by('checklist__display_order', 'checklist__name', '-checked_at')
    )
    # Group by checklist
    grouped = {}
    for r in results:
        grouped.setdefault(r.checklist, []).append(r)

    # Latest score across all periods
    all_results = list(results)
    from .services.audit_service import _score, score_label
    score = _score(all_results) if all_results else None
    label = score_label(score) if score is not None else 'Not assessed'

    return render(request, 'financial_reporting/audit_checklist.html', {
        'grouped': grouped,
        'status_choices': AuditChecklistResult.STATUSES,
        'readiness_score': score,
        'readiness_label': label,
        'checklist_count': AuditChecklist.objects.filter(active_status=True).count(),
    })


@local_only
@require_POST
def audit_evaluate(request):
    """Run all automated audit checks for the given financial period."""
    from .services.period_service import get_or_create_period
    form = ReportingPeriodForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid reporting period — could not evaluate audit readiness.')
        return redirect(reverse('financial_reporting:audit_checklist'))

    period = periods.resolve(form.period_params())
    fp = get_or_create_period(period)
    result = evaluate_audit_readiness(fp)

    if result['errors']:
        messages.warning(
            request,
            f'Evaluation completed with {len(result["errors"])} error(s). '
            f'Score: {result["score"]}% — {result["label"]}.',
        )
    else:
        messages.success(
            request,
            f'Audit readiness evaluated: {result["score"]}% — {result["label"]}.',
        )
    return redirect(reverse('financial_reporting:audit_checklist'))


# ---------------------------------------------------------------------------
# Compliance submissions
# ---------------------------------------------------------------------------

@local_only
def compliance_submissions(request):
    submissions = (
        ComplianceSubmission.objects
        .select_related('compliance_template', 'reporting_period')
        .order_by('-reporting_period__start_date', '-created_at')
    )
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        submissions = submissions.filter(status=status_filter)
    page = qb_paginate(request, submissions)
    return render(request, 'financial_reporting/compliance_submissions.html', {
        'page': page,
        'status_choices': ComplianceSubmission.STATUSES,
        'status_filter': status_filter,
    })


@local_only
def compliance_submission_edit(request, pk):
    submission = get_object_or_404(
        ComplianceSubmission.objects.select_related('compliance_template', 'reporting_period'),
        pk=pk,
    )
    if request.method == 'POST':
        form = ComplianceSubmissionForm(request.POST, request.FILES, instance=submission)
        if form.is_valid():
            form.save()
            messages.success(request, 'Submission updated.')
            return redirect(reverse('financial_reporting:compliance_submissions'))
    else:
        form = ComplianceSubmissionForm(instance=submission)
    return render(request, 'financial_reporting/compliance_submission_edit.html', {
        'form': form,
        'submission': submission,
    })


@local_only
@require_POST
def compliance_submission_file(request, pk):
    """Transition a submission to 'filed' status, recording the filing date."""
    submission = get_object_or_404(ComplianceSubmission, pk=pk)
    if submission.status == ComplianceSubmission.FILED:
        messages.info(request, 'This submission is already marked as filed.')
        return redirect(reverse('financial_reporting:compliance_submission_edit', args=[pk]))

    # filing_date defaults to today if not provided
    filing_date_raw = request.POST.get('filing_date', '').strip()
    if filing_date_raw:
        from django.utils.dateparse import parse_date
        filing_date = parse_date(filing_date_raw)
        if not filing_date:
            messages.error(request, 'Invalid filing date format — use YYYY-MM-DD.')
            return redirect(reverse('financial_reporting:compliance_submission_edit', args=[pk]))
    else:
        filing_date = timezone.now().date()

    ack_number = request.POST.get('acknowledgement_number', '').strip()[:120]

    submission.status = ComplianceSubmission.FILED
    submission.filing_date = filing_date
    if ack_number:
        submission.acknowledgement_number = ack_number
    update_fields = ['status', 'filing_date', 'updated_at']
    if ack_number:
        update_fields.append('acknowledgement_number')
    submission.save(update_fields=update_fields)

    messages.success(
        request,
        f'Submission marked as filed on {filing_date.strftime("%d %b %Y")}.'
        + (f' Acknowledgement: {ack_number}.' if ack_number else ''),
    )
    return redirect(reverse('financial_reporting:compliance_submissions'))
