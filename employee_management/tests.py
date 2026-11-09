import os
import io
import shutil
import tempfile
import zipfile
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from . import payroll as engine
from .models import (Department, EmergencyContact, Employee, EmployeeBankAccount, EmployeeDocument,
                     EmployeePayroll, EmployeeSalaryStructure, FinalizedRecordError, PayrollPeriod,
                     PayrollSettings, SalaryComponent, StatutoryRule)
from .numwords import rupees_in_words
from .pdf.payslip import payslip_filename
from .validators import mask

PDF = b'%PDF-1.4\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
TMP = tempfile.mkdtemp(prefix='qb_private_test_')


def D(x):
    return Decimal(str(x))


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.emp = self.make_emp()

    def make_emp(self, **kw):
        data = dict(first_name='Asha', last_name='Rao', department=self.dept, designation='Engineer',
                    date_of_joining=date(2025, 1, 1), status=Employee.CONFIRMED)
        data.update(kw)
        return Employee.objects.create(**data)

    def structure(self, emp=None, eff=date(2025, 1, 1), basic='30000', hra='12000', other=None, **kw):
        st = EmployeeSalaryStructure.objects.create(employee=emp or self.emp, effective_from=eff, **kw)
        SalaryComponent.objects.create(structure=st, kind='earning', code='basic', amount=D(basic))
        if hra:
            SalaryComponent.objects.create(structure=st, kind='earning', code='hra', amount=D(hra))
        for kind, code, amt in other or []:
            SalaryComponent.objects.create(structure=st, kind=kind, code=code, amount=D(amt))
        return st

    def period(self, y=2026, m=11, divisor='actual'):
        return PayrollPeriod.objects.create(year=y, month=m, divisor=divisor)


class EmployeeIdTests(Base):
    def test_auto_ids_sequential_and_prefix(self):
        self.assertEqual(self.emp.employee_id, 'EMP001')
        self.assertEqual(self.make_emp(first_name='B').employee_id, 'EMP002')
        s = PayrollSettings.current()
        s.employee_id_prefix, s.employee_id_padding = 'QB', 4
        s.save()
        self.assertEqual(self.make_emp(first_name='C').employee_id, 'QB0003')

    def test_manual_id_respected_and_skipped(self):
        self.make_emp(employee_id='EMP002', first_name='M')
        self.assertEqual(self.make_emp(first_name='N').employee_id, 'EMP003')

    def test_manual_id_unique_via_form(self):
        r = self.client.post(reverse('em:employee_new'), {
            'employee_id': 'emp001', 'first_name': 'X', 'department': self.dept.pk, 'designation': 'D',
            'employment_type': 'full_time', 'date_of_joining': '2026-01-01', 'status': 'active', 'country': 'India'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'already used')


class EmployeeValidationTests(Base):
    def test_leaving_date_rules(self):
        e = Employee(first_name='Z', department=self.dept, designation='x', date_of_joining=date(2026, 1, 1),
                     status=Employee.ACTIVE, date_of_leaving=date(2026, 2, 1))
        with self.assertRaises(ValidationError):
            e.full_clean()
        e.status, e.date_of_leaving = Employee.LEFT, None
        with self.assertRaises(ValidationError):
            e.full_clean()
        e.date_of_leaving = date(2025, 1, 1)
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_identity_formats(self):
        e = Employee(first_name='Z', department=self.dept, designation='x', date_of_joining=date(2026, 1, 1),
                     employee_id='T1', aadhaar_number='1234', pan_number='ABC', pin_code='12')
        with self.assertRaises(ValidationError) as cm:
            e.full_clean()
        for f in ('aadhaar_number', 'pan_number', 'pin_code'):
            self.assertIn(f, cm.exception.message_dict)

    def test_mask(self):
        self.assertEqual(mask('123456789012'), 'XXXXXXXX9012')
        self.emp.pan_number = 'abcde1234f'
        self.emp.save()
        self.assertEqual(self.emp.pan_masked, 'XXXXXX234F')

    def test_change_log(self):
        self.emp.designation = 'Lead'
        self.emp.save()
        self.assertTrue(self.emp.change_log.filter(field='designation', old_value='Engineer',
                                                   new_value='Lead').exists())

    def test_edit_form_keeps_sensitive_when_blank_and_never_echoes(self):
        self.emp.aadhaar_number = '123412341234'
        self.emp.save()
        r = self.client.get(reverse('em:employee_edit', args=[self.emp.pk]))
        self.assertNotContains(r, '123412341234')
        self.assertContains(r, 'XXXXXXXX1234')
        data = {'first_name': 'Asha', 'department': self.dept.pk, 'designation': 'Engineer',
                'employment_type': 'full_time', 'date_of_joining': '2025-01-01', 'status': 'confirmed',
                'country': 'India', 'aadhaar_number': ''}
        r = self.client.post(reverse('em:employee_edit', args=[self.emp.pk]), data)
        self.assertEqual(r.status_code, 302)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.aadhaar_number, '123412341234')
        self.assertEqual(self.emp.employee_id, 'EMP001')

    def test_detail_masks(self):
        self.emp.aadhaar_number = '123412341234'
        self.emp.save()
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]))
        self.assertNotContains(r, '123412341234')


class DepartmentTests(Base):
    def test_delete_blocked_with_employees(self):
        r = self.client.post(reverse('em:department_delete', args=[self.dept.pk]))
        self.assertTrue(Department.objects.filter(pk=self.dept.pk).exists())
        empty = Department.objects.create(name='Empty', code='E')
        self.client.post(reverse('em:department_delete', args=[empty.pk]))
        self.assertFalse(Department.objects.filter(pk=empty.pk).exists())

    def test_toggle_and_inactive_hidden_in_employee_form(self):
        old = Department.objects.create(name='Old', code='OLD', active=False)
        r = self.client.get(reverse('em:employee_new'))
        self.assertNotContains(r, '>Old<')
        self.client.post(reverse('em:department_toggle', args=[old.pk]))
        old.refresh_from_db()
        self.assertTrue(old.active)

    def test_crud_pages(self):
        r = self.client.post(reverse('em:department_new'), {'name': 'Finance', 'code': 'fin', 'active': 'on'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Department.objects.get(name='Finance').code, 'FIN')
        self.assertEqual(self.client.get(reverse('em:departments')).status_code, 200)


class ContactTests(Base):
    def test_multiple_contacts(self):
        for n in ('A', 'B'):
            self.client.post(reverse('em:contact_new', args=[self.emp.pk]),
                             {'name': n, 'relationship': 'Sibling', 'mobile': '9876543210'})
        self.assertEqual(self.emp.emergency_contacts.count(), 2)


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class DocumentTests(Base):
    def upload(self, content=PDF, name='a.pdf', **extra):
        data = {'document_type': 'pan', 'document_name': 'PAN', 'upload': SimpleUploadedFile(name, content)}
        data.update(extra)
        return self.client.post(reverse('em:document_new', args=[self.emp.pk]), data)

    def test_valid_upload_private_and_controlled(self):
        r = self.upload()
        self.assertEqual(r.status_code, 302)
        d = EmployeeDocument.objects.get()
        self.assertTrue(d.file.name.startswith(f'employees/{self.emp.pk}/documents/'))
        # stored under a random uuid basename, never the uploaded name (deterministic check)
        stem, ext = os.path.splitext(os.path.basename(d.file.name))
        self.assertNotEqual(os.path.basename(d.file.name), 'a.pdf')
        self.assertRegex(stem, r'^[0-9a-f]{32}$')
        self.assertEqual(ext, '.pdf')
        self.assertTrue(d.file.path.startswith(TMP))
        v = self.client.get(reverse('em:document_view', args=[self.emp.pk, d.pk]))
        self.assertEqual(v['Content-Type'], 'application/pdf')
        self.assertEqual(v['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', v['Cache-Control'])
        self.assertEqual(b''.join(v.streaming_content), PDF)
        dl = self.client.get(reverse('em:document_download', args=[self.emp.pk, d.pk]))
        self.assertIn('attachment', dl['Content-Disposition'])

    def test_rejects_bad_type_and_spoofed_content(self):
        self.upload(b'MZ\x90\x00 exe', 'evil.exe')
        self.upload(b'<html>not a pdf</html>', 'fake.pdf')
        self.upload(PNG, 'mismatch.pdf')
        self.assertEqual(EmployeeDocument.objects.count(), 0)

    def test_size_limit(self):
        s = PayrollSettings.current()
        s.document_max_mb = 1
        s.save()
        self.upload(PDF + b'0' * (1024 * 1024 + 1))
        self.assertEqual(EmployeeDocument.objects.count(), 0)

    def test_replace_keeps_history_and_delete_needs_confirm(self):
        self.upload()
        old = EmployeeDocument.objects.get()
        self.client.post(reverse('em:document_replace', args=[self.emp.pk, old.pk]),
                         {'document_type': 'pan', 'document_name': 'PAN v2',
                          'upload': SimpleUploadedFile('b.png', PNG)})
        new = EmployeeDocument.objects.get(is_current=True)
        self.assertEqual(new.replaces, old)
        self.assertEqual(new.content_type, 'image/png')
        old.refresh_from_db()
        self.assertFalse(old.is_current)
        self.assertEqual(self.client.get(reverse('em:document_history', args=[self.emp.pk, new.pk])).status_code, 200)
        r = self.client.get(reverse('em:document_delete', args=[self.emp.pk, new.pk]))
        self.assertContains(r, 'Yes, delete')
        self.assertTrue(EmployeeDocument.objects.filter(pk=new.pk).exists())
        self.client.post(reverse('em:document_delete', args=[self.emp.pk, new.pk]))
        self.assertFalse(EmployeeDocument.objects.filter(pk=new.pk).exists())
        old.refresh_from_db()
        self.assertTrue(old.is_current)

    def test_other_employee_cannot_fetch(self):
        self.upload()
        d = EmployeeDocument.objects.get()
        other = self.make_emp(first_name='O')
        self.assertEqual(self.client.get(reverse('em:document_view', args=[other.pk, d.pk])).status_code, 404)

    def test_expiry_state_and_indicator(self):
        from django.utils import timezone
        today = timezone.localdate()
        self.upload(expiry_date=str(today.replace(year=today.year - 1)))
        d = EmployeeDocument.objects.get()
        self.assertEqual(d.expiry_state(), 'expired')
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]) + '?tab=documents')
        self.assertContains(r, 'Expired')
        self.assertContains(r, 'PAN Card: Available')
        self.assertContains(r, 'Aadhaar Card: Missing')

    def test_remote_request_forbidden(self):
        r = self.client.get(reverse('em:employees'), REMOTE_ADDR='10.0.0.5')
        self.assertEqual(r.status_code, 403)


class BankTests(Base):
    def post(self, acct, confirm, ifsc='HDFC0001234'):
        return self.client.post(reverse('em:bank_new', args=[self.emp.pk]), {
            'bank_name': 'HDFC', 'account_holder_name': 'Asha', 'account_number': acct,
            'confirm_account_number': confirm, 'ifsc_code': ifsc, 'account_type': 'savings', 'bank_country': 'India'})

    def test_confirm_mismatch_and_ifsc(self):
        self.post('111122223333', '111122220000')
        self.post('111122223333', '111122223333', ifsc='BAD')
        self.assertEqual(EmployeeBankAccount.objects.count(), 0)

    def test_change_creates_history(self):
        self.post('111122223333', '111122223333')
        self.post('999988887777', '999988887777', ifsc='icic0001111')
        self.assertEqual(self.emp.bank_accounts.count(), 2)
        cur = self.emp.current_bank
        self.assertEqual(cur.account_number, '999988887777')
        self.assertEqual(cur.ifsc_code, 'ICIC0001111')
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]) + '?tab=bank')
        self.assertNotContains(r, '999988887777')
        self.assertContains(r, 'XXXXXXXX7777')

    def test_bank_row_not_editable(self):
        b = EmployeeBankAccount.add_for(self.emp, bank_name='X', account_holder_name='A', account_number='12345',
                                        ifsc_code='HDFC0001234')
        b.bank_name = 'Y'
        with self.assertRaises(ValidationError):
            b.save()


class SalaryStructureTests(Base):
    def test_new_structure_closes_previous(self):
        self.structure()
        r = self.client.post(reverse('em:salary_new', args=[self.emp.pk]), {
            'structure_type': 'monthly', 'effective_from': '2026-06-01', 'rate': '0', 'divisor': 'default',
            'e_basic': '35000', 'e_hra': '14000'})
        self.assertEqual(r.status_code, 302)
        old, new = self.emp.salary_structures.order_by('effective_from')
        self.assertEqual(old.effective_to, date(2026, 5, 31))
        self.assertEqual(old.basic_salary, D(30000))
        self.assertEqual(new.basic_salary, D(35000))
        self.assertEqual(EmployeeSalaryStructure.applicable(self.emp, date(2026, 5, 1), date(2026, 5, 31)), old)
        self.assertEqual(EmployeeSalaryStructure.applicable(self.emp, date(2026, 6, 1), date(2026, 6, 30)), new)

    def test_basic_required_and_backdate_blocked(self):
        self.structure(eff=date(2026, 1, 1))
        r = self.client.post(reverse('em:salary_new', args=[self.emp.pk]), {
            'structure_type': 'monthly', 'effective_from': '2025-06-01', 'rate': '0', 'divisor': 'default'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.emp.salary_structures.count(), 1)

    def test_totals(self):
        st = self.structure(other=[('deduction', 'pf', '1800')])
        self.assertEqual(st.total_earnings, D(42000))
        self.assertEqual(st.net_salary, D(40200))


class CalculationTests(Base):
    def test_full_month_31_actual(self):
        self.structure(basic='31000', hra='0')
        c = engine.calculate(self.emp, self.period(2026, 12))
        self.assertEqual(c.payable_days, 31)
        self.assertEqual(c.gross, D('31000.00'))

    def test_lwp_actual_divisor(self):
        self.structure(basic='30000', hra='10000')
        c = engine.calculate(self.emp, self.period(2026, 11), {'lwp_days': 2})
        self.assertEqual(c.payable_days, 28)
        self.assertEqual(c.gross, D('37333.33'))  # 28000 + 9333.33

    def test_february_actual_vs_fixed30(self):
        self.structure(basic='28000', hra='0')
        a = engine.calculate(self.emp, self.period(2026, 2), {'lwp_days': 1})
        self.assertEqual(a.divisor_days, 28)
        self.assertEqual(a.gross, D('27000.00'))
        b = engine.calculate(self.emp, self.period(2027, 2, 'fixed30'), {'lwp_days': 1})
        self.assertEqual(b.divisor_days, 30)
        self.assertEqual(b.payable_days, 29)
        self.assertEqual(b.gross, D('27066.67'))

    def test_fixed30_full_31_day_month_pays_full(self):
        self.structure(basic='30000', hra='0')
        c = engine.calculate(self.emp, self.period(2026, 12, 'fixed30'))
        self.assertEqual(c.gross, D('30000.00'))

    def test_pay_full_month(self):
        self.structure(basic='31000', hra='0', pay_full_month=True)
        c = engine.calculate(self.emp, self.period(2026, 12))
        self.assertEqual(c.gross, D('31000.00'))
        c = engine.calculate(self.emp, PayrollPeriod.objects.get(year=2026, month=12), {'lwp_days': 1})
        self.assertEqual(c.gross, D('30000.00'))

    def test_mid_month_joining_and_leaving(self):
        e = self.make_emp(date_of_joining=date(2026, 11, 16))
        self.structure(emp=e, eff=date(2026, 11, 16), basic='30000', hra='0')
        c = engine.calculate(e, self.period(2026, 11))
        self.assertEqual(c.payable_days, 15)
        self.assertEqual(c.gross, D('15000.00'))
        e.status, e.date_of_leaving = Employee.LEFT, date(2026, 11, 25)
        e.save()
        c = engine.calculate(e, PayrollPeriod.objects.get(year=2026, month=11))
        self.assertEqual(c.payable_days, 10)

    def test_daily_hourly_contract(self):
        e1 = self.make_emp(first_name='D')
        EmployeeSalaryStructure.objects.create(employee=e1, effective_from=date(2025, 1, 1),
                                               structure_type='daily', rate=D('1000'))
        p = self.period(2026, 11)
        self.assertEqual(engine.calculate(e1, p, {'payable_days': 22}).gross, D('22000.00'))
        e2 = self.make_emp(first_name='H')
        EmployeeSalaryStructure.objects.create(employee=e2, effective_from=date(2025, 1, 1),
                                               structure_type='hourly', rate=D('250.50'))
        self.assertEqual(engine.calculate(e2, p, {'approved_hours': '10.5'}).gross, D('2630.25'))
        e3 = self.make_emp(first_name='C')
        EmployeeSalaryStructure.objects.create(employee=e3, effective_from=date(2025, 1, 1),
                                               structure_type='contract', rate=D('50000'))
        self.assertEqual(engine.calculate(e3, p).gross, D('50000.00'))

    def test_extras_and_deductions(self):
        settings = PayrollSettings.current()
        settings.rounding = PayrollSettings.ROUND_2DP
        settings.save()
        self.structure(basic='30000', hra='0', other=[('deduction', 'professional_tax', '200'),
                                                      ('deduction', 'loan', '1000')])
        c = engine.calculate(self.emp, self.period(2026, 11), {
            'overtime_amount': '500', 'incentive_amount': '1000', 'additional_earnings': '250.255',
            'additional_deductions': '100'})
        self.assertEqual(c.gross, D('31750.26'))  # 250.255 -> 250.26 HALF_UP
        self.assertEqual(c.total_deductions, D('1300.00'))
        self.assertEqual(c.net, D('30450.26'))

    def test_statutory_rules_employee_vs_employer(self):
        self.structure(basic='20000', hra='5000', use_statutory_rules=True,
                       other=[('deduction', 'pf', '9999')])
        StatutoryRule.objects.create(code='pf', name='PF employee', side='employee', rate=D('12'), base='basic',
                                     base_ceiling=D('15000'), effective_from=date(2020, 1, 1))
        StatutoryRule.objects.create(code='pf', name='PF employer', side='employer', rate=D('12'), base='basic',
                                     base_ceiling=D('15000'), effective_from=date(2020, 1, 1))
        StatutoryRule.objects.create(code='esi', name='ESI', side='employee', rate=D('0.75'), base='gross',
                                     applies_if_gross_upto=D('21000'), effective_from=date(2020, 1, 1))
        StatutoryRule.objects.create(code='professional_tax', name='PT', side='employee', calculation='fixed',
                                     rate=D('200'), effective_from=date(2020, 1, 1), effective_to=date(2026, 10, 31))
        c = engine.calculate(self.emp, self.period(2026, 11))
        self.assertEqual([x.amount for x in c.deductions], [D('1800.00')])  # ESI not eligible, PT expired
        self.assertEqual(c.employer_total, D('1800.00'))
        self.assertEqual(c.net, D('23200.00'))

    def test_rounding_to_rupee(self):
        s = PayrollSettings.current()
        s.rounding = PayrollSettings.ROUND_RUPEE
        s.save()
        self.structure(basic='30000', hra='10000')
        c = engine.calculate(self.emp, self.period(2026, 11), {'lwp_days': 2})
        self.assertEqual(c.net, D('37333.00'))
        self.assertEqual(c.rounding_adjustment, D('-0.33'))

    def test_rounding_to_rupee_is_default_with_half_up_boundaries(self):
        self.assertEqual(PayrollSettings.current().rounding, PayrollSettings.ROUND_RUPEE)
        p = self.period(2026, 11)
        for amount, expected in (('10.49', '10.00'), ('10.50', '11.00'), ('10.51', '11.00')):
            with self.subTest(amount=amount):
                employee = self.make_emp(first_name=amount)
                self.structure(emp=employee, basic=amount, hra='0')
                self.assertEqual(engine.calculate(employee, p).net, D(expected))

        settings = PayrollSettings.current()
        settings.rounding = PayrollSettings.ROUND_2DP
        settings.save()
        self.assertEqual(engine.calculate(employee, p).net, D('10.51'))

    def test_invalid_inputs(self):
        self.structure()
        c = engine.calculate(self.emp, self.period(2026, 11), {'payable_days': 31})
        self.assertTrue(c.errors)

    def test_eligibility(self):
        p = self.period(2026, 11)
        self.assertEqual(engine.eligible_employees(p), [])  # no structure
        self.structure()
        future = self.make_emp(first_name='F', date_of_joining=date(2026, 12, 1))
        self.structure(emp=future, eff=date(2026, 12, 1))
        gone = self.make_emp(first_name='G', status=Employee.LEFT, date_of_leaving=date(2026, 10, 31))
        self.structure(emp=gone)
        self.assertEqual(engine.eligible_employees(p), [self.emp])


class PayrollLifecycleTests(Base):
    def setUp(self):
        super().setUp()
        self.st = self.structure()
        EmployeeBankAccount.add_for(self.emp, bank_name='HDFC', account_holder_name='Asha',
                                    account_number='111122223333', ifsc_code='HDFC0001234')

    def test_generate_no_duplicates(self):
        p = self.period()
        created, _ = engine.generate(p)
        self.assertEqual(len(created), 1)
        created, skipped = engine.generate(p)
        self.assertEqual(created, [])
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmployeePayroll.objects.create(period=p, employee=self.emp, revision=2)

    def test_generate_view_and_dashboard(self):
        r = self.client.post(reverse('em:payroll_generate'), {'year': 2026, 'month': 11, 'divisor': 'fixed30'})
        self.assertEqual(r.status_code, 302)
        p = PayrollPeriod.objects.get(year=2026, month=11)
        self.assertEqual(p.divisor, 'fixed30')
        r = self.client.get(reverse('em:payroll') + '?year=2026&month=11')
        self.assertContains(r, 'EMP001')
        self.assertContains(r, 'Refresh drafts')

    def test_payroll_period_payment_date_can_be_updated(self):
        period = self.period()
        response = self.client.post(
            reverse('em:period_update', args=[period.pk]),
            {'divisor': period.divisor, 'payment_date': '2026-11-30'},
        )

        self.assertRedirects(
            response, f'{reverse("em:payroll")}?year={period.year}&month={period.month}')
        period.refresh_from_db()
        self.assertEqual(period.payment_date, date(2026, 11, 30))

    def test_refresh_drafts_uses_current_employee_details_and_preserves_finalized(self):
        period = self.period()
        other = self.make_emp(first_name='B')
        self.structure(emp=other)
        drafts, _ = engine.generate(period)
        draft = next(record for record in drafts if record.employee_id == self.emp.pk)
        finalized = next(record for record in drafts if record.employee_id == other.pk)
        engine.finalize([finalized])

        draft.lwp_days = D('1')
        draft.payable_days = D('29')
        draft.save()
        self.st.components.filter(code='basic').update(amount=D('31000'))
        self.emp.designation = 'Director'
        self.emp.save()

        response = self.client.post(reverse('em:payroll_refresh_drafts', args=[period.pk]))
        self.assertRedirects(response, f'{reverse("em:payroll")}?year={period.year}&month={period.month}')
        draft.refresh_from_db()
        finalized.refresh_from_db()
        self.assertEqual(draft.gross_earnings, D('41566.67'))
        self.assertEqual(draft.lwp_days, D('1.00'))
        self.assertEqual(draft.snap_designation, 'Director')
        self.assertEqual(finalized.status, EmployeePayroll.FINALIZED)
        self.assertEqual(finalized.snap_designation, 'Engineer')

    def test_refresh_drafts_reports_invalid_recalculations(self):
        period = self.period()
        draft = engine.generate(period)[0][0]
        draft.payable_days = D('31')
        draft.save()

        response = self.client.post(reverse('em:payroll_refresh_drafts', args=[period.pk]), follow=True)
        self.assertContains(response, 'Payable days (31.00) cannot exceed the divisor')
        draft.refresh_from_db()
        self.assertEqual(draft.payable_days, D('31.00'))

    def test_finalize_freezes_and_snapshots(self):
        p = self.period()
        rec = engine.generate(p)[0][0]
        engine.finalize([rec])
        rec.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual(rec.status, 'finalized')
        self.assertEqual(p.status, 'finalized')
        rec.net_salary = D(1)
        with self.assertRaises(FinalizedRecordError):
            rec.save()
        with self.assertRaises(FinalizedRecordError):
            rec.delete()
        # later employee/bank/salary changes do not change the finalized record
        self.emp.designation = 'Director'
        self.emp.save()
        EmployeeBankAccount.add_for(self.emp, bank_name='SBI', account_holder_name='Asha',
                                    account_number='555566667777', ifsc_code='SBIN0001234')
        rec.refresh_from_db()
        self.assertEqual(rec.snap_designation, 'Engineer')
        self.assertEqual(rec.snap_bank_name, 'HDFC')
        r = self.client.get(reverse('em:payroll_edit', args=[rec.pk]))
        self.assertEqual(r.status_code, 302)

    def test_edit_draft_recalculates(self):
        rec = engine.generate(self.period())[0][0]
        r = self.client.post(reverse('em:payroll_edit', args=[rec.pk]), {'lwp_days': '3'})
        self.assertEqual(r.status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.payable_days, 27)
        self.assertEqual(rec.gross_earnings, D('37800.00'))

    def test_revision_keeps_original(self):
        rec = engine.generate(self.period())[0][0]
        engine.finalize([rec])
        rec.refresh_from_db()
        r = self.client.post(reverse('em:payroll_revise', args=[rec.pk]), {'reason': 'Missed LWP'})
        rev = EmployeePayroll.objects.get(revision=2)
        self.assertRedirects(r, reverse('em:payroll_edit', args=[rev.pk]))
        rec.refresh_from_db()
        self.assertTrue(rec.is_current)
        self.client.post(reverse('em:payroll_edit', args=[rev.pk]), {'lwp_days': '1'})
        self.client.post(reverse('em:payroll_finalize_one', args=[rev.pk]))
        rec.refresh_from_db()
        rev.refresh_from_db()
        self.assertFalse(rec.is_current)
        self.assertTrue(rev.is_current)
        self.assertEqual(rec.status, 'finalized')
        self.assertNotEqual(rec.net_salary, rev.net_salary)
        self.assertEqual(rev.revises, rec)

    def test_payslip_pdf_and_zip(self):
        rec = engine.generate(self.period())[0][0]
        r = self.client.get(reverse('em:payslip', args=[rec.pk]))
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertIn('EMP001_November_2026_Payslip.pdf', r['Content-Disposition'])
        z = self.client.get(reverse('em:payslip_zip', args=[rec.period.pk]))
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        self.assertEqual(names, ['EMP001_November_2026_Payslip.pdf'])
        other = Department.objects.create(name='Ops', code='OPS')
        z = self.client.get(reverse('em:payslip_zip', args=[rec.period.pk]) + f'?department={other.pk}')
        self.assertEqual(z.status_code, 302)

    def test_payslip_text_masked(self):
        self.emp.pan_number = 'ABCDE1234F'
        self.emp.save()
        from .pdf.payslip import build_payslip_data
        from invoice_app.models import BusinessProfile
        rec = engine.generate(self.period())[0][0]
        d = build_payslip_data(rec, PayrollSettings.current(), BusinessProfile.current())
        self.assertEqual(d.pan_masked, 'XXXXXX234F')
        self.assertEqual(d.account_masked, 'XXXXXXXX3333')
        self.assertEqual(d.company_name, BusinessProfile.current().name)

    def test_payslip_pdf_omits_days_divisor_and_payment_date(self):
        """Salary slip PDF no longer shows calendar/working/payable days, divisor, or payment date."""
        import fitz
        from .pdf.payslip import build_payslip_data, render_payslip
        from invoice_app.models import BusinessProfile
        rec = engine.generate(self.period())[0][0]
        data = build_payslip_data(rec, PayrollSettings.current(), BusinessProfile.current())
        pdf = render_payslip(data)
        text = ''.join(page.get_text() for page in fitz.open(stream=pdf, filetype='pdf'))
        lowered = text.lower()
        for label in (
            'calendar days', 'calender days', 'working days', 'payable days',
            'salary divisor', 'payment date',
        ):
            self.assertNotIn(label, lowered, msg=f'PDF still contains {label!r}')
        self.assertIn('employee id', lowered)
        self.assertIn('net salary', lowered)
        self.assertIn('lwp days', lowered)

    def test_exports(self):
        from openpyxl import load_workbook
        rec = engine.generate(self.period())[0][0]
        r = self.client.get(reverse('em:export_register', args=[rec.period.pk]))
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws['A2'].value, 'EMP001')
        # bank file: only finalized
        r = self.client.get(reverse('em:export_bank', args=[rec.period.pk, 'csv']))
        self.assertNotIn(b'111122223333', r.content)
        engine.finalize([rec])
        r = self.client.get(reverse('em:export_bank', args=[rec.period.pk, 'csv']))
        self.assertNotIn(b'111122223333', r.content)  # masked by default
        self.assertIn(b'XXXXXXXX3333', r.content)
        self.assertIn(b'HDFC0001234', r.content)
        r = self.client.get(reverse('em:export_bank', args=[rec.period.pk, 'csv']), {'full': '1'})
        self.assertIn(b'111122223333', r.content)
        r = self.client.get(reverse('em:export_bank', args=[rec.period.pk, 'xlsx']))
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws['D2'].value, 'XXXXXXXX3333')
        r = self.client.get(reverse('em:export_bank', args=[rec.period.pk, 'xlsx']), {'full': '1'})
        self.assertEqual(load_workbook(io.BytesIO(r.content)).active['D2'].value, '111122223333')

    def test_reports(self):
        rec = engine.generate(self.period())[0][0]
        for kind in ('monthly', 'department', 'joining'):
            r = self.client.get(reverse('em:reports') + f'?report={kind}&year=2026&month=11')
            self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse('em:reports') + f'?report=history&employee={self.emp.pk}&year=2026&month=11')
        self.assertContains(r, 'November 2026')
        r = self.client.get(reverse('em:reports') + '?report=monthly&year=2026&month=11&export=xlsx')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b'PK'))


class MiscTests(Base):
    def test_words_and_filename(self):
        self.assertEqual(rupees_in_words(D('1234567.50')),
                         'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paise Only')
        self.assertEqual(payslip_filename('EMP001', 'November 2026'), 'EMP001_November_2026_Payslip.pdf')

    def test_pages_render(self):
        for name in ('em:home', 'em:employees', 'em:employee_new', 'em:departments', 'em:payroll', 'em:reports',
                     'em:settings', 'em:rule_new'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for tab in ('overview', 'contacts', 'documents', 'bank', 'salary', 'payroll', 'log'):
            r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]) + f'?tab={tab}')
            self.assertEqual(r.status_code, 200, tab)
        self.assertEqual(self.client.get(reverse('em:salary_new', args=[self.emp.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:bank_new', args=[self.emp.pk])).status_code, 200)

    def test_invoice_pages_still_work_with_new_nav(self):
        for name in ('dashboard', 'invoice_list', 'clients', 'projects_hub', 'profile_edit', 'new_invoice'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'Payroll settings')

    def test_sample_command(self):
        out_dir = tempfile.mkdtemp()
        buf = io.StringIO()
        call_command('make_sample_employee', out=out_dir, stdout=buf)
        call_command('make_sample_employee', out=out_dir, stdout=io.StringIO())
        self.assertEqual(Employee.objects.filter(notes__startswith='[sample]').count(), 1)
        self.assertIn('net 46467.00', buf.getvalue())  # sample now uses attendance: payable 28/30
        self.assertEqual(EmployeePayroll.objects.count(), 0)
        shutil.rmtree(out_dir)

    def test_photo_upload_controlled(self):
        r = self.client.post(reverse('em:employee_edit', args=[self.emp.pk]), {
            'first_name': 'Asha', 'department': self.dept.pk, 'designation': 'Engineer',
            'employment_type': 'full_time', 'date_of_joining': '2025-01-01', 'status': 'confirmed',
            'country': 'India', 'photo_upload': SimpleUploadedFile('me.png', PNG)})
        self.assertEqual(r.status_code, 302)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.photo.name.endswith('.png'))
        r = self.client.get(reverse('em:employee_photo', args=[self.emp.pk]))
        self.assertEqual(r['Content-Type'], 'image/png')


class DesignGapTests(TestCase):
    def setUp(self):
        from .models import Department, Employee
        self.dept = Department.objects.create(name='Gap Dept', code='GAP')
        self.Employee = Employee

    def _emp(self, n='A'):
        return self.Employee.objects.create(first_name=n, department=self.dept, designation='Dev',
                                            date_of_joining=date(2026, 1, 1))

    def test_employee_ids_sequential(self):
        from .models import allocate_employee_id
        a, b = allocate_employee_id(), allocate_employee_id()
        self.assertRegex(a, r'^EMP\d{3}$')
        self.assertEqual(int(b[3:]), int(a[3:]) + 1)

    def test_hard_delete_blocked_with_payroll_history(self):
        from django.db.models import ProtectedError
        from .models import EmployeePayroll, PayrollPeriod
        e = self._emp()
        per = PayrollPeriod.objects.create(year=2026, month=1)
        EmployeePayroll.objects.create(period=per, employee=e)
        with self.assertRaises(ProtectedError):
            e.delete()
        self.assertTrue(self.Employee.objects.filter(pk=e.pk).exists())

    def test_register_csv_export(self):
        from .models import PayrollPeriod
        per = PayrollPeriod.objects.create(year=2026, month=2)
        r = self.client.get(reverse('em:export_register_csv', args=[per.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn(b'Net Salary', r.content)


class NavSmokeTests(TestCase):
    def test_home_and_every_nav_link(self):
        r = self.client.get('/')
        self.assertContains(r, 'QuantBud Business Management')
        for s in ('Invoice Management', 'Employee Management', 'Payroll Management', 'Shared'):
            self.assertContains(r, s)
        import re
        links = set(re.findall(r'href="(/[^"]*)"', r.content.decode()))
        self.assertGreater(len(links), 15)
        for url in sorted(links):
            if url.startswith('/static/'):
                continue
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_legacy_invoice_urls_still_resolve(self):
        for url in ('/invoices/', '/customers/', '/projects/', '/business/', '/invoice/new/', '/invoicing/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class AliasTests(Base):
    def test_default_on_create(self):
        self.assertEqual(self.emp.alias, 'Asha')
        e = self.make_emp(first_name='  Ravi Kumar ', last_name='S')
        self.assertEqual(Employee.objects.get(pk=e.pk).alias, 'Ravi')

    def test_explicit_alias_preserved(self):
        e = self.make_emp(first_name='Priya', alias='PJ')
        e.designation = 'Lead'
        e.save()
        self.assertEqual(Employee.objects.get(pk=e.pk).alias, 'PJ')

    def test_blank_alias_refilled(self):
        self.emp.alias = 'Ash'
        self.emp.save()
        self.emp.alias = '  '
        self.emp.save()
        self.assertEqual(Employee.objects.get(pk=self.emp.pk).alias, 'Asha')

    def test_backfill_migration(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module('employee_management.migrations.0005_employee_alias')
        Employee.objects.filter(pk=self.emp.pk).update(alias='')
        other = self.make_emp(first_name='Meera', alias='Mee')
        mig.backfill(apps, None)
        self.assertEqual(Employee.objects.get(pk=self.emp.pk).alias, 'Asha')
        self.assertEqual(Employee.objects.get(pk=other.pk).alias, 'Mee')

    def test_form_list_detail_search(self):
        from .forms import EmployeeForm
        f = EmployeeForm().fields['alias']
        self.assertFalse(f.required)
        self.assertEqual(f.help_text, 'Defaults to first name')
        self.make_emp(first_name='Zed', alias='Zorro')
        r = self.client.get(reverse('em:employees'), {'q': 'zorro'})
        self.assertContains(r, '<th>Alias</th>', html=False)
        self.assertContains(r, 'Zorro')
        self.assertNotContains(r, 'Asha')
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]))
        self.assertContains(r, '<span class="muted">Alias</span><br>Asha', html=False)


def _zip_bytes(encrypted=False, corrupt=False):
    import zipfile as zf
    buf = io.BytesIO()
    with zf.ZipFile(buf, 'w', zf.ZIP_DEFLATED) as z:
        zi = zf.ZipInfo('degree.txt')
        zi.compress_type = zf.ZIP_DEFLATED
        z.writestr(zi, b'B.Tech certificate ' * 200)
    data = bytearray(buf.getvalue())
    if encrypted:  # set the 'encrypted' flag bit in the local and central headers
        data[6] |= 0x1
        data[data.rfind(b'PK\x01\x02') + 8] |= 0x1
    if corrupt:
        for i in range(45, 60):
            data[i] ^= 0xFF
    return bytes(data)


FORM_PDF = b'%PDF-1.4\n%test\n'


class EmployeeFormDocumentTests(Base):
    def post(self, files=None, emp=None, **extra):
        data = {'first_name': 'Asha', 'department': self.dept.pk, 'designation': 'Engineer',
                'employment_type': 'full_time', 'date_of_joining': '2025-01-01', 'status': 'confirmed',
                'country': 'India'}
        data.update(files or {})
        data.update(extra)
        url = reverse('em:employee_edit', args=[emp.pk]) if emp else reverse('em:employee_new')
        return self.client.post(url, data)

    def current(self, dtype, emp=None):
        return EmployeeDocument.objects.filter(employee=emp or self.emp, document_type=dtype, is_current=True)

    def test_form_saves_with_no_documents(self):
        r = self.post(first_name='Nodoc')
        self.assertEqual(r.status_code, 302)
        e = Employee.objects.get(first_name='Nodoc')
        self.assertFalse(e.documents.exists())
        r = self.client.get(reverse('em:employee_detail', args=[e.pk]))
        self.assertContains(r, 'Documents (optional)')
        self.assertContains(r, 'Not uploaded', count=4)

    def test_each_upload_works(self):
        r = self.post({'doc_aadhaar': SimpleUploadedFile('aadhaar.pdf', FORM_PDF),
                       'doc_pan': SimpleUploadedFile('pan.png', PNG),
                       'doc_experience': SimpleUploadedFile('exp.jpg', b'\xff\xd8\xff\xe0' + b'0' * 20),
                       'doc_skills_zip': SimpleUploadedFile('skills.zip', _zip_bytes())}, emp=self.emp)
        self.assertEqual(r.status_code, 302)
        for t, ct, ext in [('aadhaar', 'application/pdf', '.pdf'), ('pan', 'image/png', '.png'),
                           ('experience_certificate', 'image/jpeg', '.jpg'),
                           ('skills_education_zip', 'application/zip', '.zip')]:
            d = self.current(t).get()
            self.assertEqual(d.content_type, ct)
            stem, e = os.path.splitext(os.path.basename(d.file.name))
            self.assertEqual(e, ext)
            self.assertRegex(stem, r'^[0-9a-f]{32}$')
            self.assertTrue(os.path.abspath(d.file.path).startswith(os.path.abspath(TMP)))
            dl = self.client.get(reverse('em:document_download', args=[self.emp.pk, d.pk]))
            self.assertEqual(dl.status_code, 200)
            self.assertIn('attachment', dl['Content-Disposition'])
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]))
        self.assertContains(r, 'Uploaded on', count=4)
        self.assertNotContains(r, 'Not uploaded')
        from document_management.models import Document
        self.assertEqual(Document.objects.filter(source_type='employee_document').count(), 4)

    def test_wrong_types_rejected(self):
        cases = [{'doc_aadhaar': SimpleUploadedFile('a.zip', _zip_bytes())},
                 {'doc_pan': SimpleUploadedFile('p.pdf', b'not a pdf')},
                 {'doc_experience': SimpleUploadedFile('x.docx', b'PK\x03\x04')},
                 {'doc_skills_zip': SimpleUploadedFile('s.pdf', FORM_PDF)}]
        for files in cases:
            r = self.post(files, emp=self.emp)
            self.assertEqual(r.status_code, 200, files)
        self.assertFalse(self.emp.documents.exists())

    def test_non_zip_corrupt_and_encrypted_zip_rejected(self):
        for blob, msg in [(b'just text', 'not a valid ZIP'), (_zip_bytes(corrupt=True), 'corrupt'),
                          (_zip_bytes(encrypted=True), 'encrypted')]:
            r = self.post({'doc_skills_zip': SimpleUploadedFile('s.zip', blob)}, emp=self.emp)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, msg)
        self.assertFalse(self.emp.documents.exists())

    @override_settings(EMPLOYEE_DOC_MAX_MB=1, EMPLOYEE_ZIP_MAX_MB=1)
    def test_size_limits_configurable(self):
        big = FORM_PDF + b'0' * (1024 * 1024 + 10)
        r = self.post({'doc_pan': SimpleUploadedFile('p.pdf', big)}, emp=self.emp)
        self.assertContains(r, 'too large')

    def test_replace_keeps_history(self):
        self.post({'doc_pan': SimpleUploadedFile('pan1.pdf', FORM_PDF)}, emp=self.emp)
        first = self.current('pan').get()
        self.post({'doc_pan': SimpleUploadedFile('pan2.pdf', FORM_PDF + b'v2')}, emp=self.emp)
        cur = self.current('pan').get()
        self.assertNotEqual(cur.pk, first.pk)
        self.assertEqual(cur.replaces_id, first.pk)
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertTrue(os.path.exists(first.file.path))
        self.assertEqual(EmployeeDocument.objects.filter(employee=self.emp, document_type='pan').count(), 2)

    def test_private_serving_local_only_and_no_public_url(self):
        self.post({'doc_aadhaar': SimpleUploadedFile('a.pdf', FORM_PDF)}, emp=self.emp)
        d = self.current('aadhaar').get()
        url = reverse('em:document_download', args=[self.emp.pk, d.pk])
        self.assertEqual(self.client.get(url, REMOTE_ADDR='192.168.1.20').status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 200)
        from django.conf import settings
        self.assertEqual(self.client.get(settings.MEDIA_URL + d.file.name).status_code, 404)
        self.assertEqual(self.client.get('/private_media/' + d.file.name).status_code, 404)
        self.assertFalse(os.path.abspath(d.file.path).startswith(os.path.abspath(settings.MEDIA_ROOT)))

    def test_identity_numbers_stay_masked(self):
        self.emp.aadhaar_number, self.emp.pan_number = '123412341234', 'ABCDE1234F'
        self.emp.save()
        r = self.client.get(reverse('em:employee_detail', args=[self.emp.pk]))
        self.assertNotContains(r, '123412341234')
        self.assertNotContains(r, 'ABCDE1234F')
