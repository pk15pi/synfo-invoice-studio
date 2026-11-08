from django.test import TestCase
from django.urls import reverse

from financial_reporting.models import ReportCategory, ReportDefinition


class FinancialReportingViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = ReportCategory.objects.get(code='accounting-reports')
        ReportDefinition.objects.create(
            report_code='future-gst', report_name='GST reconciliation', category=category,
            source_application='compliance', supported_period_types=['month'],
            supported_formats=['xlsx'], active_status=False,
        )

    def test_dashboard_and_catalogue_render(self):
        dashboard = self.client.get(reverse('financial_reporting:dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Financial Reports &amp; Audit Centre')
        # Period choice is the shared picker; the current FY is shown as the reporting period.
        from reporting import periods
        self.assertContains(dashboard, 'Reporting period')
        self.assertContains(dashboard, periods.fy_label(periods.fy_start_year(periods.today())))
        self.assertContains(dashboard, '<option value="financial" selected>Financial year</option>')

        catalogue = self.client.get(reverse('financial_reporting:catalogue'))
        self.assertEqual(catalogue.status_code, 200)
        self.assertContains(catalogue, 'Trial Balance')
        self.assertContains(catalogue, 'Open report')
        self.assertNotContains(catalogue, 'GST reconciliation')

    def test_catalogue_search_and_local_only_boundary(self):
        response = self.client.get(reverse('financial_reporting:catalogue'), {'q': 'trial'})
        self.assertContains(response, 'Trial Balance')
        self.assertNotContains(response, 'GST reconciliation')
        self.assertEqual(
            self.client.get(reverse('financial_reporting:dashboard'), REMOTE_ADDR='10.1.1.2').status_code,
            403,
        )

    def test_invalid_custom_range_is_reported_without_using_user_dates(self):
        response = self.client.get(reverse('financial_reporting:dashboard'), {
            'period': 'custom', 'date_from': '2026-07-02', 'date_to': '2026-07-01',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reporting period is invalid')
        self.assertContains(response, 'FY')
