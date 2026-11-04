from django.db.models import Q

from financial_reporting.models import ReportDefinition


def active_reports(*, query='', category='', period_type=''):
    reports = ReportDefinition.objects.filter(active_status=True).select_related('category')
    if query:
        reports = reports.filter(
            Q(report_code__icontains=query)
            | Q(report_name__icontains=query)
            | Q(description__icontains=query)
        )
    if category:
        reports = reports.filter(category__code=category)
    if period_type:
        reports = [report for report in reports if period_type in report.supported_period_types]
    return reports
