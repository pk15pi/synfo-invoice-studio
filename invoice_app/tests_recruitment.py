from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounting import services as acc
from accounting import postings
from . import gst, ledger
from .models import (BusinessProfile, Client, Invoice, InvoiceLine, InvoicePayment, RecruitmentAgreement,
                     RecruitmentCandidate, RecruitmentPlacement, RecruitmentRequisition)
from .pdf.from_models import build_invoice_data
from .pdf.formats import render_for_invoice
from .recruitment_billing import approve_fee, calculate_fee


def gstin(state='06', pan='AABFQ2999K'):
    prefix = f'{state}{pan}1Z'
    return prefix + gst.gstin_checksum(prefix)


class RecruitmentPlacementTests(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.profile.amount_rounding = BusinessProfile.ROUND_PAISE
        self.profile.gstin = gstin()
        self.profile.save(update_fields=['amount_rounding', 'gstin'])
        self.client_a = Client.objects.create(name='Recruitment Client A')
        self.candidate = RecruitmentCandidate.objects.create(candidate_name='Candidate Internal Name',
                                                              default_designation='Software Engineer')

    def agreement(self, client=None, percentage='8', method='percentage', fixed_fee=None, **extra):
        values = dict(client=client or self.client_a, billing_method=method,
                      percentage=D(percentage) if percentage else None,
                      fixed_fee=D(fixed_fee) if fixed_fee is not None else None,
                      ctc_basis=RecruitmentAgreement.ANNUAL_CTC, effective_start=date(2026, 1, 1),
                      agreement_reference='REC-AG-01', billing_trigger=RecruitmentAgreement.JOINING,
                      status='active')
        values.update(extra)
        obj = RecruitmentAgreement(**values)
        obj.full_clean()
        obj.save()
        return obj

    def placement(self, agreement=None, **extra):
        values = dict(client=agreement.client if agreement else self.client_a,
                      candidate=self.candidate, agreement=agreement or self.agreement(),
                      designation='Software Engineer', finalized_annual_ctc=D('800000'),
                      ctc_finalized=True, ctc_approved_on=date(2026, 10, 1),
                      client_approval_reference='Client approval email 42',
                      offer_reference='OFFER-42', joining_date=date(2026, 10, 15))
        values.update(extra)
        obj = RecruitmentPlacement(**values)
        obj.full_clean()
        obj.save()
        return obj

    def post_placement_invoice(self, placement, **extra):
        data = {
            'client': placement.client_id, 'invoice_date': '2026-11-02', 'billing_year': '2026',
            'billing_mon': '10', 'terms_mode': 'custom', 'terms_text': 'Net 30',
            'service_type': Invoice.RECRUITMENT_PLACEMENT, 'resource_display_format': 'designation',
            'action': 'save', 'line_project': [''], 'line_name': [''], 'line_desc': [''],
            'line_from': [''], 'line_to': [''], 'line_days': ['1'], 'line_unit': [''],
            'line_amount': [''], 'line_deployment': [''], 'line_method': [''],
            'line_proration': [''], 'line_remarks': [''], 'line_placement': [str(placement.pk)],
        }
        data.update(extra)
        return self.client.post(reverse('new_invoice'), data)

    def test_client_specific_percentage_examples_and_fixed_fee(self):
        scenarios = [('8', '800000', '64000.00'), ('10', '1200000', '120000.00'),
                     ('12', '1000000', '120000.00')]
        for percentage, ctc, expected in scenarios:
            c = Client.objects.create(name=f'Client {percentage}')
            a = self.agreement(client=c, percentage=percentage, agreement_reference=f'REC-{percentage}')
            p = self.placement(a, client=c, finalized_annual_ctc=D(ctc))
            self.assertEqual(calculate_fee(p)[0], D(expected))
        fixed = self.agreement(method='fixed', percentage=None, fixed_fee='40000', agreement_reference='FIXED-40K')
        p = self.placement(fixed, ctc_finalized=False, finalized_annual_ctc=None,
                           ctc_approved_on=None, joining_date=date(2026, 10, 20))
        self.assertEqual(calculate_fee(p)[0], D('40000.00'))

    def test_expected_ctc_and_unmet_trigger_cannot_be_approved(self):
        a = self.agreement()
        expected_only = self.placement(a, expected_annual_ctc=D('1000000'),
            finalized_annual_ctc=None, ctc_finalized=False, ctc_approved_on=None)
        self.assertFalse(expected_only.can_approve_fee)
        with self.assertRaisesMessage(ValidationError, 'client-approved CTC'):
            approve_fee(expected_only)
        not_joined = self.placement(a, finalized_annual_ctc=D('900000'), joining_date=None)
        self.assertFalse(not_joined.trigger_met)
        with self.assertRaisesMessage(ValidationError, 'billing trigger'):
            approve_fee(not_joined)

    def test_triggered_fee_approval_freezes_terms_and_links_invoice(self):
        agreement = self.agreement(percentage='8', guarantee_days=90, payment_terms='Net 30')
        placement = self.placement(agreement, finalized_annual_ctc=D('800000'))
        approve_fee(placement)
        placement.refresh_from_db()
        self.assertEqual(placement.fee_amount, D('64000.00'))
        self.assertEqual(placement.percentage_snapshot, D('8.000'))
        self.assertEqual((placement.guarantee_start, placement.guarantee_end), (date(2026, 10, 15), date(2027, 1, 13)))

        response = self.post_placement_invoice(placement)
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        line = invoice.lines.get()
        placement.refresh_from_db()
        self.assertEqual(invoice.service_type, Invoice.RECRUITMENT_PLACEMENT)
        self.assertEqual(invoice.total, D('64000.00'))
        self.assertEqual((placement.invoice_id, placement.invoice_line_id), (invoice.pk, line.pk))
        self.assertEqual(line.recruitment_placement_id, placement.pk)

        # A pending draft may be edited, but cannot create a second invoice for the same placement.
        self.assertEqual(self.client.get(reverse('invoice_edit', args=[invoice.pk])).status_code, 200)
        duplicate = self.post_placement_invoice(placement)
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, 'already has an invoice')
        self.assertEqual(Invoice.objects.count(), 1)

        agreement.percentage = D('10')
        agreement.save(update_fields=['percentage', 'updated_at'])
        placement.refresh_from_db()
        self.assertEqual(placement.percentage_snapshot, D('8.000'))
        self.assertEqual(placement.fee_amount, D('64000.00'))
        with self.assertRaisesMessage(ValidationError, 'snapshot is locked'):
            approve_fee(placement)

    def test_fee_override_requires_reason_and_is_audited(self):
        from audit.models import AuditEvent
        placement = self.placement(self.agreement())
        with self.assertRaisesMessage(ValidationError, 'requires a reason'):
            approve_fee(placement, override_amount=D('60000'))
        placement = approve_fee(placement, override_amount=D('60000'), override_reason='Client-approved concession REF-7')
        event = AuditEvent.objects.filter(app_label='invoice_app', model='RecruitmentPlacement',
                                          object_id=str(placement.pk), reason='Client-approved concession REF-7').first()
        self.assertIsNotNone(event)
        self.assertTrue(placement.fee_approved)
        self.assertEqual(placement.fee_amount, D('60000.00'))

    def test_candidate_can_be_placed_with_multiple_clients_and_optional_requisition(self):
        b = Client.objects.create(name='Recruitment Client B')
        p1 = self.placement(self.agreement(), joining_date=date(2026, 10, 10))
        p2 = self.placement(self.agreement(client=b, agreement_reference='REC-B'), client=b,
                            joining_date=date(2026, 10, 12))
        req = RecruitmentRequisition.objects.create(client=b, title='Senior Engineer', reference='REQ-9')
        p3 = self.placement(self.agreement(client=b, agreement_reference='REC-B2'), client=b, requisition=req,
                            joining_date=date(2026, 10, 15))
        self.assertEqual({p1.candidate_id, p2.candidate_id, p3.candidate_id}, {self.candidate.pk})
        self.assertEqual({p1.client_id, p2.client_id, p3.client_id}, {self.client_a.pk, b.pk})

    def test_pdf_privacy_gst_accounting_payment_and_credit_mapping(self):
        self.client_a.country = 'India'
        self.client_a.tax_treatment = gst.DOMESTIC_GST
        self.client_a.gstin = gstin()
        self.client_a.state_code = '06'
        self.client_a.save(update_fields=['country', 'tax_treatment', 'gstin', 'state_code'])
        agreement = self.agreement(percentage='10', gst_rate=D('18'), sac_code='998314')
        placement = self.placement(agreement, finalized_annual_ctc=D('1200000'),
                                   invoice_display_format='designation')
        approve_fee(placement)
        name_preview = self.post_placement_invoice(placement, action='preview', resource_display_format='name')
        self.assertEqual(name_preview.status_code, 200)
        import fitz
        name_text = '\n'.join(page.get_text() for page in fitz.open(stream=name_preview.content, filetype='pdf'))
        self.assertIn('Candidate Internal Name', name_text)
        self.assertNotIn('1200000', name_text)
        response = self.post_placement_invoice(placement)
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.taxable_value, invoice.cgst_amount, invoice.sgst_amount),
                         (D('120000.00'), D('10800.00'), D('10800.00')))
        pdf_data = build_invoice_data(invoice, self.profile)
        self.assertEqual(pdf_data.lines[0].project_name, 'Software Engineer')
        self.assertNotIn('Candidate Internal Name', str(pdf_data.lines))
        self.assertNotIn('1200000', str(pdf_data.lines))
        rendered = render_for_invoice(invoice, self.profile)
        self.assertTrue(rendered.startswith(b'%PDF'))

        ledger.finalize_invoice(invoice)
        self.assertEqual(acc.balance(acc.mapped_account('invoice.recruitment_revenue')), D('120000.00'))
        self.assertEqual(acc.balance(acc.mapped_account('invoice.revenue')), D('0.00'))
        payment = InvoicePayment.objects.create(invoice=invoice, receipt_date=date(2026, 11, 3),
                                               amount=invoice.grand_total, mode='bank')
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_summary()['status'], 'Paid')
        self.assertEqual(payment.amount, D('141600.00'))

    def test_candidate_form_prevents_duplicate_name_without_reference(self):
        response = self.client.post(reverse('recruitment_candidate_add'), {
            'candidate_name': self.candidate.candidate_name,
            'default_designation': 'Software Engineer', 'candidate_reference': '', 'notes': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.assertEqual(RecruitmentCandidate.objects.count(), 1)

    def test_placement_form_requires_approval_and_can_approve_after_joining(self):
        agreement = self.agreement(percentage='8')
        response = self.client.post(reverse('recruitment_placement_add'), {
            'client': self.client_a.pk, 'candidate': self.candidate.pk, 'requisition': '',
            'agreement': agreement.pk, 'designation': 'Software Engineer',
            'expected_annual_ctc': '1000000', 'finalized_annual_ctc': '800000',
            'monthly_ctc': '', 'fixed_components': '', 'variable_components': '',
            'employer_contributions': '', 'other_ctc_components': '', 'agreed_basis_amount': '',
            'ctc_finalized': 'on', 'ctc_approved_on': '2026-10-01',
            'client_approval_reference': 'Approved offer REF-8', 'offer_reference': 'OFFER-8',
            'offer_accepted_on': '', 'joining_date': '2026-10-15', 'custom_trigger_date': '',
            'custom_trigger_reference': '', 'invoice_display_format': 'designation',
            'fee_override_amount': '', 'fee_override_reason': '', 'action': 'approve'})
        self.assertEqual(response.status_code, 302)
        placement = RecruitmentPlacement.objects.get()
        self.assertTrue(placement.fee_approved)
        self.assertEqual(placement.fee_amount, D('64000.00'))

    def test_dashboard_and_agreement_pages_render_filters_and_guarantee_notice(self):
        agreement = self.agreement(guarantee_days=90)
        placement = self.placement(agreement)
        approve_fee(placement)
        self.assertContains(self.client.get(reverse('recruitment_dashboard')), 'Candidate placements')
        self.assertContains(self.client.get(reverse('recruitment_dashboard') + '?client=' + str(self.client_a.pk)),
                            'Finalized annual CTC')
        self.assertEqual(self.client.get(reverse('recruitment_agreement_add')).status_code, 200)
        self.assertEqual(self.client.get(reverse('recruitment_placement_add')).status_code, 200)
