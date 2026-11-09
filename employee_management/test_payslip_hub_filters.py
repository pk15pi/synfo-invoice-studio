"""Payslips hub: standard period picker filters by salary month."""
from datetime import date
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from employee_management import payroll as engine
from employee_management.models import EmployeePayroll, EmployeeSalaryStructure, PayrollPeriod
from employee_management.tests import Base, TMP, D
from employee_management import views_hubs as hubs


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class PayslipHubFilterTests(Base):
    def _finalize(self, year, month, emp=None):
        emp = emp or self.emp
        if not EmployeeSalaryStructure.objects.filter(employee=emp).exists():
            self.structure(emp=emp)
        period, _ = PayrollPeriod.objects.get_or_create(
            year=year, month=month, defaults={'divisor': 'actual'})
        engine.generate(period)
        slip = EmployeePayroll.objects.get(period=period, employee=emp, is_current=True)
        if slip.status != EmployeePayroll.FINALIZED:
            engine.finalize([slip])
            slip.refresh_from_db()
        return slip

    def test_no_params_redirects_to_previous_calendar_month(self):
        with mock.patch('employee_management.views_hubs.timezone.localdate', return_value=date(2026, 11, 9)):
            r = self.client.get(reverse('em:payslips'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('period_mode=calendar', r['Location'])
        self.assertIn('calendar_year=2026', r['Location'])
        self.assertIn('calendar_month=10', r['Location'])

    def test_previous_month_params_helper(self):
        self.assertEqual(
            hubs._previous_month_params(date(2026, 1, 5)),
            {'period_mode': 'calendar', 'calendar_year': '2025', 'calendar_month': '12'},
        )

    def test_calendar_month_filters_list(self):
        oct_slip = self._finalize(2026, 10)
        sep_slip = self._finalize(2026, 9)
        r = self.client.get(reverse('em:payslips'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Filter by')
        self.assertContains(r, reverse('em:payslip', args=[oct_slip.pk]))
        self.assertNotContains(r, reverse('em:payslip', args=[sep_slip.pk]))
        self.assertContains(r, 'Export Q1')
        self.assertContains(r, 'page_size')

    def test_financial_year_quarter_filters_list(self):
        apr = self._finalize(2026, 4)
        may = self._finalize(2026, 5)
        aug = self._finalize(2026, 8)
        r = self.client.get(reverse('em:payslips'), {
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q1',
        })
        self.assertContains(r, reverse('em:payslip', args=[apr.pk]))
        self.assertContains(r, reverse('em:payslip', args=[may.pk]))
        self.assertNotContains(r, reverse('em:payslip', args=[aug.pk]))

    def test_custom_date_range_filters_list(self):
        jun = self._finalize(2026, 6)
        jul = self._finalize(2026, 7)
        r = self.client.get(reverse('em:payslips'), {
            'period_mode': 'custom', 'date_from': '2026-06-01', 'date_to': '2026-06-30',
        })
        self.assertContains(r, reverse('em:payslip', args=[jun.pk]))
        self.assertNotContains(r, reverse('em:payslip', args=[jul.pk]))

    def test_apply_keeps_page_size(self):
        self._finalize(2026, 10)
        r = self.client.get(reverse('em:payslips'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
            'page_size': '50',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="page_size"')
        self.assertContains(r, 'value="50"')

    def test_legacy_fy_param_still_lists_and_exports(self):
        self._finalize(2026, 4)
        r = self.client.get(reverse('em:payslips'), {'fy': '2026'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'April 2026')
        self.assertContains(r, reverse('em:payslip_quarter_tds', args=[1]) + '?fy=2026')

    def test_export_fy_from_calendar_uses_period_end(self):
        from reporting import periods as report_periods
        period = report_periods.resolve_standard({
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertEqual(hubs._export_fy_start(period, {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        }), 2026)
        fy_period = report_periods.resolve_standard({
            'period_mode': 'financial', 'fy': '2025', 'period_type': 'full_fy',
        })
        self.assertEqual(hubs._export_fy_start(fy_period, {
            'period_mode': 'financial', 'fy': '2025',
        }), 2025)
