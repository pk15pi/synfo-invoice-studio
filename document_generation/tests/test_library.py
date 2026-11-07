from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from document_generation.models import (
    DocumentCategory, DocumentClause, DocumentTemplate, DocumentTemplateClause,
    DocumentTemplateVersion, DocumentType,
)
from document_generation.services import (
    create_clause_version, create_template_version, transition_clause,
    transition_template_version,
)
from document_generation.template_engine import resolve_template
from document_generation.models import CompanyDocumentProfile


class StandardDocumentLibraryTests(TestCase):
    def setUp(self):
        self.document_type = DocumentType.objects.get(code='offer-letter')
        self.staff = get_user_model().objects.create_user(
            username='library-admin', password='test-password', is_staff=True,
        )

    def test_library_pages_render_and_template_creation_starts_with_a_draft_version(self):
        for name in ('library', 'categories', 'types', 'templates', 'template_versions', 'clauses'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f'document_generation:{name}')).status_code, 200)

        response = self.client.post(reverse('document_generation:template_new'), {
            'name': 'Offer workflow test', 'code': '', 'document_type': self.document_type.pk,
            'category': self.document_type.categories.first().pk,
            'description': 'Test template', 'applicable_party_type': 'employee',
            'business_scenario': 'Employment', 'template_format': 'markdown',
            'default_output_format': 'docx', 'effective_date': '', 'retirement_date': '',
        })
        template = DocumentTemplate.objects.get(name='Offer workflow test')
        version = template.versions.get()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(template.code)
        self.assertEqual(template.status, DocumentTemplate.STATUS_DRAFT)
        self.assertEqual(version.status, DocumentTemplateVersion.DRAFT)
        self.assertEqual(version.output_formats, ['docx'])
        self.assertTrue(AuditEvent.objects.filter(
            app_label='document_generation', model='DocumentTemplateVersion',
            object_id=str(version.pk), action='create',
        ).exists())

    def test_categories_and_document_types_can_be_created_edited_and_codes_are_unique(self):
        response = self.client.post(reverse('document_generation:categories'), {
            'code': 'phase3-test-category', 'name': 'Phase 3 Test Category',
            'description': 'Test category', 'display_order': '90', 'is_active': 'on',
        })
        self.assertEqual(response.status_code, 302)
        category = DocumentCategory.objects.get(code='phase3-test-category')

        response = self.client.post(f"{reverse('document_generation:categories')}?edit={category.pk}", {
            'code': 'phase3-test-category', 'name': 'Phase 3 Test Category',
            'description': 'Updated category', 'display_order': '91',
        })
        self.assertEqual(response.status_code, 302)
        category.refresh_from_db()
        self.assertFalse(category.is_active)
        self.assertEqual(category.description, 'Updated category')

        type_data = {
            'code': 'phase3-test-type', 'name': 'Phase 3 Test Type',
            'categories': [str(category.pk)], 'description': '', 'number_prefix': 'QB-TST',
            'number_padding': '3', 'year_convention': 'calendar',
            'reset_frequency': 'calendar_year', 'approval_required': 'on',
            'default_output_formats': '["docx", "pdf"]', 'is_active': 'on',
        }
        response = self.client.post(reverse('document_generation:types'), type_data)
        self.assertEqual(response.status_code, 302)
        created_type = DocumentType.objects.get(code='phase3-test-type')
        self.assertTrue(created_type.categories.filter(pk=category.pk).exists())

        duplicate = {**type_data, 'name': 'Duplicate code type'}
        response = self.client.post(reverse('document_generation:types'), duplicate)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DocumentType.objects.filter(name='Duplicate code type').exists())

    def test_template_review_approval_activation_and_new_version_preserve_history(self):
        template = DocumentTemplate.objects.create(
            document_type=self.document_type, name='Versioned offer', code='versioned-offer',
        )
        first = create_template_version(template, change_description='Initial')
        first.body_template = 'Original {{ employee_name }}'
        first.save()
        transition_template_version(first, 'submit')
        transition_template_version(first, 'approve', actor=self.staff)
        transition_template_version(first, 'activate', actor=self.staff)
        template.refresh_from_db()
        self.assertEqual(template.active_version_id, first.pk)
        self.assertEqual(template.status, DocumentTemplate.STATUS_ACTIVE)

        second = create_template_version(template, self.staff, 'Add revised language')
        self.assertEqual(second.body_template, first.body_template)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(second.status, DocumentTemplateVersion.DRAFT)
        self.assertEqual(template.active_version_id, first.pk)
        first.body_template = 'Changed'
        with self.assertRaises(ValidationError):
            first.save()

    def test_only_staff_can_approve_or_activate_template_and_clause(self):
        template = DocumentTemplate.objects.create(
            document_type=self.document_type, name='Permission offer', code='permission-offer',
        )
        version = create_template_version(template)
        version.body_template = 'Offer'
        version.save()
        transition_template_version(version, 'submit')
        with self.assertRaises(PermissionDenied):
            transition_template_version(version, 'approve')
        transition_template_version(version, 'approve', actor=self.staff)
        transition_template_version(version, 'activate', actor=self.staff)

        clause = DocumentClause.objects.create(
            code='permission-test', name='Permission test', category='confidentiality',
            clause_text='Confidential information must be protected.',
        )
        transition_clause(clause, 'submit')
        with self.assertRaises(PermissionDenied):
            transition_clause(clause, 'approve')
        transition_clause(clause, 'approve', actor=self.staff)

    def test_template_deactivation_reactivation_retirement_and_historical_protection(self):
        template = DocumentTemplate.objects.create(
            document_type=self.document_type, name='Lifecycle offer', code='lifecycle-offer',
        )
        version = create_template_version(template, change_description='Initial')
        version.body_template = 'Lifecycle text.'
        version.save()
        transition_template_version(version, 'submit')
        transition_template_version(version, 'approve', actor=self.staff)
        transition_template_version(version, 'activate', actor=self.staff)
        transition_template_version(version, 'deactivate', actor=self.staff)
        template.refresh_from_db()
        self.assertEqual(template.status, DocumentTemplate.STATUS_INACTIVE)
        transition_template_version(version, 'activate', actor=self.staff)
        transition_template_version(version, 'retire', actor=self.staff)
        template.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(template.status, DocumentTemplate.STATUS_RETIRED)
        self.assertEqual(version.status, DocumentTemplateVersion.RETIRED)
        self.assertIsNotNone(version.retirement_date)

        template.active_version = version
        with self.assertRaises(ValidationError):
            template.full_clean()

        from document_generation.models import GeneratedDocument
        document = GeneratedDocument.objects.create(
            document_number='QB-LIFECYCLE-TEST', document_type=self.document_type,
            template_version=version,
        )
        with self.assertRaises(ProtectedError):
            version.delete()
        self.assertIsNotNone(document.pk)

    def test_clause_versions_preserve_approved_text_and_template_clause_selection_is_snapshotted(self):
        clause = DocumentClause.objects.create(
            code='optional-terms-test', name='Optional terms', category='payment',
            clause_text='Optional approved term.', default_inclusion=False,
            approval_status=DocumentClause.APPROVED, approved_by=self.staff,
            approved_at=timezone.now(), is_active=True,
        )
        mandatory = DocumentClause.objects.create(
            code='mandatory-terms-test', name='Mandatory terms', category='notice',
            clause_text='Mandatory approved term.', is_mandatory=True,
            approval_status=DocumentClause.APPROVED, approved_by=self.staff,
            approved_at=timezone.now(), is_active=True,
        )
        template = DocumentTemplate.objects.create(
            document_type=self.document_type, name='Clause selection', code='clause-selection',
        )
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='Agreement body.',
        )
        optional_assignment = DocumentTemplateClause.objects.create(
            template_version=version, clause=clause, display_order=2,
        )
        DocumentTemplateClause.objects.create(
            template_version=version, clause=mandatory, display_order=1,
        )
        version.status = DocumentTemplateVersion.APPROVED
        version.approved_by = self.staff
        version.approved_at = timezone.now()
        version.save()
        profile = CompanyDocumentProfile.current()

        without_optional = resolve_template(
            version, {}, 'D-1', timezone.localdate(), None, None, profile,
            optional_clause_ids=[],
        )
        self.assertIn('Mandatory approved term.', without_optional['content'])
        self.assertNotIn('Optional approved term.', without_optional['content'])

        with_optional = resolve_template(
            version, {}, 'D-1', timezone.localdate(), None, None, profile,
            optional_clause_ids=[optional_assignment.pk],
        )
        self.assertIn('Optional approved term.', with_optional['content'])
        self.assertEqual(with_optional['clauses'][0]['version'], 1)

        next_clause = create_clause_version(clause, self.staff)
        next_clause.clause_text = 'Revised optional term.'
        next_clause.save()
        self.assertEqual(clause.clause_text, 'Optional approved term.')
        self.assertEqual(next_clause.version, 2)

