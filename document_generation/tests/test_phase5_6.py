import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from access_control.models import UserProfile
from document_generation import automation_services
from document_generation.automation_services import (
    evaluate_compliance, run_document_automation,
)
from document_generation.models import (
    Document, DocumentApproval, DocumentAutomationExecution, DocumentAutomationLock,
    DocumentAutomationRule, DocumentComplianceRequirement, DocumentComplianceReview,
    DocumentCorrectiveAction, DocumentCorrectiveActionEvent, DocumentNotification,
    DocumentRenewal, DocumentShare, DocumentShareEvent, DocumentShareSignatory,
    DocumentSignedCopy, DocumentType, DocumentParty, GeneratedDocument,
)
from document_generation.sharing_services import (
    create_share, record_acknowledgement, record_delivery, record_reminder,
    upload_signed_copy, verify_signed_copy, decline_signature,
)

User = get_user_model()


def pdf_upload(name='signed.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4\nTest signed file\n%%EOF', content_type='application/pdf')


class PhaseFiveSixBase(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_ctx = override_settings(EMPLOYEE_PRIVATE_ROOT=self.temp.name)
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)
        self.addCleanup(self.temp.cleanup)
        self.manager = User.objects.create_user(username='phase56-manager', password='pass')
        UserProfile.objects.create(user=self.manager, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        self.owner = User.objects.create_user(username='phase56-owner', password='pass')
        self.other = User.objects.create_user(username='phase56-other', password='pass')
        self.doc_type = DocumentType.objects.get(code='non-disclosure-agreement-nda')
        self.doc_type.renewal_enabled = True
        self.doc_type.renewal_lead_time_days = 90
        self.doc_type.save()
        file_path = Path(self.temp.name) / 'issued.pdf'
        file_path.write_bytes(b'%PDF-1.4\nOriginal issued file\n%%EOF')
        self.original_bytes = file_path.read_bytes()
        self.original = Document.objects.create(
            category='agreement', title='Original issued PDF', source_type='phase56-test',
            source_id='original-issued-file', file_path='issued.pdf',
            original_filename='issued.pdf', mime_type='application/pdf', size=file_path.stat().st_size,
        )
        self.document = GeneratedDocument.objects.create(
            document_number='QB-P56-2026-001', document_type=self.doc_type,
            status='draft', issue_date=timezone.localdate(), official_issue_date=timezone.localdate(),
            expiry_date=timezone.localdate() + timedelta(days=30), created_by=self.owner,
            file_document=self.original, pdf_file_document=self.original,
        )
        self.party = DocumentParty.objects.create(
            generated_document=self.document, role='employee', display_name='Known Employee',
            content_type=ContentType.objects.get_for_model(User), object_id=str(self.owner.pk),
        )
        self.document.status = 'approved'
        self.document.save()
        self.document.status = 'issued'
        self.document.save()

    def share_data(self, **overrides):
        data = {
            'recipient_type': 'external', 'recipient_name': 'Recipient One',
            'recipient_email': 'recipient@example.test', 'sharing_method': 'email',
            'acknowledgement_requested': True, 'signature_required': False,
            'parsed_signatories': [], 'next_follow_up_date': None, 'remarks': 'Sent manually',
        }
        data.update(overrides)
        return data


class DocumentSharingLifecycleTests(PhaseFiveSixBase):
    def test_shares_are_version_scoped_repeatable_and_blocked_for_ineligible_documents(self):
        first = create_share(self.document, self.manager, self.share_data())
        second = create_share(self.document, self.manager, self.share_data(recipient_name='Second Recipient'))
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.generated_document_id, self.document.pk)
        self.assertEqual(first.acknowledgement_status, 'pending')
        self.assertEqual(DocumentShareEvent.objects.filter(action='shared').count(), 2)
        with self.assertRaises(ValidationError):
            first.delete()
        with self.assertRaises(PermissionDenied):
            create_share(self.document, self.other, self.share_data())
        draft = GeneratedDocument.objects.create(
            document_number='QB-P56-DRAFT', document_type=self.doc_type, created_by=self.owner,
        )
        with self.assertRaises(ValidationError):
            create_share(draft, self.manager, self.share_data())
        cancelled = GeneratedDocument.objects.create(
            document_number='QB-P56-CANCELLED', document_type=self.doc_type,
            status='cancelled', cancellation_reason='Test cancellation', created_by=self.owner,
        )
        with self.assertRaises(ValidationError):
            create_share(cancelled, self.manager, self.share_data())

    def test_delivery_acknowledgement_and_reminders_are_separate_and_audited(self):
        share = create_share(self.document, self.manager, self.share_data())
        record_delivery(share, self.manager, 'Handed to the recipient', pdf_upload('delivery.pdf'))
        share.refresh_from_db()
        self.assertEqual(share.delivery_status, 'delivered')
        self.assertEqual(share.acknowledgement_status, 'pending')
        self.assertTrue(share.delivery_evidence.exists())
        record_reminder(share, self.manager, 'phone', 'Called recipient', timezone.localdate() + timedelta(days=2))
        evidence = pdf_upload('ack.pdf')
        record_acknowledgement(share, self.manager, {
            'status': 'acknowledged', 'acknowledgement_name': 'Recipient One',
            'method': 'physical', 'remarks': 'Signed receipt returned', 'evidence': evidence,
        })
        share.refresh_from_db()
        self.assertEqual(share.acknowledgement_status, 'acknowledged')
        self.assertIsNotNone(share.acknowledged_at)
        self.assertTrue(share.acknowledgement_evidence.sha256)
        self.assertTrue(share.events.filter(action='reminder_recorded').exists())
        self.assertTrue(share.events.filter(action='acknowledgement_updated').exists())
        with self.assertRaises(ValidationError):
            DocumentShareEvent.objects.filter(share=share).update(action='tampered')

    def test_multiple_signatories_require_verified_copies_and_preserve_original(self):
        share = create_share(self.document, self.manager, self.share_data(
            signature_required=True,
            parsed_signatories=[('Signer A', 'Director'), ('Signer B', 'Witness')],
        ))
        signers = list(share.signatories.order_by('id'))
        upload_signed_copy(signers[0], self.manager, pdf_upload('signer-a.pdf'))
        signers[0].refresh_from_db()
        self.assertEqual(signers[0].status, 'submitted')
        self.assertEqual(signers[0].verification_status, 'awaiting')
        self.assertEqual(share.signature_status, 'pending')
        with self.assertRaises(ValidationError):
            verify_signed_copy(signers[0], self.manager, 'rejected', '')
        verify_signed_copy(signers[0], self.manager, 'verified')
        share.refresh_from_db()
        self.assertEqual(share.signature_status, 'partially_signed')
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'issued')
        upload_signed_copy(signers[1], self.manager, pdf_upload('signer-b.pdf'))
        verify_signed_copy(signers[1], self.manager, 'verified')
        share.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(share.signature_status, 'fully_signed')
        self.assertEqual(self.document.status, 'signed')
        self.assertEqual(DocumentSignedCopy.objects.filter(signatory__share=share).count(), 2)
        self.assertTrue(all(copy.sha256 for copy in DocumentSignedCopy.objects.filter(signatory__share=share)))
        self.assertEqual(Path(self.temp.name, 'issued.pdf').read_bytes(), self.original_bytes)
        with self.assertRaises(ValidationError):
            DocumentSignedCopy.objects.filter(signatory=signers[0]).delete()

    def test_verification_rejection_requires_reason_and_uploads_are_retained(self):
        share = create_share(self.document, self.manager, self.share_data(
            signature_required=True, parsed_signatories=[('Signer', 'Employee')],
        ))
        signer = share.signatories.get()
        upload_signed_copy(signer, self.manager, pdf_upload())
        verify_signed_copy(signer, self.manager, 'rejected', 'Signature page is incomplete.')
        signer.refresh_from_db()
        self.assertEqual(signer.status, 'pending')
        self.assertEqual(signer.verification_status, 'rejected')
        upload_signed_copy(signer, self.manager, pdf_upload('replacement.pdf'))
        self.assertEqual(DocumentSignedCopy.objects.filter(signatory=signer).count(), 2)
        self.assertTrue(signer.signed_copies.filter(verification_status='rejected').exists())
        decline_signature(signer, self.manager, 'Recipient declined to sign.')
        self.assertEqual(share.signature_status, 'declined')

    def test_local_dashboard_does_not_expose_recipient_links_or_files(self):
        create_share(self.document, self.manager, self.share_data())
        self.client.force_login(self.manager)
        response = self.client.get(reverse('document_generation:sharing_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'secure recipient links are not enabled')
        self.client.force_login(self.other)
        denied = self.client.get(reverse('document_generation:share_detail', args=[1]))
        self.assertIn(denied.status_code, (403, 404))


class DocumentAutomationComplianceTests(PhaseFiveSixBase):
    def test_approval_reminders_are_configurable_idempotent_and_stop_after_decision(self):
        approver = User.objects.create_user(username='phase56-approver', password='pass')
        UserProfile.objects.create(user=approver, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        pending = GeneratedDocument.objects.create(
            document_number='QB-P56-PENDING', document_type=self.doc_type,
            status='pending_approval', created_by=self.owner,
        )
        approval = DocumentApproval.objects.create(
            generated_document=pending, approver=approver, approver_name=approver.username,
            status=DocumentApproval.PENDING,
            requested_at=timezone.now() - timedelta(days=3),
        )
        result = run_document_automation()
        self.assertFalse(result['locked'])
        self.assertTrue(DocumentNotification.objects.filter(
            user=approver, generated_document=pending, action='approval_reminder',
        ).exists())
        run_document_automation()
        self.assertEqual(DocumentNotification.objects.filter(
            user=approver, generated_document=pending, action='approval_reminder',
        ).count(), 1)
        approval.status = DocumentApproval.APPROVED
        approval.decided_at = timezone.now()
        approval.save()
        run_document_automation()
        self.assertEqual(DocumentNotification.objects.filter(
            user=approver, generated_document=pending, action='approval_reminder',
        ).count(), 1)
        rule = DocumentAutomationRule.objects.get(code='approval-reminders')
        self.assertEqual(rule.intervals_days, [1, 3, 5])

    def test_expiry_and_renewal_reminders_use_actual_expiry_and_link_tracking(self):
        result = run_document_automation()
        self.assertFalse(result['locked'])
        self.assertTrue(DocumentNotification.objects.filter(
            generated_document=self.document, action='expiry_reminder',
        ).exists())
        renewal = DocumentRenewal.objects.get(generated_document=self.document)
        self.assertEqual(renewal.status, 'due')
        self.assertEqual(renewal.lead_time_days, 90)
        self.assertFalse(DocumentSignedCopy.objects.exists())

    def test_compliance_checks_are_configurable_and_review_history_is_append_only(self):
        requirement = DocumentComplianceRequirement.objects.create(
            name='NDA per employee policy', document_type=self.doc_type, recipient_type='employee',
            mandatory=True, responsible_person=self.manager, review_frequency_days=30,
        )
        reviews = evaluate_compliance([requirement], force=True, actor=self.manager)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].status, 'compliant')
        self.assertEqual(reviews[0].party_id, self.party.pk)
        self.assertEqual(reviews[0].reviewed_by, self.manager)
        self.assertEqual(evaluate_compliance([requirement], actor=self.manager), [])
        requirement.acknowledgement_required = True
        requirement.save()
        second = evaluate_compliance([requirement], force=True, actor=self.manager)
        self.assertEqual(second[0].status, 'pending')
        self.assertIn('acknowledgement', second[0].reason.lower())
        with self.assertRaises(ValidationError):
            DocumentComplianceReview.objects.filter(pk=reviews[0].pk).update(status='compliant')

    def test_failed_automation_is_recorded_retryable_and_global_lock_prevents_duplicate_runner(self):
        rule = DocumentAutomationRule.objects.create(
            code='test-failure-rule', name='Test failure', rule_type='approval', intervals_days=[1],
        )
        with mock.patch.object(automation_services, '_approval_reminders', side_effect=RuntimeError('temporary test failure')):
            result = run_document_automation(rule_ids=[rule.pk])
        failed = DocumentAutomationExecution.objects.filter(rule=rule, status='failed').latest('started_at')
        self.assertIn('temporary test failure', failed.error)
        rule.refresh_from_db()
        self.assertIn('temporary test failure', rule.last_error)
        DocumentAutomationLock.objects.filter(name='document-automation').update(
            locked_until=timezone.now() + timedelta(minutes=10), owner_token='held-by-test',
        )
        skipped = run_document_automation(rule_ids=[rule.pk])
        self.assertTrue(skipped['locked'])
        DocumentAutomationLock.objects.filter(name='document-automation').update(locked_until=None)
        retried = run_document_automation(rule_ids=[rule.pk])
        self.assertFalse(retried['locked'])
        self.assertEqual(DocumentAutomationExecution.objects.filter(rule=rule).count(), 2)
        self.assertTrue(DocumentAutomationExecution.objects.filter(rule=rule, status='completed').exists())

    def test_working_day_threshold_and_notification_preference(self):
        from document_generation.automation_services import _days_between
        from datetime import date
        self.assertEqual(_days_between(date(2026, 11, 13), date(2026, 11, 16), calendar_days=False), 1)
        rule = DocumentAutomationRule.objects.create(
            code='email-blocked', name='No email', rule_type='approval', intervals_days=[1], email_enabled=False,
        )
        rule.email_enabled = True
        with self.assertRaises(ValidationError):
            rule.full_clean()


class DocumentReportTests(PhaseFiveSixBase):
    def test_report_page_and_excel_pdf_exports_use_filtered_scope(self):
        create_share(self.document, self.manager, self.share_data())
        self.client.force_login(self.manager)
        page = self.client.get(reverse('document_generation:document_reports'), {'report': 'expiring'})
        self.assertEqual(page.status_code, 200)
        excel = self.client.get(reverse('document_generation:document_reports'), {'report': 'expiring', 'download': 'excel'})
        self.assertEqual(excel['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        pdf = self.client.get(reverse('document_generation:document_reports'), {'report': 'expiring', 'download': 'pdf'})
        self.assertEqual(pdf['Content-Type'], 'application/pdf')

    def test_notification_preferences_and_help_routes_are_available(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('document_generation:notification_preferences'))
        self.assertEqual(response.status_code, 200)
        requirement = DocumentComplianceRequirement.objects.create(
            name='NDA check', document_type=self.doc_type, recipient_type='employee',
        )
        self.assertTrue(requirement.is_active)
        self.assertEqual(reverse('document_generation:automation_dashboard'), '/documents/automation/')
