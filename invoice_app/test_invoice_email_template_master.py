"""Master page for invoice email subject/body templates."""
from django.test import TestCase
from django.urls import reverse

from invoice_app.models import Client, Country, InvoiceEmailTemplate


class InvoiceEmailTemplateMasterTests(TestCase):
    def setUp(self):
        self.country = (
            Country.objects.filter(iso2='US').first()
            or Country.objects.filter(iso2='IN').first()
        )
        if not self.country:
            self.country = Country.objects.create(
                name='United States', iso2='US', iso3='USA')

    def test_list_in_menu_and_renders(self):
        InvoiceEmailTemplate.objects.create(
            kind='subject', name='Subj A', text='Hello {invoice_number}')
        r = self.client.get(reverse('invoice_email_templates'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Email templates')
        self.assertContains(r, 'Subj A')
        self.assertContains(r, 'Add template')
        self.assertContains(r, 'page_size')
        dash = self.client.get(reverse('dashboard'))
        self.assertContains(dash, reverse('invoice_email_templates'))
        self.assertContains(dash, 'Email templates')

    def test_create_edit_filter(self):
        r = self.client.post(reverse('invoice_email_template_add'), {
            'kind': 'subject',
            'name': 'New Subj',
            'text': 'Invoice {invoice_number}',
        })
        self.assertEqual(r.status_code, 302)
        tpl = InvoiceEmailTemplate.objects.get(name='New Subj')
        self.assertEqual(tpl.kind, 'subject')
        r2 = self.client.post(reverse('invoice_email_template_edit', args=[tpl.pk]), {
            'kind': 'body',
            'name': 'New Body',
            'text': 'Dear {customer_name}',
        })
        self.assertEqual(r2.status_code, 302)
        tpl.refresh_from_db()
        self.assertEqual(tpl.kind, 'body')
        self.assertEqual(tpl.name, 'New Body')
        listed = self.client.get(reverse('invoice_email_templates'), {'kind': 'body'})
        self.assertContains(listed, 'New Body')
        listed_subj = self.client.get(
            reverse('invoice_email_templates'), {'kind': 'subject'})
        self.assertNotContains(listed_subj, 'New Body')

    def test_delete_unused_and_block_in_use(self):
        unused = InvoiceEmailTemplate.objects.create(
            kind='subject', name='Unused', text='x')
        used = InvoiceEmailTemplate.objects.create(
            kind='body', name='Used', text='y')
        Client.objects.create(
            name='Uses Tpl',
            country_ref=self.country,
            invoice_email_body_template=used,
        )
        r = self.client.post(
            reverse('invoice_email_template_delete', args=[unused.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(
            InvoiceEmailTemplate.objects.filter(pk=unused.pk).exists())
        r2 = self.client.post(
            reverse('invoice_email_template_delete', args=[used.pk]),
            follow=True,
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(
            InvoiceEmailTemplate.objects.filter(pk=used.pk).exists())
        self.assertContains(r2, 'cannot be deleted')

    def test_customer_form_dropdown_includes_master_template(self):
        tpl = InvoiceEmailTemplate.objects.create(
            kind='subject', name='From Master', text='Subj {invoice_number}')
        add = self.client.get(reverse('client_add'))
        self.assertContains(add, 'From Master')
        self.assertContains(add, f'value="{tpl.pk}"')
