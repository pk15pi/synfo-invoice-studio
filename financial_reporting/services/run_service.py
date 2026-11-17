"""Report execution service: creates a ReportRun, calls the reporting app, saves output."""
import traceback
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from financial_reporting.models import ReportRun
from financial_reporting.services.period_service import get_or_create_period


def _period_params(period):
    """Build a params dict from a reporting.periods.Period (or FinancialPeriod) object."""
    # If called with a reporting Period object it already has .params
    if hasattr(period, 'params'):
        return dict(period.params)
    # Called with a FinancialPeriod model: reconstruct minimal params
    return {
        'date_from': period.start_date.isoformat(),
        'date_to': period.end_date.isoformat(),
        'period': 'custom',
    }


def _build_report(key, params):
    """Call reporting.reports.build() for management-report keys."""
    from reporting.reports import build
    return build(key, params)


def _build_ledger_report(key, params):
    """Call the matching ledger function from reporting.ledger_reports."""
    import reporting.ledger_reports as LR
    from reporting import periods as P
    period = P.resolve(params)
    registry = {item[0]: item[2] for item in LR.ITEMS}
    if key not in registry:
        raise KeyError(f'Unknown ledger report key: {key}')
    return registry[key](params, period)


def _report_to_xlsx(rep):
    from reporting.exports import report_xlsx
    return report_xlsx(rep)


def _report_to_csv(rep):
    from reporting.exports import report_csv
    return report_csv(rep)


def _report_to_pdf(rep):
    from reporting.exports import report_pdf
    return report_pdf(rep)


def _render_report(rep, output_format):
    if output_format == 'xlsx':
        return _report_to_xlsx(rep)
    if output_format == 'csv':
        return _report_to_csv(rep)
    if output_format == 'pdf':
        return _report_to_pdf(rep)
    # Fallback: xlsx
    return _report_to_xlsx(rep)


def _count_rows(rep):
    total = 0
    for t in getattr(rep, 'tables', []):
        total += len(getattr(t, 'rows', []))
    return total


def execute_report_run(report_definition, period, output_format, user):
    """
    Create and execute a ReportRun for `report_definition` over `period`.

    Args:
        report_definition: a ReportDefinition instance.
        period: a reporting.periods.Period object (or a FinancialPeriod model).
        output_format: one of 'pdf', 'xlsx', 'csv'.
        user: the requesting user (stored for auditing; unused in this synchronous version).

    Returns:
        The ReportRun instance with status set to 'succeeded' or 'failed'.
    """
    # Resolve the FinancialPeriod ORM row
    from reporting import periods as P
    if hasattr(period, 'start') and hasattr(period, 'end'):
        # reporting.periods.Period object
        rp_period = period
        fp = get_or_create_period(rp_period)
        from_date = rp_period.start
        to_date = rp_period.end
        params = dict(rp_period.params)
    else:
        # FinancialPeriod model instance
        fp = period
        from_date = period.start_date
        to_date = period.end_date
        params = {
            'date_from': from_date.isoformat(),
            'date_to': to_date.isoformat(),
            'period': 'custom',
        }

    run = ReportRun.objects.create(
        report_definition=report_definition,
        financial_period=fp,
        from_date=from_date,
        to_date=to_date,
        output_format=output_format or 'xlsx',
        status=ReportRun.RUNNING,
        generated_at=timezone.now(),
        source_data_as_of=timezone.now(),
    )

    key = report_definition.applicability_rules.get('legacy_report_key', '')
    service = report_definition.source_model_or_service

    try:
        if service == 'reporting.reports':
            if not key:
                raise ValueError('No legacy_report_key in applicability_rules.')
            rep = _build_report(key, params)
        elif service == 'reporting.ledger_reports':
            if not key:
                raise ValueError('No legacy_report_key in applicability_rules.')
            rep = _build_ledger_report(key, params)
        else:
            # Unknown service — mark incomplete rather than fail
            run.status = ReportRun.INCOMPLETE
            run.error_details = (
                f'source_model_or_service "{service}" is not handled by run_service. '
                'Implement a handler to generate output for this report.'
            )
            run.generated_at = timezone.now()
            run.save(update_fields=['status', 'error_details', 'generated_at'])
            return run

        # Render to bytes
        file_bytes = _render_report(rep, run.output_format)
        ext = run.output_format if run.output_format in ('pdf', 'xlsx', 'csv') else 'bin'

        # Save file via private_storage through the FileField
        content = ContentFile(file_bytes, name=f'placeholder.{ext}')
        run.generated_file.save(f'report.{ext}', content, save=False)

        run.record_count = _count_rows(rep)
        run.status = ReportRun.SUCCEEDED
        run.generated_at = timezone.now()
        run.save(update_fields=['generated_file', 'record_count', 'status', 'generated_at'])

    except Exception:
        run.status = ReportRun.FAILED
        run.error_details = traceback.format_exc()
        run.generated_at = timezone.now()
        run.save(update_fields=['status', 'error_details', 'generated_at'])

    return run
