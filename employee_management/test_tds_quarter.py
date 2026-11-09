"""Quarterly CA salary TDS Excel export (Indian FY)."""
import io
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from employee_management import payroll as engine
from employee_management.exports import quarter_months, salary_tds_quarter_rows, salary_tds_quarter_xlsx
from employee_management.models import EmployeePayroll, EmployeeSalaryStructure, PayrollPeriod
from employee_management.tests import Base, TMP, D


class QuarterMonthTests(TestCase):
    def test_indian_fy_quarters_for_known_fy(self):
        self.assertEqual(quarter_months(2026, 1), [(2026, 4), (2026, 5), (2026, 6)])
        self.assertEqual(quarter_months(2026, 2), [(2026, 7), (2026, 8), (2026, 9)])
        self.assertEqual(quarter_months(2026, 3), [(2026, 10), (2026, 11), (2026, 12)])
        self.assertEqual(quarter_months(2026, 4), [(2027, 1), (2027, 2), (2027, 3)])


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class SalaryTdsQuarterExportTests(Base):
    def _ensure_structure(self, employee, basic='30000', hra='10000', tds='1000'):
        if EmployeeSalaryStructure.objects.filter(employee=employee).exists():
            return
        self.structure(emp=employee, basic=basic, hra=hra, other=[('deduction', 'tds', tds)])

    def _finalize_month(self, employee, year, month, basic='30000', hra='10000', tds='1000'):
        self._ensure_structure(employee, basic=basic, hra=hra, tds=tds)
        period, _ = PayrollPeriod.objects.get_or_create(
            year=year, month=month, defaults={'divisor': 'actual'})
        engine.generate(period)
        slip = EmployeePayroll.objects.get(period=period, employee=employee, is_current=True)
        if slip.status != EmployeePayroll.FINALIZED:
            engine.finalize([slip])
            slip.refresh_from_db()
        return slip

    def test_three_months_one_employee_one_row(self):
        self.emp.pan_number = 'ABCDE1234F'
        self.emp.save()
        for month in (4, 5, 6):
            self._finalize_month(self.emp, 2026, month)
        headers, rows, *_rest = salary_tds_quarter_rows(2026, 1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[0], self.emp.employee_id)
        self.assertEqual(row[2], 'ABCDE1234F')
        q_gross = Decimal(str(row[headers.index('Gross Salary (Quarter)')]))
        self.assertEqual(q_gross, Decimal(str(row[5])) + Decimal(str(row[7])) + Decimal(str(row[9])))

    def test_two_employees_two_rows(self):
        other = self.make_emp(first_name='Bina', pan_number='BBBBE1234B')
        self.emp.pan_number = 'AAAAA1234A'
        self.emp.save()
        self._ensure_structure(self.emp, tds='500')
        self._ensure_structure(other, tds='500')
        self._finalize_month(self.emp, 2026, 7, tds='500')
        self._finalize_month(other, 2026, 7, tds='500')
        _headers, rows, *_rest = salary_tds_quarter_rows(2026, 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[2] for row in rows}, {'AAAAA1234A', 'BBBBE1234B'})

    def test_missing_month_leaves_blank_not_extra_row(self):
        self.emp.pan_number = 'CCCCC1234C'
        self.emp.save()
        self._finalize_month(self.emp, 2026, 4)
        _headers, rows, *_rest = salary_tds_quarter_rows(2026, 1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotEqual(row[5], '')
        self.assertEqual(row[7], '')
        self.assertEqual(row[8], '')
        self.assertEqual(row[9], '')
        self.assertEqual(row[10], '')

    def test_revision_does_not_duplicate_or_double(self):
        self.emp.pan_number = 'DDDDD1234D'
        self.emp.save()
        slip = self._finalize_month(self.emp, 2026, 10, basic='30000', hra='0', tds='0')
        first_gross = slip.gross_earnings
        rev = engine.start_revision(slip, 'fix')
        inputs = {field: getattr(rev, field) for field in EmployeePayroll.INPUT_FIELDS}
        inputs['lwp_days'] = D('5')
        inputs['payable_days'] = rev.divisor_days - D('5')
        engine.recalculate(rev, inputs)
        engine.finalize([rev])
        rev.refresh_from_db()
        slip.refresh_from_db()
        self.assertFalse(slip.is_current)
        self.assertTrue(rev.is_current)
        self.assertEqual(
            EmployeePayroll.objects.filter(employee=self.emp, period=slip.period, status='finalized').count(), 2)
        headers, rows, *_rest = salary_tds_quarter_rows(2026, 3)
        self.assertEqual(len(rows), 1)
        q_gross = Decimal(str(rows[0][headers.index('Gross Salary (Quarter)')]))
        self.assertEqual(q_gross, rev.gross_earnings)
        self.assertNotEqual(q_gross, first_gross + rev.gross_earnings)

    def test_empty_quarter_still_downloads_headers(self):
        data, filename = salary_tds_quarter_xlsx(2026, 4)
        self.assertTrue(filename.startswith('Salary_TDS_Q4_FY2026-27'))
        wb = load_workbook(io.BytesIO(data))
        ws = wb.active
        self.assertEqual(ws['A1'].value, 'Employee ID')
        self.assertEqual(ws['C1'].value, 'PAN')
        self.assertIsNone(ws['A2'].value)

    def test_buttons_and_download_hide_pan_on_html(self):
        self.emp.pan_number = 'EEEEE1234E'
        self.emp.save()
        self._finalize_month(self.emp, 2026, 4)
        page = self.client.get(reverse('em:payslips') + '?fy=2026')
        self.assertEqual(page.status_code, 200)
        for label in ('Export Q1', 'Export Q2', 'Export Q3', 'Export Q4'):
            self.assertContains(page, label)
        self.assertNotContains(page, 'EEEEE1234E')
        self.assertContains(page, reverse('em:payslip_quarter_tds', args=[1]))
        resp = self.client.get(reverse('em:payslip_quarter_tds', args=[1]) + '?fy=2026')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])
        self.assertIn('Salary_TDS_Q1_FY2026-27.xlsx', resp['Content-Disposition'])
        wb = load_workbook(io.BytesIO(resp.content))
        values = [cell.value for row in wb.active.iter_rows(min_row=2, max_row=2) for cell in row]
        self.assertIn('EEEEE1234E', values)
