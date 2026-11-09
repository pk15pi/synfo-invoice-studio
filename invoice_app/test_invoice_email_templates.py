"""Invoice email To/CC/BCC and subject/body templates."""
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
from invoice_app.forms import ClientForm
from invoice_app.models import (
    BusinessProfile, Client, Country, Invoice, InvoiceEmailDefaults,
    InvoiceEmailLog, InvoiceEmailTemplate,
)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class InvoiceEmailTemplateSendTests(TestCase):
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
        )
        self.pdf_patch = mock.patch(
            'invoice_app.invoice_email.render_for_invoice', return_value=b'%PDF-mock%',
        )
        self.pdf_patch.start()

    def tearDown(self):
        self.pdf_patch.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _invoice(self, number='INV-T1'):
        inv = Invoice.objects.create(
            client=self.customer, invoice_number=number, invoice_date=date(2026, 10, 15),
            due_date=date(2026, 10, 15), total=Decimal('1000.00'), taxable_value=Decimal('1000.00'),
        )
        Invoice.objects.filter(pk=inv.pk).update(
            status=Invoice.FINALIZED, finalized_at=timezone.now(), cancelled=False,
        )
        inv.refresh_from_db()
        return inv

    def test_one_message_with_to_cc_bcc(self):
        self.customer.invoice_email_to = 'a@example.com; b@example.com'
        self.customer.invoice_email_cc = 'cc@example.com'
        self.customer.invoice_email_bcc = 'bcc@example.com'
        self.customer.save()
        inv = self._invoice()
        mail.outbox.clear()
        log = IE.send_invoice(inv)
        self.assertTrue(log.success)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['a@example.com', 'b@example.com'])
        self.assertEqual(msg.cc, ['cc@example.com'])
        self.assertEqual(msg.bcc, ['bcc@example.com'])
        self.assertEqual(log.recipient_email, 'a@example.com, b@example.com')
        self.assertEqual(log.recipient_cc, 'cc@example.com')
        self.assertIn('bcc@example.com', log.recipient_bcc)

    def test_to_falls_back_to_client_email(self):
        self.customer.invoice_email_to = ''
        self.customer.email = 'fallback@acme.example'
        self.customer.save()
        inv = self._invoice('INV-FB')
        mail.outbox.clear()
        log = IE.send_invoice(inv)
        self.assertTrue(log.success)
        self.assertEqual(mail.outbox[0].to, ['fallback@acme.example'])

    def test_missing_to_and_email_not_sent(self):
        self.customer.invoice_email_to = ''
        self.customer.email = ''
        self.customer.save()
        inv = self._invoice('INV-NONE')
        mail.outbox.clear()
        log = IE.send_invoice(inv)
        self.assertFalse(log.success)
        self.assertEqual(len(mail.outbox), 0)

    def test_subject_body_from_template_with_placeholders(self):
        subj = InvoiceEmailTemplate.objects.create(
            kind=InvoiceEmailTemplate.SUBJECT, name='Std subj',
            text='Invoice {invoice_number} for {customer_name}',
        )
        body = InvoiceEmailTemplate.objects.create(
            kind=InvoiceEmailTemplate.BODY, name='Std body',
            text='Hello {customer_brand}, total {grand_total} on {invoice_date}.',
        )
        self.customer.invoice_email_subject_template = subj
        self.customer.invoice_email_body_template = body
        self.customer.invoice_email_use_custom_subject = False
        self.customer.invoice_email_use_custom_body = False
        self.customer.save()
        inv = self._invoice('INV-PH')
        mail.outbox.clear()
        IE.send_invoice(inv)
        msg = mail.outbox[0]
        self.assertEqual(msg.subject, 'Invoice INV-PH for Acme Corp')
        self.assertIn('Hello Acme, total 1000.00 on 15 Oct 2026.', msg.body)

    def test_custom_subject_body(self):
        self.customer.invoice_email_use_custom_subject = True
        self.customer.invoice_email_custom_subject = 'Custom {invoice_number}'
        self.customer.invoice_email_use_custom_body = True
        self.customer.invoice_email_custom_body = 'Body for {customer_name}'
        self.customer.save()
        inv = self._invoice('INV-CU')
        mail.outbox.clear()
        log = IE.send_invoice(inv)
        self.assertTrue(log.success)
        self.assertEqual(mail.outbox[0].subject, 'Custom INV-CU')
        self.assertIn('Body for Acme Corp', mail.outbox[0].body)
        self.assertEqual(log.subject, 'Custom INV-CU')

    def test_fallback_subject_when_unset(self):
        inv = self._invoice('INV-DF')
        mail.outbox.clear()
        IE.send_invoice(inv)
        self.assertTrue(mail.outbox[0].subject.startswith('Invoice'))
        self.assertIn('INV-DF', mail.outbox[0].subject)


class ClientInvoiceEmailFormTests(TestCase):
    def setUp(self):
        self.country = Country.objects.filter(iso2='US').first() or Country.objects.filter(iso2='IN').first()
        if not self.country:
            self.country = Country.objects.create(name='United States', iso2='US', iso3='USA')

    def _base(self, **extra):
        data = {
            'name': 'Form Co', 'country_ref': self.country.pk,
            'email': 'main@form.example', 'billing_type': 'monthly_prorated',
            'terms_mode': 'custom', 'tax_treatment': 'export', 'active': 'on',
        }
        data.update(extra)
        return data

    def test_save_to_cc_bcc_and_reject_invalid(self):
        f = ClientForm(data=self._base(
            invoice_email_to='good@x.com; also@y.com',
            invoice_email_cc='cc@z.com',
            invoice_email_bcc='bcc@z.com',
        ))
        self.assertTrue(f.is_valid(), f.errors)
        client = f.save()
        self.assertEqual(client.invoice_email_to, 'good@x.com, also@y.com')
        self.assertEqual(client.invoice_email_cc, 'cc@z.com')
        bad = ClientForm(data=self._base(invoice_email_to='not-an-email'))
        self.assertFalse(bad.is_valid())
        self.assertIn('invoice_email_to', bad.errors)

    def test_save_as_template_and_defaults_prefill(self):
        f = ClientForm(data=self._base(
            invoice_email_use_custom_subject='on',
            invoice_email_custom_subject='Saved subject {invoice_number}',
            save_subject_template_name='My Subject',
            invoice_email_use_custom_body='on',
            invoice_email_custom_body='Saved body for {customer_name}',
            save_body_template_name='My Body',
        ))
        self.assertTrue(f.is_valid(), f.errors)
        client = f.save()
        self.assertFalse(client.invoice_email_use_custom_subject)
        self.assertIsNotNone(client.invoice_email_subject_template_id)
        self.assertEqual(client.invoice_email_subject_template.name, 'My Subject')
        self.assertEqual(client.invoice_email_body_template.name, 'My Body')
        defaults = InvoiceEmailDefaults.current()
        self.assertEqual(defaults.subject_template_id, client.invoice_email_subject_template_id)
        self.assertEqual(defaults.body_template_id, client.invoice_email_body_template_id)
        # New form prefills last selection
        add = ClientForm()
        self.assertEqual(add.initial.get('invoice_email_subject_template'), client.invoice_email_subject_template_id)
        self.assertEqual(add.initial.get('invoice_email_body_template'), client.invoice_email_body_template_id)

    def test_page_shows_to_cc_bcc(self):
        self.customer = Client.objects.create(
            name='Show Co', email='s@x.com',
            invoice_email_to='to@x.com', invoice_email_cc='cc@x.com', invoice_email_bcc='bcc@x.com',
        )
        inv = Invoice.objects.create(
            client=self.customer, invoice_number='INV-SHOW', invoice_date=date(2026, 10, 1),
            due_date=date(2026, 10, 1), total=Decimal('10'), taxable_value=Decimal('10'),
        )
        Invoice.objects.filter(pk=inv.pk).update(
            status=Invoice.FINALIZED, finalized_at=timezone.now(), cancelled=False,
        )
        r = self.client.get(reverse('invoice_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        self.assertContains(r, 'to@x.com')
        self.assertContains(r, 'cc@x.com')
        self.assertContains(r, 'bcc@x.com')
        self.assertContains(r, '>To<')
