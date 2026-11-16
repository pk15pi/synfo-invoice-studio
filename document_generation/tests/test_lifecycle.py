import tempfile
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from access_control.models import UserProfile
from document_management.models import Document
from document_generation.lifecycle_services import (
    cancel_document, confirm_document_signed, decide_approval, evaluate_expired_documents,
    issue_document, mark_document_sent, notify_upcoming_expiries, submit_document,
)
from document_generation.models import (
    DocumentActivityLog, DocumentApproval, DocumentNotification, DocumentType, GeneratedDocument,
)


User = get_user_model()


class DocumentLifecycleServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='doc-owner', password='pass')
        self.approver_one = User.objects.create_user(username='doc-approver-1', password='pass')
        self.approver_two = User.objects.create_user(username='doc-approver-2', password='pass')
        self.other = User.objects.create_user(username='doc-other', password='pass')
        self.manager = User.objects.create_user(username='doc-manager', password='pass')
        UserProfile.objects.create(user=self.approver_one, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        UserProfile.objects.create(user=self.approver_two, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        UserProfile.objects.create(user=self.manager, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        self.document_type = DocumentType.objects.get(code='non-disclosure-agreement-nda')
        self.document_type.approval_required = True
        self.document_type.approval_workflow = [
            {'user_id': self.approver_one.pk, 'user_name': self.approver_one.username},
            {'user_id': self.approver_two.pk, 'user_name': self.approver_two.username},
        ]
        self.document_type.requires_expiry = False
        self.document_type.save()
        self.document = GeneratedDocument.objects.create(
            document_number='QB-LC-2026-001', document_type=self.document_type,
            rendered_content='Private draft content', content_snapshot={'field_values': {}},
            created_by=self.owner,
        )

    def test_multi_level_approval_sequence_and_revision_decision(self):
        submit_document(self.document, self.owner)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'pending_approval')
        steps = list(self.document.approvals.order_by('level'))
        self.assertEqual([step.status for step in steps], [DocumentApproval.PENDING, DocumentApproval.WAITING])
        with self.assertRaises(PermissionDenied):
            decide_approval(steps[0], self.other, 'approve')
        decide_approval(steps[0], self.approver_one, 'approve', 'Reviewed.')
        steps[1].refresh_from_db()
        self.assertEqual(steps[1].status, DocumentApproval.PENDING)
        with self.assertRaises(ValidationError):
            decide_approval(steps[0], self.approver_one, 'approve')
        decide_approval(steps[1], self.approver_two, 'revise', 'Correct the recipient address.')
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'revision_requested')
        steps[1].refresh_from_db()
        self.assertEqual(steps[1].comments, 'Correct the recipient address.')
        self.assertTrue(self.document.activity.filter(action='revision_requested').exists())
        self.assertTrue(DocumentNotification.objects.filter(user=self.owner, action='revision_requested').exists())
        steps[0].comments = 'Silently changed'
        with self.assertRaises(ValidationError):
            steps[0].save()
        with self.assertRaises(ValidationError):
            DocumentApproval.objects.filter(pk=steps[0].pk).delete()

    def test_rejection_requires_a_reason_and_stops_later_levels(self):
        submit_document(self.document, self.owner)
        first = self.document.approvals.get(level=1)
        with self.assertRaises(ValidationError):
            decide_approval(first, self.approver_one, 'reject')
        decide_approval(first, self.approver_one, 'reject', 'Incorrect terms.')
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, 'rejected')
        self.assertEqual(self.document.approvals.get(level=2).status, DocumentApproval.WITHDRAWN)

    def test_issue_requires_completed_approval_and_preserved_output(self):
        self.manager.is_staff = True
        self.manager.save(update_fields=('is_staff',))
        with self.assertRaises(ValidationError):
            issue_document(self.document, self.manager)
        with tempfile.TemporaryDirectory() as private_root, override_settings(EMPLOYEE_PRIVATE_ROOT=private_root):
            output = Path(private_root) / 'generated' / 'final.pdf'
            output.parent.mkdir(parents=True)
            output.write_bytes(b'%PDF-1.4 lifecycle test')
            ref = Document.objects.create(
                category='agreement', title='Lifecycle PDF', source_type='test_lifecycle',
                source_id='issued-file', file_path='generated/final.pdf', original_filename='final.pdf',
                mime_type='application/pdf', size=output.stat().st_size,
            )
            self.document.pdf_file_document = ref
            self.document.file_document = ref
            self.document.save(update_fields=('pdf_file_document', 'file_document', 'updated_at'))
            self.document_type.requires_expiry = True
            self.document_type.save(update_fields=('requires_expiry', 'updated_at'))
            submit_document(self.document, self.owner)
            decide_approval(self.document.approvals.get(level=1), self.approver_one, 'approve')
            decide_approval(self.document.approvals.get(level=2), self.approver_two, 'approve')
            with self.assertRaises(ValidationError):
                issue_document(self.document, self.manager)
            expiry = timezone.localdate() + timedelta(days=180)
            issue_document(self.document, self.manager, timezone.localdate(), expiry)
            self.document.refresh_from_db()
            self.assertEqual(self.document.status, 'issued')
            self.assertEqual(self.document.official_issue_date, timezone.localdate())
            with self.assertRaises(ValidationError):
                issue_document(self.document, self.manager)
            mark_document_sent(self.document, self.manager, 'Legal Team', 'email', 'Sent manually.')
            self.document.refresh_from_db()
            self.assertEqual(self.document.status, 'sent')
            confirm_document_signed(self.document, self.manager, 'Authorized signer', 'Paper copy checked.')
            self.document.refresh_from_db()
            self.assertEqual(self.document.status, 'signed')
            self.assertTrue(self.document.signatures.filter(method='manual', status='signed').exists())
            with self.assertRaises(ValidationError):
                cancel_document(self.document, self.manager, '')
            cancel_document(self.document, self.manager, 'Agreement entered in error.')
            self.document.refresh_from_db()
            self.assertEqual(self.document.status, 'cancelled')
            self.assertTrue(self.document.cancelled_at)
            self.assertEqual(self.document.cancelled_by, self.manager)

    def test_expiry_is_evaluated_and_notified_without_deleting_document(self):
        expiring = GeneratedDocument.objects.create(
            document_number='QB-LC-2026-002', document_type=self.document_type,
            status='issued', issue_date=date(2024, 1, 1), expiry_date=timezone.localdate() - timedelta(days=1),
            created_by=self.owner,
        )
        evaluate_expired_documents()
        expiring.refresh_from_db()
        self.assertEqual(expiring.status, 'expired')
        self.assertTrue(DocumentActivityLog.objects.filter(generated_document=expiring, action='expired').exists())
        self.assertTrue(GeneratedDocument.objects.filter(pk=expiring.pk).exists())

        upcoming = GeneratedDocument.objects.create(
            document_number='QB-LC-2026-003', document_type=self.document_type,
            status='issued', issue_date=timezone.localdate(), expiry_date=timezone.localdate() + timedelta(days=10),
            created_by=self.owner,
        )
        notify_upcoming_expiries()
        notify_upcoming_expiries()
        self.assertEqual(DocumentNotification.objects.filter(
            generated_document=upcoming, action='expiring_soon',
        ).count(), 1)


class DocumentLifecyclePageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='page-owner', password='pass')
        self.approver = User.objects.create_user(username='page-approver', password='pass')
        self.other = User.objects.create_user(username='page-other', password='pass')
        self.document_type = DocumentType.objects.get(code='non-disclosure-agreement-nda')
        self.document = GeneratedDocument.objects.create(
            document_number='QB-LP-2026-001', document_type=self.document_type,
            created_by=self.owner,
        )

    def test_dashboard_and_detail_render_and_scope_to_owner(self):
        self.client.force_login(self.owner)
        dashboard = self.client.get(reverse('document_generation:lifecycle_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Document Lifecycle Dashboard')
        detail = self.client.get(reverse('document_generation:document_detail', args=(self.document.pk,)))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Lifecycle activity')
        self.client.force_login(self.other)
        hidden = self.client.get(reverse('document_generation:document_detail', args=(self.document.pk,)))
        self.assertEqual(hidden.status_code, 403)

    def test_approval_workflow_page_is_manager_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('document_generation:approval_workflows')).status_code, 403)
        manager = User.objects.create_user(username='page-manager', password='pass')
        UserProfile.objects.create(user=manager, role=UserProfile.Role.DOCUMENT_CONTROLLER)
        self.client.force_login(manager)
        response = self.client.get(reverse('document_generation:approval_workflows'), {'type': self.document_type.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approval Workflow Configuration')
