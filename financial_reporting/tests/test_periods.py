from datetime import date

from django.test import SimpleTestCase

from reporting.periods import fy_range, resolve

from financial_reporting.forms import ReportingPeriodForm
from financial_reporting.services.period_service import financial_year_dates, persisted_period_type


class ReportingPeriodTests(SimpleTestCase):
    def test_indian_financial_year_boundaries(self):
        self.assertEqual(fy_range(2025), (date(2025, 4, 1), date(2026, 3, 31)))
        self.assertEqual(financial_year_dates(2026), (date(2026, 4, 1), date(2027, 3, 31)))

    def test_monthly_period_has_calendar_month_boundaries(self):
        period = resolve({'period': 'month', 'month': '2024-02'})
        self.assertEqual((period.start, period.end), (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(persisted_period_type(period), 'month')

    def test_quarter_and_half_year_use_indian_fy_boundaries(self):
        quarter = resolve({'period': 'fy', 'fy': '2025', 'fy_part': 'Q1'})
        half = resolve({'period': 'fy', 'fy': '2025', 'fy_part': 'H2'})
        self.assertEqual((quarter.start, quarter.end), (date(2025, 4, 1), date(2025, 6, 30)))
        self.assertEqual((half.start, half.end), (date(2025, 10, 1), date(2026, 3, 31)))
        self.assertEqual(persisted_period_type(quarter), 'quarter')
        self.assertEqual(persisted_period_type(half), 'half_year')

    def test_financial_year_and_custom_ranges(self):
        fy = resolve({'period': 'fy', 'fy': '2026'})
        custom = resolve({'period': 'custom', 'date_from': '2026-05-07', 'date_to': '2026-06-11'})
        self.assertEqual((fy.start, fy.end), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual((custom.start, custom.end), (date(2026, 5, 7), date(2026, 6, 11)))
        self.assertEqual(persisted_period_type(fy), 'financial_year')
        self.assertEqual(persisted_period_type(custom), 'custom')

    def test_period_form_requires_complete_and_ordered_ranges(self):
        self.assertFalse(ReportingPeriodForm({'period': 'custom', 'date_from': '2026-06-01'}).is_valid())
        self.assertFalse(ReportingPeriodForm({
            'period': 'custom', 'date_from': '2026-06-02', 'date_to': '2026-06-01',
        }).is_valid())
        form = ReportingPeriodForm({
            'period': 'custom', 'date_from': '2026-06-01', 'date_to': '2026-06-02',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.period_params()['date_from'], '2026-06-01')
