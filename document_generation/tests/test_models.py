from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from document_management.models import Document
from employee_management.storage import private_storage
from invoice_app.models import Client
from audit.models import AuditEvent

from document_generation.models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentAcknowledgement,
    DocumentActivityLog,
    DocumentApproval,
    DocumentCategory,
    DocumentClause,
    DocumentFieldDefinition,
    DocumentNumberSequence,
    DocumentParty,
    DocumentSignature,
    DocumentTemplate,
    DocumentTemplateClause,
    DocumentTemplateVersion,
    DocumentType,
    GeneratedDocument,
)
from document_generation.services import allocate_document_number


class DocumentGenerationModelTests(TestCase):
    def setUp(self):
        self.document_type = DocumentType.objects.get(code='non-disclosure-agreement-nda')

    def test_seeded_categories_and_document_types_are_configurable(self):
        self.assertEqual(DocumentCategory.objects.count(), 8)
        self.assertTrue(self.document_type.categories.filter(code='client').exists())
        self.assertEqual(self.document_type.number_prefix, 'QB-NDA')

    def test_company_profile_has_only_user_supplied_identity_defaults(self):
        profile = CompanyDocumentProfile.current()
        self.assertEqual(profile.legal_entity_name, 'Quantbud LLP')
        self.assertEqual(profile.brand_name, 'Synfowebs Technology')
        self.assertEqual(profile.llpin, '')
        self.assertEqual(CompanyDocumentProfile.objects.count(), 1)

    def test_profile_and_signatory_relationship(self):
        profile = CompanyDocumentProfile.current()
        signatory = CompanyDocumentSignatory.objects.create(
            profile=profile, name='Authorized representative', is_authorized=True,
        )
        self.assertEqual(signatory.profile, profile)

    def test_template_versions_fields_and_clause_versions(self):
        template = DocumentTemplate.objects.create(document_type=self.document_type, name='Base NDA')
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='{{ candidate_name }}',
        )
        field = DocumentFieldDefinition.objects.create(
            document_type=self.document_type, field_key='candidate_name',
            label='Candidate name', is_required=True,
        )
        clause = DocumentClause.objects.create(
            code='test-confidentiality', name='Confidentiality', clause_text='Draft clause text', version=1,
        )
        assignment = DocumentTemplateClause.objects.create(template_version=version, clause=clause)
        self.assertEqual(template.versions.get(), version)
        self.assertEqual(field.document_type, self.document_type)
        self.assertEqual(assignment.clause, clause)

    def test_template_active_version_must_belong_to_template(self):
        first = DocumentTemplate.objects.create(document_type=self.document_type, name='First')
        second = DocumentTemplate.objects.create(document_type=self.document_type, name='Second')
        version = DocumentTemplateVersion.objects.create(template=first, version_number=1)
        second.active_version = version
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_approved_template_and_clause_content_cannot_be_changed(self):
        user = get_user_model().objects.create_user(username='reviewer')
        template = DocumentTemplate.objects.create(document_type=self.document_type, name='Approved NDA')
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='Original',
            status=DocumentTemplateVersion.APPROVED, approved_by=user, approved_at=timezone.now(),
        )
        version.body_template = 'Edited'
        with self.assertRaises(ValidationError):
            version.save()

        clause = DocumentClause.objects.create(
            code='ip', name='IP ownership', clause_text='Original', version=1,
            approval_status=DocumentClause.APPROVED, approved_by=user, approved_at=timezone.now(),
        )
        clause.clause_text = 'Edited'
        with self.assertRaises(ValidationError):
            clause.save()

    def test_issued_document_snapshots_and_parties_are_immutable(self):
        document = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-001', document_type=self.document_type,
            rendered_content='Immutable PDF source',
            content_snapshot={'party': 'Original'}, issue_date=date(2026, 1, 1),
        )
        party = DocumentParty.objects.create(
            generated_document=document, role='client', display_name='Example Client',
            contact_snapshot={'address': 'Original'},
        )
        document.status = 'issued'
        document.save()
        document.rendered_content = 'Changed'
        with self.assertRaises(ValidationError):
            document.save()
        with self.assertRaises(ValidationError):
            document.delete()
        with self.assertRaises(ValidationError):
            GeneratedDocument.objects.filter(pk=document.pk).delete()

        party.display_name = 'Changed'
        with self.assertRaises(ValidationError):
            party.save()

    def test_document_number_uniqueness_and_sequence_allocation(self):
        first = allocate_document_number(self.document_type, date(2026, 11, 4))
        second = allocate_document_number(self.document_type, date(2026, 11, 4))
        next_year = allocate_document_number(self.document_type, date(2027, 1, 2))
        self.assertEqual(first, 'QB-NDA-2026-001')
        self.assertEqual(second, 'QB-NDA-2026-002')
        self.assertEqual(next_year, 'QB-NDA-2027-001')
        self.assertEqual(DocumentNumberSequence.objects.count(), 2)

        GeneratedDocument.objects.create(document_number=first, document_type=self.document_type)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GeneratedDocument.objects.create(document_number=first, document_type=self.document_type)

    def test_cancelled_documents_do_not_reuse_allocated_numbers(self):
        number = allocate_document_number(self.document_type, date(2026, 11, 4))
        cancelled = GeneratedDocument(
            document_number=number, document_type=self.document_type,
            status='cancelled', cancellation_reason='Issued in error.',
        )
        cancelled.save()
        self.assertEqual(
            allocate_document_number(self.document_type, date(2026, 11, 4)),
            'QB-NDA-2026-002',
        )

    def test_cancellation_reason_is_preserved_after_cancellation(self):
        document = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-008', document_type=self.document_type,
            status='issued',
        )
        document.status = 'cancelled'
        document.cancellation_reason = 'Cancelled by management.'
        document.save()
        document.cancellation_reason = 'Changed reason.'
        with self.assertRaises(ValidationError):
            document.save()

    def test_financial_year_sequence_and_never_reset_sequence(self):
        self.document_type.reset_frequency = DocumentType.RESET_FINANCIAL
        self.document_type.year_convention = DocumentType.YEAR_FINANCIAL
        self.document_type.number_prefix = 'QB-FY-NDA'
        self.document_type.save()
        self.assertEqual(
            allocate_document_number(self.document_type, date(2026, 3, 31)),
            'QB-FY-NDA-2025-26-001',
        )
        self.assertEqual(
            allocate_document_number(self.document_type, date(2026, 4, 1)),
            'QB-FY-NDA-2026-27-001',
        )

        self.document_type.reset_frequency = DocumentType.RESET_NEVER
        self.document_type.year_convention = DocumentType.YEAR_NONE
        self.document_type.save()
        self.assertEqual(allocate_document_number(self.document_type, date(2030, 1, 1)), 'QB-FY-NDA-001')
        self.assertEqual(allocate_document_number(self.document_type, date(2031, 1, 1)), 'QB-FY-NDA-002')

    def test_configured_period_and_year_conventions_must_match(self):
        self.document_type.year_convention = DocumentType.YEAR_FINANCIAL
        with self.assertRaises(ValidationError):
            self.document_type.full_clean()

    def test_document_parties_link_existing_client_without_duplication(self):
        client = Client.objects.create(name='Existing Client')
        document = GeneratedDocument.objects.create(
            document_number='QB-MCA-2026-001',
            document_type=DocumentType.objects.get(code='master-client-agreement-mca'),
        )
        party = DocumentParty.objects.create(
            generated_document=document,
            role='client',
            content_type=ContentType.objects.get_for_model(Client),
            object_id=str(client.pk),
            display_name=client.name,
            contact_snapshot={'name': client.name},
        )
        self.assertEqual(party.linked_record, client)
        self.assertEqual(Client.objects.count(), 1)

    def test_existing_document_index_is_reused_for_private_file_references(self):
        document_index = Document.objects.create(
            category='agreement', title='Issued document', source_type='document_generation',
            source_id='doc-1', file_path='document_generation/test.pdf',
        )
        generated = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-002', document_type=self.document_type,
            file_document=document_index,
        )
        self.assertEqual(generated.file_document, document_index)
        self.assertEqual(CompanyDocumentProfile._meta.get_field('logo').storage, private_storage)

    def test_digital_acknowledgement_requires_explicit_confirmation_and_timestamp(self):
        document = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-003', document_type=self.document_type,
        )
        acknowledgement = DocumentAcknowledgement(
            generated_document=document, acknowledgement_type=DocumentAcknowledgement.DIGITAL,
        )
        with self.assertRaises(ValidationError):
            acknowledgement.full_clean()
        acknowledgement.acknowledging_name = 'Employee'
        acknowledgement.acknowledged_at = timezone.now()
        acknowledgement.confirmation_text = 'I confirm I received this document.'
        acknowledgement.full_clean()

    def test_cancellation_requires_reason_and_audit_history_is_append_only(self):
        document = GeneratedDocument(
            document_number='QB-NDA-2026-004', document_type=self.document_type, status='cancelled',
        )
        with self.assertRaises(ValidationError):
            document.save()
        document.cancellation_reason = 'Cancelled by management.'
        document.save()

        log = DocumentActivityLog.objects.create(
            generated_document=document, action='cancel', details={'reason': 'Cancelled by management.'},
        )
        log.action = 'updated'
        with self.assertRaises(ValidationError):
            log.save()
        with self.assertRaises(ValidationError):
            DocumentActivityLog.objects.filter(pk=log.pk).update(action='updated')
        with self.assertRaises(ValidationError):
            log.delete()

    def test_audit_events_exclude_document_body_and_party_snapshots(self):
        document = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-006', document_type=self.document_type,
            rendered_content='Private agreement content',
            content_snapshot={'salary': 'sensitive'},
        )
        event = AuditEvent.objects.get(
            app_label='document_generation', model='GeneratedDocument', object_id=str(document.pk),
        )
        self.assertIn('rendered_content', event.changes)
        self.assertIn('content_snapshot', event.changes)
        self.assertNotIn('Private agreement content', str(event.changes))
        self.assertNotIn('sensitive', str(event.changes))

    def test_approval_and_signature_history_refer_to_generated_document(self):
        document = GeneratedDocument.objects.create(
            document_number='QB-NDA-2026-005', document_type=self.document_type,
        )
        approval = DocumentApproval.objects.create(generated_document=document)
        signature = DocumentSignature.objects.create(
            generated_document=document, signer_name='Authorized representative',
        )
        acknowledgement = DocumentAcknowledgement.objects.create(
            generated_document=document, acknowledgement_type=DocumentAcknowledgement.PHYSICAL,
        )
        self.assertEqual(approval.generated_document, document)
        self.assertEqual(signature.generated_document, document)
        self.assertEqual(acknowledgement.generated_document, document)
