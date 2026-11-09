"""Shared Email templates master (categories + salary defaults)."""
from django.test import TestCase
from django.urls import reverse

from invoice_app.models import Client, Country, InvoiceEmailDefaults, InvoiceEmailTemplate


class InvoiceEmailTemplateMasterTests(TestCase):
    def setUp(self):
        self.country = (
            Country.objects.filter(iso2='US').first()
            or Country.objects.filter(iso2='IN').first()
        )
        if not self.country:
            self.country = Country.objects.create(
                name='United States', iso2='US', iso3='USA')

    def test_list_in_shared_menu_and_renders(self):
        InvoiceEmailTemplate.objects.create(
            category='customer', kind='subject', name='Subj A',
            text='Hello {invoice_number}')
        r = self.client.get(reverse('email_templates'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Email templates')
        self.assertContains(r, 'Subj A')
        self.assertContains(r, 'Add template')
        self.assertContains(r, 'page_size')
        self.assertContains(r, 'Shared')
        home = self.client.get(reverse('home'))
        self.assertContains(home, reverse('email_templates'))
        self.assertContains(home, 'Email templates')
        # Not under Invoice Management menu label next to Email invoices only —
        # still linked from Shared; old invoice URL redirects.
        legacy = self.client.get('/invoices/email-templates/')
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(legacy.url, reverse('email_templates'))

    def test_create_edit_filter_category(self):
        r = self.client.post(reverse('email_template_add'), {
            'category': 'customer',
            'kind': 'subject',
            'name': 'New Subj',
            'text': 'Invoice {invoice_number}',
        })
        self.assertEqual(r.status_code, 302)
        tpl = InvoiceEmailTemplate.objects.get(name='New Subj')
        self.assertEqual(tpl.kind, 'subject')
        self.assertEqual(tpl.category, 'customer')
        r2 = self.client.post(reverse('email_template_edit', args=[tpl.pk]), {
            'category': 'common',
            'kind': 'body',
            'name': 'New Body',
            'text': 'Dear {customer_name}',
        })
        self.assertEqual(r2.status_code, 302)
        tpl.refresh_from_db()
        self.assertEqual(tpl.kind, 'body')
        self.assertEqual(tpl.category, 'common')
        listed = self.client.get(reverse('email_templates'), {'kind': 'body'})
        self.assertContains(listed, 'New Body')
        listed_subj = self.client.get(reverse('email_templates'), {'kind': 'subject'})
        self.assertNotContains(listed_subj, 'New Body')
        by_cat = self.client.get(reverse('email_templates'), {'category': 'common'})
        self.assertContains(by_cat, 'New Body')

    def test_delete_unused_block_in_use_and_salary_default(self):
        unused = InvoiceEmailTemplate.objects.create(
            category='customer', kind='subject', name='Unused', text='x')
        used = InvoiceEmailTemplate.objects.create(
            category='customer', kind='body', name='Used', text='y')
        salary = InvoiceEmailTemplate.objects.create(
            category='salary', kind='subject', name='Sal Def', text='Sal {salary_month}')
        Client.objects.create(
            name='Uses Tpl',
            country_ref=self.country,
            invoice_email_body_template=used,
        )
        defaults = InvoiceEmailDefaults.current()
        defaults.salary_subject_template = salary
        defaults.save()

        r = self.client.post(reverse('email_template_delete', args=[unused.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(InvoiceEmailTemplate.objects.filter(pk=unused.pk).exists())

        r2 = self.client.post(
            reverse('email_template_delete', args=[used.pk]), follow=True)
        self.assertTrue(InvoiceEmailTemplate.objects.filter(pk=used.pk).exists())
        self.assertContains(r2, 'cannot be deleted')

        r3 = self.client.post(
            reverse('email_template_delete', args=[salary.pk]), follow=True)
        self.assertTrue(InvoiceEmailTemplate.objects.filter(pk=salary.pk).exists())
        self.assertContains(r3, 'salary-slip default')

    def test_customer_form_dropdown_excludes_salary_includes_common(self):
        cust = InvoiceEmailTemplate.objects.create(
            category='customer', kind='subject', name='Cust Subj',
            text='C {invoice_number}')
        common = InvoiceEmailTemplate.objects.create(
            category='common', kind='subject', name='Common Subj',
            text='M {month}')
        salary = InvoiceEmailTemplate.objects.create(
            category='salary', kind='subject', name='Sal Subj',
            text='S {salary_month}')
        add = self.client.get(reverse('client_add'))
        html = add.content.decode()
        self.assertIn('Cust Subj', html)
        self.assertIn('Common Subj', html)
        self.assertIn(f'value="{cust.pk}"', html)
        self.assertIn(f'value="{common.pk}"', html)
        self.assertNotIn('Sal Subj', html)
        # Scope to the subject-template <select> so unrelated option values do not false-positive.
        start = html.find('id="id_invoice_email_subject_template"')
        end = html.find('</select>', start)
        subj_opts = html[start:end]
        self.assertIn(f'value="{cust.pk}"', subj_opts)
        self.assertIn(f'value="{common.pk}"', subj_opts)
        self.assertNotIn(f'value="{salary.pk}"', subj_opts)

    def test_set_salary_default(self):
        tpl = InvoiceEmailTemplate.objects.create(
            category='salary', kind='body', name='Sal Body',
            text='Hi {employee_name}')
        r = self.client.post(
            reverse('email_template_set_salary_default', args=[tpl.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Salary default')
        defaults = InvoiceEmailDefaults.current()
        self.assertEqual(defaults.salary_body_template_id, tpl.pk)
