from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as accounting_services
from . import billing, gst, ledger, manpower_billing
from .models import (BusinessProfile, Client, Invoice, ManpowerBillingRecord, ManpowerDeployment,
                     ManpowerRateRevision, ProjectTemplate)
from .pdf.from_models import build_invoice_data


class ManpowerBillingTests(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.profile.amount_rounding = BusinessProfile.ROUND_PAISE
        self.profile.save(update_fields=['amount_rounding'])
        self.client_obj = Client.objects.create(name='Manpower Client')

    def post_lines(self, rows, **extra):
        data = {'client': self.client_obj.pk, 'invoice_date': '2026-11-02', 'billing_year': '2026',
                'billing_mon': '10', 'terms_mode': 'custom', 'terms_text': '30', 'service_type': 'general_manpower',
                'resource_display_format': 'designation', 'action': 'save'}
        for name in ('line_project', 'line_name', 'line_desc', 'line_from', 'line_to', 'line_days', 'line_unit',
                     'line_amount', 'line_deployment', 'line_method', 'line_proration', 'line_remarks'):
            data[name] = [row.get(name.removeprefix('line_'), '') for row in rows]
        data.update(extra)
        return self.client.post(reverse('new_invoice'), data)

    def test_monthly_daily_hourly_and_fixed_contract_totals(self):
        self.assertEqual(self.post_lines([
            {'name': 'React Developer', 'days': '2', 'unit': '80000', 'method': 'monthly'},
            {'name': 'Python Developer', 'days': '1', 'unit': '75000', 'method': 'monthly'},
            {'name': 'QA Engineer', 'days': '1', 'unit': '50000', 'method': 'monthly'},
        ]).status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, D('285000.00'))
        self.assertEqual(invoice.service_type, Invoice.GENERAL_MANPOWER)
        self.assertIsNone(invoice.lines.first().project_id)

        self.post_lines([{'name': 'Technician', 'days': '20', 'unit': '2500', 'method': 'daily'}])
        self.assertEqual(Invoice.objects.order_by('-pk').first().total, D('50000.00'))
        self.post_lines([{'name': 'Consultant', 'days': '80', 'unit': '1000', 'method': 'hourly'}])
        self.assertEqual(Invoice.objects.order_by('-pk').first().total, D('80000.00'))
        self.post_lines([{'name': 'Contract team', 'days': '1', 'unit': '200000', 'method': 'fixed'}])
        self.assertEqual(Invoice.objects.order_by('-pk').first().total, D('200000.00'))

    def make_deployment(self, **kwargs):
        values = dict(client=self.client_obj, resource_name='External QA resource', designation='QA Engineer',
                      start_date=date(2026, 10, 10), billing_method='monthly', billing_rate=D('60000.00'),
                      proration_method='calendar', rate_effective_from=date(2026, 10, 10), status='active')
        values.update(kwargs)
        deployment = ManpowerDeployment.objects.create(**values)
        ManpowerRateRevision.objects.create(deployment=deployment, billing_method=deployment.billing_method,
                                            rate=deployment.billing_rate, proration_method=deployment.proration_method,
                                            effective_from=deployment.rate_effective_from)
        return deployment

    def test_monthly_proration_methods_and_rate_history(self):
        d = self.make_deployment()
        self.assertEqual(manpower_billing.calculate_deployment(d, date(2026, 10, 1), date(2026, 10, 31),
                                                               'paise')[4], D('42580.65'))
        d30 = self.make_deployment(resource_name='30-day resource', proration_method='30_day')
        self.assertEqual(manpower_billing.calculate_deployment(d30, date(2026, 10, 1), date(2026, 10, 31),
                                                               'paise')[4], D('44000.00'))
        dfull = self.make_deployment(resource_name='Full-month resource', proration_method='full_month')
        self.assertEqual(manpower_billing.calculate_deployment(dfull, date(2026, 10, 1), date(2026, 10, 31),
                                                               'paise')[4], D('60000.00'))

    def test_mid_period_rate_change_is_applied_and_old_invoice_snapshot_is_unchanged(self):
        d = self.make_deployment()
        ManpowerRateRevision.objects.create(deployment=d, billing_method='monthly', rate=D('90000'),
                                            proration_method='calendar', effective_from=date(2026, 10, 15))
        amount = manpower_billing.calculate_deployment(d, date(2026, 10, 1), date(2026, 10, 31), 'paise')[4]
        self.assertEqual(amount, D('59032.26'))
        r = self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}])
        self.assertEqual(r.status_code, 302)
        invoice = Invoice.objects.get()
        saved = invoice.total
        d.billing_rate = D('120000.00')
        d.save(update_fields=['billing_rate'])
        invoice.refresh_from_db()
        self.assertEqual(invoice.total, saved)
        self.assertEqual(invoice.lines.get().amount, saved)

    def test_working_days_and_manual_override_require_configured_inputs(self):
        d = self.make_deployment(resource_name='Working-day resource', proration_method='working_days')
        total = manpower_billing.working_days(date(2026, 10, 1), date(2026, 10, 31))
        billable = manpower_billing.working_days(date(2026, 10, 10), date(2026, 10, 31))
        expected = billing.round_amount(D('60000') * billable / total, 'paise')
        self.assertEqual(manpower_billing.calculate_deployment(
            d, date(2026, 10, 1), date(2026, 10, 31), 'paise')[4], expected)
        manual = self.make_deployment(resource_name='Manual resource', proration_method='manual')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            manpower_billing.calculate_deployment(manual, date(2026, 10, 1), date(2026, 10, 31), 'paise')
        result = manpower_billing.calculate_deployment(manual, date(2026, 10, 1), date(2026, 10, 31),
                                                       'paise', manual_amount=D('50000'), remarks='Client approved amount')
        self.assertEqual(result[4], D('50000.00'))

    def test_deployment_invoice_duplicate_period_blocked_unless_supplementary(self):
        d = self.make_deployment()
        self.assertEqual(self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}]).status_code, 302)
        response = self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already has a bill for the selected period')
        self.assertEqual(ManpowerBillingRecord.objects.count(), 1)
        response = self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}], supplementary='on')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManpowerBillingRecord.objects.count(), 2)

    def test_optional_project_resource_display_and_consolidated_pdf(self):
        p = ProjectTemplate.objects.create(client=self.client_obj, name='Existing project', monthly_amount=D('1000'))
        d = self.make_deployment(project=p)
        self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}],
                        resource_display_format='consolidated')
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.lines.get().manpower_deployment_id, d.pk)
        pdf_data = build_invoice_data(invoice, self.profile)
        self.assertEqual(len(pdf_data.lines), 1)
        self.assertEqual(pdf_data.lines[0].project_name, 'Manpower Supply Services')
        self.assertEqual(pdf_data.total, invoice.total)

    def test_name_display_preserves_resource_and_designation_without_internal_ids(self):
        d = self.make_deployment(resource_name='External QA resource', designation='QA Engineer')
        self.post_lines([{'name': 'QA Engineer', 'days': '1', 'deployment': str(d.pk)}],
                        resource_display_format='name')
        pdf_data = build_invoice_data(Invoice.objects.get(), self.profile)
        self.assertEqual(pdf_data.lines[0].project_name, 'External QA resource')
        self.assertIn('QA Engineer', pdf_data.lines[0].description)
        self.assertNotIn(f'deployment {d.pk}', pdf_data.lines[0].description.lower())
        self.assertNotIn(str(d.pk), pdf_data.lines[0].project_name)

    def test_deployment_screen_and_recurring_draft_prefill(self):
        d = self.make_deployment(recurring=True)
        self.assertContains(self.client.get(reverse('manpower_deployments')), 'Prepare invoice')
        self.assertContains(self.client.get(reverse('manpower_deployment_add')), 'Billing rate')
        url = reverse('new_invoice') + f'?client={self.client_obj.pk}&service_type=general_manpower&deployment={d.pk}&billing_year=2026&billing_mon=10'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['initial']['lines'][0]['deployment'], d.pk)

    def test_manpower_invoice_uses_existing_gst_and_accounting_posting(self):
        profile_gstin_prefix = '06AABFQ2999K1Z'
        self.profile.gstin = profile_gstin_prefix + gst.gstin_checksum(profile_gstin_prefix)
        self.profile.save(update_fields=['gstin'])
        client_gstin_prefix = '06AAACI1234A1Z'
        self.client_obj.gstin = client_gstin_prefix + gst.gstin_checksum(client_gstin_prefix)
        self.client_obj.country = 'India'
        self.client_obj.tax_treatment = gst.DOMESTIC_GST
        self.client_obj.state_code = '06'
        self.client_obj.save(update_fields=['gstin', 'country', 'tax_treatment', 'state_code'])

        response = self.post_lines([{'name': 'QA Engineer', 'days': '1', 'unit': '1000', 'method': 'monthly'}])
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.taxable_value, invoice.cgst_amount, invoice.sgst_amount),
                         (D('1000.00'), D('90.00'), D('90.00')))
        ledger.finalize_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.FINALIZED)
        self.assertIsNotNone(invoice.ledger_entry)
        customer_party = ('customer', str(self.client_obj.pk))
        self.assertEqual(postings.party_balance(accounting_services.mapped_account('invoice.ar'), *customer_party),
                         D('1180.00'))
        self.assertEqual(accounting_services.balance(accounting_services.mapped_account('invoice.revenue')),
                         D('1000.00'))
