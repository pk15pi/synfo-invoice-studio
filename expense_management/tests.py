from django.conf import settings
import shutil
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from employee_management.models import Department, Employee
from invoice_app.models import Client, ProjectTemplate

from . import reports, services
from .fy import fy_label, fy_range, fy_start_year, quarter_range, resolve_period
from .models import (CompanyExpense, CompanyExpenseAttachment, CompanyExpensePayment, EmployeeExpenseAttachment,
                     EmployeeExpenseClaim as C, EmployeeReimbursement, ExpenseCategory, ExpenseSubcategory, Vendor)

D = Decimal
PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
TMP = tempfile.mkdtemp(prefix='qb-exp-test-')


def pdf(name='bill.pdf', data=PDF):
    return SimpleUploadedFile(name, data, content_type='application/pdf')


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        from employee_management.storage import private_storage
        private_storage.__dict__.pop('base_location', None)
        private_storage.__dict__.pop('location', None)
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.emp = Employee.objects.create(first_name='Asha', last_name='Rao', department=self.dept,
                                           designation='Engineer', date_of_joining=date(2024, 4, 1),
                                           status=Employee.CONFIRMED)
        self.tech = ExpenseCategory.objects.get(name='Technology')
        self.travel = ExpenseCategory.objects.get(name='Travel')
        self.hosting = ExpenseSubcategory.objects.get(category=self.tech, name='Hosting')
        self.vendor = Vendor.objects.create(name='Host Co', gstin='29ABCDE1234F1Z5', bank_account_number='123456789012')

    def expense(self, **kw):
        data = dict(expense_date=date(2026, 5, 10), vendor=self.vendor, category=self.tech, subcategory=self.hosting,
                    description='VPS', amount_before_tax=D('10000'), gst_amount=D('1800'), igst_amount=D('1800'))
        data.update(kw)
        e = CompanyExpense(**data)
        e.full_clean()
        e.save()
        return e

    def claim(self, amount='5000', **kw):
        data = dict(employee=self.emp, claim_date=date(2026, 5, 12), expense_date=date(2026, 5, 11),
                    category=self.travel, description='Taxi', amount_claimed=D(amount))
        data.update(kw)
        c = C(**data)
        c.full_clean()
        c.save()
        return c

    def pay(self, e, amount, ref='', d=date(2026, 5, 15)):
        return services.record_payment(e, payment_date=d, method='bank_transfer', amount=amount, reference=ref)

    def reimb(self, c, amount, ref='', d=date(2026, 5, 20)):
        return services.record_reimbursement(c, reimbursement_date=d, method='bank_transfer', amount=amount,
                                             reference=ref)


class FYTests(TestCase):
    def test_indian_fy(self):
        self.assertEqual(fy_start_year(date(2026, 3, 31)), 2025)
        self.assertEqual(fy_start_year(date(2026, 4, 1)), 2026)
        self.assertEqual(fy_label(2026), 'FY 2026-27')
        self.assertEqual(fy_range(2026), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(quarter_range(date(2026, 5, 3)), (date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual(quarter_range(date(2027, 2, 3)), (date(2027, 1, 1), date(2027, 3, 31)))
        s, e, _ = resolve_period({'fy': '2025'})[:3]
        self.assertEqual((s, e), (date(2025, 4, 1), date(2026, 3, 31)))


class SeedTests(TestCase):
    def test_seeded_categories(self):
        names = set(ExpenseCategory.objects.values_list('name', flat=True))
        for n in ['Office', 'Technology', 'Travel', 'Employee Expenses', 'Professional Services',
                  'Marketing and Sales', 'Financial', 'Other']:
            self.assertIn(n, names)
        self.assertTrue(ExpenseSubcategory.objects.filter(category__name='Travel', name='Hotel').exists())


class CompanyExpenseTests(Base):
    def test_ids_and_total(self):
        e1, e2 = self.expense(), self.expense()
        self.assertEqual(e1.expense_id, 'EXP-0001')
        self.assertEqual(e2.expense_id, 'EXP-0002')
        self.assertEqual(e1.total_amount, D('11800.00'))
        self.assertEqual(e1.payment_status, CompanyExpense.UNPAID)

    def test_total_formula_with_discount_and_other_taxes(self):
        e = self.expense(other_taxes=D('100'), discount=D('400'))
        self.assertEqual(e.total_amount, D('11500.00'))

    def test_direct_total(self):
        e = self.expense(amount_mode='direct', amount_before_tax=D('0'), gst_amount=D('0'), igst_amount=D('0'),
                         total_amount=D('999.50'))
        self.assertEqual(e.total_amount, D('999.50'))

    def test_gst_split_must_match(self):
        with self.assertRaises(ValidationError):
            self.expense(gst_amount=D('1800'), igst_amount=D('0'), cgst_amount=D('800'), sgst_amount=D('800'))

    def test_negative_rejected(self):
        with self.assertRaises(ValidationError):
            self.expense(amount_before_tax=D('-5'))

    def test_partial_then_full_payment(self):
        e = self.expense(amount_mode='direct', amount_before_tax=D('0'), gst_amount=D('0'), igst_amount=D('0'),
                         total_amount=D('20000'))
        self.pay(e, '10000', 'UTR1')
        e.refresh_from_db()
        self.assertEqual(e.payment_status, CompanyExpense.PARTIAL)
        self.assertEqual(e.outstanding_amount, D('10000.00'))
        self.pay(e, '10000', 'UTR2', date(2026, 6, 1))
        e.refresh_from_db()
        self.assertEqual(e.payment_status, CompanyExpense.PAID)
        self.assertEqual(e.outstanding_amount, D('0.00'))
        self.assertEqual(e.payments.count(), 2)

    def test_overpayment_and_duplicate_blocked(self):
        e = self.expense()
        with self.assertRaises(ValidationError):
            self.pay(e, '20000')
        self.pay(e, '1000', 'R1')
        with self.assertRaises(ValidationError):
            self.pay(e, '1000', 'R1')
        self.assertEqual(e.payments.count(), 1)

    def test_payment_cannot_be_deleted(self):
        e = self.expense()
        p = self.pay(e, '100')
        with self.assertRaises(ValidationError):
            p.delete()
        self.assertTrue(CompanyExpensePayment.objects.filter(pk=p.pk).exists())

    def test_cancel_keeps_history_and_excluded_from_totals(self):
        e = self.expense()
        self.pay(e, '100')
        with self.assertRaises(ValidationError):
            services.cancel_expense(e, '')
        services.cancel_expense(e, 'Duplicate bill')
        e.refresh_from_db()
        self.assertTrue(e.is_cancelled)
        self.assertEqual(e.payments.count(), 1)
        with self.assertRaises(ValidationError):
            self.pay(e, '50')
        f = reports.parse_filters({'fy': '2026'})
        self.assertEqual(reports.company_qs(f).count(), 0)

    def test_foreign_currency_inr(self):
        e = self.expense(currency='USD', exchange_rate=D('83.5'), amount_mode='direct', amount_before_tax=D('0'),
                         gst_amount=D('0'), igst_amount=D('0'), total_amount=D('100'))
        self.assertEqual(e.total_inr, D('8350.00'))

    def test_project_client_mismatch(self):
        c1, c2 = Client.objects.create(name='A'), Client.objects.create(name='B')
        p = ProjectTemplate.objects.create(client=c1, name='P1')
        with self.assertRaises(ValidationError):
            self.expense(client=c2, project=p)
        e = self.expense(project=p)
        self.assertEqual(e.client_id, c1.pk)


class AttachmentTests(Base):
    def test_upload_view_download_replace_delete(self):
        e = self.expense()
        att = services.add_attachment(e, pdf(), 'vendor_invoice')
        path = Path(att.file.path)
        self.assertTrue(path.exists())
        self.assertTrue(str(path).startswith(str(Path(TMP))))
        r = self.client.get(reverse('exp:att_view', args=['company', att.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('inline', r['Content-Disposition'])
        r.close()
        r = self.client.get(reverse('exp:att_download', args=['company', att.pk]))
        self.assertIn('attachment', r['Content-Disposition'])
        r.close()
        services.replace_attachment(att, SimpleUploadedFile('r.png', PNG, content_type='image/png'))
        att.refresh_from_db()
        self.assertFalse(path.exists())
        self.assertEqual(att.content_type, 'image/png')
        r = self.client.get(reverse('exp:att_delete', args=['company', att.pk]))
        self.assertContains(r, 'Delete attachment')
        self.client.post(reverse('exp:att_delete', args=['company', att.pk]))
        self.assertFalse(CompanyExpenseAttachment.objects.exists())
        self.assertTrue(CompanyExpense.objects.filter(pk=e.pk).exists())

    def test_bad_file_rejected(self):
        e = self.expense()
        with self.assertRaises(ValidationError):
            services.add_attachment(e, SimpleUploadedFile('x.pdf', b'MZ not a pdf', content_type='application/pdf'),
                                    'other')
        self.assertTrue(services.validate_uploads([SimpleUploadedFile('a.exe', b'MZ')]))

    def test_claim_attachment(self):
        c = self.claim()
        att = services.add_attachment(c, pdf(), 'receipt' if 'receipt' in dict(EmployeeExpenseAttachment.TYPES)
                                      else EmployeeExpenseAttachment.TYPES[0][0])
        self.assertIn('claims', att.file.name)
        r = self.client.get(reverse('exp:att_view', args=['claim', att.pk]))
        self.assertEqual(r.status_code, 200)

    def test_backup_includes_expense_attachments(self):
        # backup_data zips everything under EMPLOYEE_PRIVATE_ROOT; expense files must live there.
        e = self.expense()
        att = services.add_attachment(e, pdf(), 'vendor_invoice')
        root = Path(settings.EMPLOYEE_PRIVATE_ROOT).resolve()
        self.assertTrue(Path(att.file.path).resolve().is_relative_to(root))
        self.assertTrue(att.file.name.replace('\\', '/').startswith('expenses/'))


class ClaimTests(Base):
    def test_ids_snapshot_and_draft(self):
        c = self.claim()
        self.assertEqual(c.claim_id, 'CLM-0001')
        self.assertEqual(c.status, C.DRAFT)
        self.assertEqual(c.snap_department, 'Engineering')
        self.assertEqual(c.department_id, self.dept.pk)

    def test_full_flow_and_history(self):
        c = self.claim('1200')
        services.submit_claim(c)
        services.start_review(c)
        services.approve_claim(c)
        self.assertEqual(c.status, C.APPROVED)
        self.assertEqual(c.reimbursed_amount, D('0'))  # approval is not reimbursement
        self.reimb(c, '1200')
        self.assertEqual(c.status, C.REIMBURSED)
        self.assertEqual([r.action for r in c.reviews.order_by('id')],
                         ['submit', 'start_review', 'approve', 'reimburse'])

    def test_partial_approval_partial_reimbursement(self):
        c = self.claim('5000')
        services.submit_claim(c)
        services.approve_claim(c, '4500', 'policy cap')
        self.assertEqual(c.status, C.PARTIALLY_APPROVED)
        self.reimb(c, '3000')
        self.assertEqual(c.approved_amount, D('4500.00'))
        self.assertEqual(c.reimbursed_amount, D('3000.00'))
        self.assertEqual(c.outstanding_amount, D('1500.00'))
        self.assertIn('Partially Reimbursed', c.status_label)
        with self.assertRaises(ValidationError):
            self.reimb(c, '2000')
        self.reimb(c, '1500', d=date(2026, 5, 25))
        self.assertEqual(c.status, C.REIMBURSED)
        self.assertEqual(c.outstanding_amount, D('0.00'))

    def test_approve_more_than_claimed_blocked(self):
        c = self.claim('100')
        services.submit_claim(c)
        with self.assertRaises(ValidationError):
            services.approve_claim(c, '150')

    def test_reject_requires_reason_and_draft_cannot_be_reimbursed(self):
        c = self.claim()
        with self.assertRaises(ValidationError):
            self.reimb(c, '10')
        services.submit_claim(c)
        with self.assertRaises(ValidationError):
            services.reject_claim(c, '  ')
        services.reject_claim(c, 'No receipt')
        self.assertEqual(c.status, C.REJECTED)
        self.assertEqual(c.rejection_reason, 'No receipt')
        with self.assertRaises(ValidationError):
            self.reimb(c, '10')

    def test_return_for_correction_and_cancel(self):
        c = self.claim()
        services.submit_claim(c)
        services.return_for_correction(c, 'Attach bill')
        self.assertEqual(c.status, C.DRAFT)
        self.assertTrue(c.returned_for_correction)
        services.cancel_claim(c, 'Not needed')
        self.assertEqual(c.status, C.CANCELLED)

    def test_duplicate_reimbursement_blocked(self):
        c = self.claim('1000')
        services.submit_claim(c)
        services.approve_claim(c)
        self.reimb(c, '100', 'X1')
        with self.assertRaises(ValidationError):
            self.reimb(c, '100', 'X1')
        self.assertEqual(EmployeeReimbursement.objects.count(), 1)


class CategoryVendorTests(Base):
    def test_used_category_cannot_be_deleted(self):
        self.expense()
        r = self.client.post(reverse('exp:category_delete', args=['category', self.tech.pk]), follow=True)
        self.assertTrue(ExpenseCategory.objects.filter(pk=self.tech.pk).exists())
        self.assertContains(r, 'cannot be deleted')
        r = self.client.post(reverse('exp:category_delete', args=['sub', self.hosting.pk]))
        self.assertTrue(ExpenseSubcategory.objects.filter(pk=self.hosting.pk).exists())

    def test_unused_subcategory_delete_and_toggle(self):
        s = ExpenseSubcategory.objects.create(category=self.tech, name='Temp')
        self.client.post(reverse('exp:category_delete', args=['sub', s.pk]))
        self.assertFalse(ExpenseSubcategory.objects.filter(pk=s.pk).exists())
        self.client.post(reverse('exp:category_toggle', args=['category', self.tech.pk]))
        self.tech.refresh_from_db()
        self.assertFalse(self.tech.active)

    def test_vendor_code_mask_and_quick_add(self):
        self.assertTrue(self.vendor.vendor_code.startswith('VND-'))
        self.assertTrue(self.vendor.account_masked.endswith('9012'))
        self.assertNotIn('12345678', self.vendor.account_masked)
        r = self.client.post(reverse('exp:vendor_quick_add'), {'qv-name': 'Quick Ltd', 'qv-vendor_type': 'other'})
        self.assertEqual(r.json()['ok'], True)
        r = self.client.post(reverse('exp:vendor_quick_add'), {'qv-name': 'Bad', 'qv-vendor_type': 'other',
                                                               'qv-gstin': 'XYZ'})
        self.assertEqual(r.status_code, 400)


class ReportTests(Base):
    def test_reports_and_exports(self):
        e = self.expense(cgst_amount=D('900'), sgst_amount=D('900'), igst_amount=D('0'))
        self.pay(e, '5000')
        c = self.claim('5000')
        services.submit_claim(c)
        services.approve_claim(c, '4500')
        self.reimb(c, '3000')
        f = reports.parse_filters({'fy': '2026'})
        cat = reports.build('category', f)
        self.assertEqual(cat['totals'][2], D('16300.00'))
        emp = reports.build('employee', f)
        self.assertEqual(emp['rows'][0][3:], [D('4500.00'), D('3000.00'), D('1500.00')])
        gst = reports.build('gst', f)
        self.assertEqual(gst['rows'][0][5], D('900.00'))
        self.assertIn('accountant', gst['note'].lower())
        ven = reports.build('vendor', f)
        self.assertEqual(ven['rows'][0][4], D('6800.00'))
        for key in reports.REPORTS:
            for fmt in ('csv', 'xlsx', 'pdf'):
                r = self.client.get(reverse('exp:report', args=[key]), {'fy': '2026', 'format': fmt})
                self.assertEqual(r.status_code, 200, (key, fmt))
            self.assertEqual(self.client.get(reverse('exp:report', args=[key]), {'fy': '2026'}).status_code, 200)


class PageTests(Base):
    def test_pages_load(self):
        e = self.expense()
        c = self.claim()
        services.add_attachment(e, pdf(), 'vendor_invoice')
        urls = [reverse('exp:dashboard'), reverse('exp:dashboard') + '?period=prev_fy', reverse('exp:all'),
                reverse('exp:all') + '?q=VPS&type=company', reverse('exp:expense_new'),
                reverse('exp:expense_detail', args=[e.pk]), reverse('exp:expense_edit', args=[e.pk]),
                reverse('exp:claims'), reverse('exp:review'), reverse('exp:claim_new'),
                reverse('exp:claim_detail', args=[c.pk]), reverse('exp:claim_edit', args=[c.pk]),
                reverse('exp:vendors'), reverse('exp:vendor_new'), reverse('exp:vendor_detail', args=[self.vendor.pk]),
                reverse('exp:categories'), reverse('exp:category_new'), reverse('exp:subcategory_new', args=[self.tech.pk]),
                reverse('exp:reports')]
        for u in urls:
            self.assertEqual(self.client.get(u).status_code, 200, u)
        self.assertContains(self.client.get('/'), 'Expense Management')

    def test_create_expense_via_form_with_payment_and_attachment(self):
        r = self.client.post(reverse('exp:expense_new'), {
            'expense_date': '2026-05-10', 'vendor': self.vendor.pk, 'category': self.tech.pk,
            'subcategory': self.hosting.pk, 'description': 'Form VPS', 'amount_mode': 'breakup',
            'amount_before_tax': '1000', 'gst_amount': '180', 'igst_amount': '180', 'cgst_amount': '',
            'sgst_amount': '', 'other_taxes': '', 'discount': '', 'total_amount': '', 'currency': 'INR',
            'exchange_rate': '1', 'doc_type': 'vendor_invoice', 'attachments': pdf(),
            'pay_now': '1', 'pay-payment_date': '2026-05-11', 'pay-method': 'upi', 'pay-amount': '1180'})
        e = CompanyExpense.objects.get(description='Form VPS')
        self.assertRedirects(r, reverse('exp:expense_detail', args=[e.pk]))
        self.assertEqual(e.total_amount, D('1180.00'))
        self.assertEqual(e.payment_status, CompanyExpense.PAID)
        self.assertEqual(e.attachments.count(), 1)

    def test_claim_form_and_review_actions(self):
        r = self.client.post(reverse('exp:claim_new'), {
            'employee': self.emp.pk, 'claim_date': '2026-05-12', 'expense_date': '2026-05-11',
            'category': self.travel.pk, 'description': 'Cab', 'amount_claimed': '5000', 'currency': 'INR',
            'exchange_rate': '1', 'submit_now': '1'})
        c = C.objects.get(description='Cab')
        self.assertEqual(c.status, C.SUBMITTED)
        self.assertContains(self.client.get(reverse('exp:review')), c.claim_id)
        self.client.post(reverse('exp:claim_action', args=[c.pk, 'partial']), {'approved_amount': '4500'})
        self.client.post(reverse('exp:claim_reimburse', args=[c.pk]), {
            'reimbursement_date': '2026-05-20', 'amount': '3000', 'method': 'bank_transfer'})
        c.refresh_from_db()
        self.assertEqual(c.outstanding_amount, D('1500.00'))
        r = self.client.post(reverse('exp:claim_action', args=[c.pk, 'reject']), {'reason': 'x'}, follow=True)
        c.refresh_from_db()
        self.assertEqual(c.status, C.PARTIALLY_APPROVED)

    def test_local_only_guard(self):
        r = self.client.get(reverse('exp:dashboard'), REMOTE_ADDR='10.1.2.3')
        self.assertEqual(r.status_code, 403)


class SampleCommandTests(Base):
    def test_sample_command(self):
        Client.objects.create(name='Acme')
        out = StringIO()
        call_command('make_sample_expenses', stdout=out)
        self.assertIn('[sample]', out.getvalue())
        self.assertTrue(CompanyExpense.objects.filter(remarks__contains='[sample]').exists())
        self.assertTrue(C.objects.filter(remarks__contains='[sample]').exists())
        call_command('make_sample_expenses', '--remove', stdout=out)
        self.assertFalse(CompanyExpense.objects.filter(remarks__contains='[sample]').exists())
