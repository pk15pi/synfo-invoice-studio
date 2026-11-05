from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from document_generation.models import (
    CompanyDocumentProfile,
    DocumentCategory,
    DocumentTemplate,
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
        self.assertEqual(DocumentCategory.objects.count(), 8)
        self.assertGreater(DocumentType.objects.count(), 100)

    def test_generation_requires_a_current_approved_template(self):
        response = self.client.get(
            reverse('document_generation:generate'),
            {'type': DocumentType.objects.get(code='offer-letter').pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no active, approved template yet')

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
            '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        version.refresh_from_db()
        template.refresh_from_db()
        self.assertEqual(version.status, DocumentTemplateVersion.APPROVED)
        self.assertEqual(version.approved_by, user)
        self.assertIsNotNone(version.approved_at)
        self.assertEqual(template.active_version, version)
