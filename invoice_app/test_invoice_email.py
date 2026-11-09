"""Email invoices page (mirrors salary-slip email tests)."""
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employee_management import email_config as EC
from invoice_app import invoice_email as IE
from invoice_app.models import BusinessProfile, Client, Invoice, InvoiceEmailLog
from reporting import periods as report_periods


class PreviousMonthDefaultTests(TestCase):
    def test_previous_month_params(self):
        self.assertEqual(
            IE.previous_month_params(date(2026, 11, 9)),
            {'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10'},
        )
        self.assertEqual(
            IE.previous_month_params(date(2026, 1, 1)),
            {'period_mode': 'calendar', 'calendar_year': '2025', 'calendar_month': '12'},
        )


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class InvoiceEmailPageTests(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.objects.create(pk=1, name='QuantBud LLP')
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / 'email.json'
        self.env = mock.patch.dict(
            'os.environ',
            {'QB_EMAIL_CONFIG_FILE': str(self.cfg), 'QB_EMAIL_PASSWORD': ''},
            clear=False,
        )
        self.env.start()
        EC.save_config({
            'host': 'smtp.office365.com', 'port': 587, 'user': 'support@synfowebs.com',
            'from_email': 'support@synfowebs.com', 'password': 'test-secret-pw',
            'use_tls': True, 'use_ssl': False, 'send_gap_seconds': 30,
        })
        self.customer = Client.objects.create(
            name='Acme Corp', brand_name='Acme', email='billing@acme.example',
            address='1 Main St',
        )
        self.pdf_patch = mock.patch(
            'invoice_app.invoice_email.render_for_invoice', return_value=b'%PDF-mock%',
        )
        self.pdf_patch.start()

    def tearDown(self):
        self.pdf_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _invoice(self, number='INV-001', when=date(2026, 10, 15), email=None, status=Invoice.FINALIZED):
        if email is not None:
            self.customer.email = email
            self.customer.save(update_fields=['email'])
        inv = Invoice.objects.create(
            client=self.customer,
            invoice_number=number,
            invoice_date=when,
            due_date=when,
            total=Decimal('1000.00'),
            taxable_value=Decimal('1000.00'),
        )
        if status == Invoice.FINALIZED:
            Invoice.objects.filter(pk=inv.pk).update(
                status=Invoice.FINALIZED, finalized_at=timezone.now(), cancelled=False,
            )
            inv.refresh_from_db()
        return inv

    def test_default_redirects_to_previous_month(self):
        with mock.patch('invoice_app.invoice_email.timezone.localdate', return_value=date(2026, 11, 9)):
            r = self.client.get(reverse('invoice_email'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('calendar_year=2026', r['Location'])
        self.assertIn('calendar_month=10', r['Location'])

    def test_explicit_period_kept(self):
        self._invoice()
        r = self.client.get(reverse('invoice_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'INV-001')
        self.assertContains(r, 'Acme Corp')

    def test_page_renders_buttons_and_filters(self):
        self._invoice()
        r = self.client.get(reverse('invoice_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertContains(r, 'Filter by')
        self.assertContains(r, 'Send to all')
        self.assertContains(r, 'Bulk send')
        self.assertContains(r, 'Resend')
        self.assertContains(r, 'data-gap="30"')
        self.assertContains(r, 'Not sent')
        self.assertContains(r, 'page_size')
        self.assertContains(r, '1000.00')
        self.assertIn('run(0, \'Bulk send\')', r.content.decode())
        self.assertIn('run(gapSec * 1000', r.content.decode())

    def test_menu_link_present(self):
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, reverse('invoice_email'))
        self.assertContains(r, 'Email invoices')

    def test_send_success_and_resend(self):
        inv = self._invoice()
        mail.outbox.clear()
        next_url = reverse('invoice_email') + '?period_mode=calendar&calendar_year=2026&calendar_month=10'
        r = self.client.post(reverse('invoice_email_send', args=[inv.pk]), {'next': next_url})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['billing@acme.example'])
        self.assertEqual(mail.outbox[0].from_email, 'support@synfowebs.com')
        self.assertTrue(mail.outbox[0].attachments)
        page = self.client.get(next_url)
        self.assertContains(page, 'Sent')
        mail.outbox.clear()
        self.client.post(reverse('invoice_email_send', args=[inv.pk]), {'next': next_url})
        self.assertEqual(InvoiceEmailLog.objects.filter(invoice=inv, success=True).count(), 2)

    def test_failed_send_not_marked_sent_masks_password(self):
        inv = self._invoice(number='INV-FAIL')
        with mock.patch('invoice_app.invoice_email.EmailMessage.send', side_effect=RuntimeError('bad test-secret-pw')):
            self.client.post(reverse('invoice_email_send', args=[inv.pk]), {'next': reverse('invoice_email')})
        log = InvoiceEmailLog.objects.get(invoice=inv)
        self.assertFalse(log.success)
        self.assertNotIn('test-secret-pw', log.error)
        page = self.client.get(reverse('invoice_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertContains(page, 'Not sent')

    def test_missing_email_not_sent(self):
        inv = self._invoice(number='INV-NOMAIL', email='')
        mail.outbox.clear()
        self.client.post(reverse('invoice_email_send', args=[inv.pk]), {'next': reverse('invoice_email')})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(InvoiceEmailLog.objects.get(invoice=inv).success)

    def test_bulk_queue_and_json_skip(self):
        a = self._invoice(number='INV-A')
        b = self._invoice(number='INV-B', when=date(2026, 10, 20))
        c = Client.objects.create(name='No Mail Co', email='')
        inv_c = Invoice.objects.create(
            client=c, invoice_number='INV-C', invoice_date=date(2026, 10, 5),
            due_date=date(2026, 10, 5), total=Decimal('10'), taxable_value=Decimal('10'),
        )
        Invoice.objects.filter(pk=inv_c.pk).update(
            status=Invoice.FINALIZED, finalized_at=timezone.now(), cancelled=False,
        )
        inv_c.refresh_from_db()
        IE.send_invoice(a)
        period = report_periods.resolve_standard({
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        queue = IE.bulk_queue(list(IE.invoices_for_period(period)))
        self.assertEqual(queue['skipped_already_sent'], 1)
        self.assertEqual(queue['skipped_no_email'], 1)
        self.assertEqual(queue['pending_ids'], [b.pk])
        page = self.client.get(reverse('invoice_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10', 'page_size': '5',
        })
        self.assertContains(page, f'data-ids="{b.pk}"')
        mail.outbox.clear()
        r = self.client.post(reverse('invoice_email_send_json', args=[a.pk]), {'skip_if_sent': '1'})
        self.assertEqual(r.json()['status'], 'skipped')
        self.assertEqual(len(mail.outbox), 0)
        r2 = self.client.post(reverse('invoice_email_send_json', args=[b.pk]), {'skip_if_sent': '1'})
        self.assertEqual(r2.json()['status'], 'sent')
