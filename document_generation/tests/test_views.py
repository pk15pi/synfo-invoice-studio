from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from document_generation.models import (
    CompanyDocumentProfile,
    DocumentClause,
    DocumentCategory,
    DocumentTemplate,
    DocumentTemplateClause,
    DocumentTemplateVersion,
    DocumentType,
)


class DocumentGenerationViewTests(TestCase):
    def test_all_document_generation_models_are_registered_in_admin(self):
        models = apps.get_app_config('document_generation').get_models()
        unregistered = [model.__name__ for model in models if not admin.site.is_registered(model)]
        self.assertEqual(unregistered, [])

    def test_dashboard_and_catalogue_render(self):
        response = self.client.get(reverse('document_generation:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Document &amp; Agreement Management')
        self.assertContains(response, 'Client')
        self.assertContains(response, 'Templates pending review')
        self.assertContains(response, 'Manage clause library')

        catalogue = self.client.get(reverse('document_generation:catalogue'), {'q': 'Master Client Agreement'})
        self.assertEqual(catalogue.status_code, 200)
        self.assertContains(catalogue, 'Master Client Agreement')
        self.assertContains(catalogue, 'QB-MCA')

    def test_catalogue_search_category_filter_and_pagination(self):
        response = self.client.get(reverse('document_generation:catalogue'), {'q': 'offer'})
        self.assertContains(response, 'Offer Letter')
        self.assertNotContains(response, 'Master Client Agreement')

        response = self.client.get(reverse('document_generation:catalogue'), {'category': 'candidate'})
        self.assertContains(response, 'Offer Letter')
        self.assertNotContains(response, 'Asset Management Policy')

    def test_profile_settings_can_save_configured_company_details(self):
        response = self.client.post(reverse('document_generation:settings'), {
            'legal_entity_name': 'Quantbud LLP',
            'brand_name': 'Synfowebs Technology',
            'llpin': '',
            'registered_office_address': '',
            'business_address': '',
            'gstin': '',
            'pan': '',
            'official_email': '',
            'contact_number': '',
            'website': '',
            'partner_details': '[]',
            'document_footer': '',
            'jurisdiction': '',
            'default_language': 'English',
        })
        self.assertRedirects(response, reverse('document_generation:settings'))
        self.assertEqual(CompanyDocumentProfile.objects.get().legal_entity_name, 'Quantbud LLP')

    def test_remote_request_is_rejected(self):
        response = self.client.get(
            reverse('document_generation:dashboard'), REMOTE_ADDR='10.1.1.2',
        )
        self.assertEqual(response.status_code, 403)

    def test_phase_later_links_are_explicit_placeholders(self):
        response = self.client.get(
            reverse('document_generation:placeholder', args=['client-documents']),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not included in the current document-generation release')
        self.assertContains(response, 'not a legally certified electronic signature')
        self.assertEqual(DocumentCategory.objects.count(), 9)
        self.assertGreater(DocumentType.objects.count(), 100)

    def test_generation_requires_a_current_approved_template(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        response = self.client.get(
            reverse('document_generation:generate'),
            {'category': document_type.categories.first().pk, 'type': document_type.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no active, approved template yet')
        self.assertContains(response, '/admin/document_generation/documenttemplate/')

    def test_category_filters_all_active_document_types(self):
        offer_type = DocumentType.objects.get(code='offer-letter')
        category = offer_type.categories.get(code='candidate')
        response = self.client.get(
            reverse('document_generation:generate'), {'category': category.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{offer_type.pk}"')
        self.assertContains(response, 'template approval required')
        self.assertNotContains(response, 'Choose a category first')
        self.assertNotContains(response, 'Asset Management Policy')

    def test_approved_document_type_loads_its_fields(self):
        user = get_user_model().objects.create_user(
            username='template-approver', password='test-password',
        )
        document_type = DocumentType.objects.get(code='offer-letter')
        template = DocumentTemplate.objects.create(document_type=document_type, name='Approved offer')
        version = DocumentTemplateVersion.objects.create(
            template=template,
            version_number=1,
            status=DocumentTemplateVersion.APPROVED,
            approved_by=user,
            approved_at=timezone.now(),
            body_template='{{ candidate_name }}',
        )
        template.active_version = version
        template.save(update_fields=('active_version', 'updated_at'))

        response = self.client.get(
            reverse('document_generation:generate'),
            {
                'category': document_type.categories.get(code='candidate').pk,
                'type': document_type.pk,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Step 3: Complete document details')
        self.assertContains(response, 'id="id_candidate_name"')
        self.assertNotContains(response, 'no active, approved template yet')

    def test_admin_catalogue_profile_and_template_version_pages_render(self):
        user = get_user_model().objects.create_superuser(
            username='document-admin', email='admin@example.invalid', password='test-password',
        )
        self.client.force_login(user)
        profile = CompanyDocumentProfile.current()
        document_type = DocumentType.objects.get(code='offer-letter')
        template = DocumentTemplate.objects.create(document_type=document_type, name='Offer template')
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='{{ candidate_name }}',
        )

        pages = (
            reverse('admin:document_generation_documentcategory_changelist'),
            reverse('admin:document_generation_documenttype_changelist'),
            reverse('admin:document_generation_companydocumentprofile_change', args=[profile.pk]),
            reverse('admin:document_generation_documenttemplateversion_change', args=[version.pk]),
        )
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_admin_can_approve_a_template_version_and_activate_it(self):
        user = get_user_model().objects.create_superuser(
            username='template-reviewer', email='reviewer@example.invalid', password='test-password',
        )
        self.client.force_login(user)
        document_type = DocumentType.objects.get(code='offer-letter')
        template = DocumentTemplate.objects.create(document_type=document_type, name='Reviewed offer')
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='{{ candidate_name }}',
        )
        url = reverse('admin:document_generation_documenttemplateversion_change', args=[version.pk])
        response = self.client.post(url, {
            'template': str(template.pk),
            'version_number': '1',
            'status': DocumentTemplateVersion.APPROVED,
            'header_text': '',
            'body_template': '{{ candidate_name }}',
            'footer_text': '',
            'signature_configuration': '{}',
            'created_by': '',
            'template_clauses-TOTAL_FORMS': '0',
            'template_clauses-INITIAL_FORMS': '0',
            'template_clauses-MIN_NUM_FORMS': '0',
            'template_clauses-MAX_NUM_FORMS': '1000',
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        version.refresh_from_db()
        template.refresh_from_db()
        self.assertEqual(version.status, DocumentTemplateVersion.APPROVED)
        self.assertEqual(version.approved_by, user)
        self.assertIsNotNone(version.approved_at)
        self.assertEqual(template.active_version, version)

    def test_admin_template_preview_is_available_for_unapproved_versions(self):
        user = get_user_model().objects.create_superuser(
            username='template-preview-admin', email='preview@example.invalid',
            password='test-password',
        )
        self.client.force_login(user)
        document_type = DocumentType.objects.get(code='offer-letter')
        version = document_type.templates.get(
            name='Offer Letter',
        ).versions.get(version_number=1)

        response = self.client.get(
            reverse('document_generation:template_preview', args=(version.pk,)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Administrative sample only')
        self.assertContains(response, 'not approved and cannot be used')
        self.assertContains(response, '[Candidate name]')

    def test_template_preview_requires_admin_access(self):
        response = self.client.get(
            reverse('document_generation:template_preview', args=(1,)),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response['Location'])

    def test_admin_cannot_approve_template_with_unapproved_mapped_clauses(self):
        user = get_user_model().objects.create_superuser(
            username='template-clause-reviewer', email='clause@example.invalid',
            password='test-password',
        )
        self.client.force_login(user)
        document_type = DocumentType.objects.get(code='offer-letter')
        template = DocumentTemplate.objects.create(document_type=document_type, name='Offer with clause')
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, body_template='{{ candidate_name }}',
        )
        clause = DocumentClause.objects.create(
            code='offer-review-test', name='Review test clause',
            clause_text='Review required.', approval_status=DocumentClause.REVIEW,
        )
        DocumentTemplateClause.objects.create(
            template_version=version, clause=clause, is_mandatory=True,
        )
        response = self.client.post(
            reverse('admin:document_generation_documenttemplateversion_change', args=(version.pk,)),
            {
                'template': str(template.pk),
                'version_number': '1',
                'status': DocumentTemplateVersion.APPROVED,
                'header_text': '',
                'body_template': '{{ candidate_name }}',
                'footer_text': '',
                'output_formats': '["docx", "pdf"]',
                'signature_configuration': '{}',
                'created_by': '',
                'template_clauses-TOTAL_FORMS': '1',
                'template_clauses-INITIAL_FORMS': '1',
                'template_clauses-MIN_NUM_FORMS': '0',
                'template_clauses-MAX_NUM_FORMS': '1000',
                'template_clauses-0-id': str(
                    DocumentTemplateClause.objects.get(
                        template_version=version, clause=clause,
                    ).pk
                ),
                'template_clauses-0-template_version': str(version.pk),
                'template_clauses-0-clause': str(clause.pk),
                'template_clauses-0-display_order': '0',
                'template_clauses-0-is_mandatory': 'on',
                'template_clauses-0-text_override': '',
                '_save': 'Save',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approve and activate every mapped clause')
        version.refresh_from_db()
        self.assertEqual(version.status, DocumentTemplateVersion.DRAFT)
