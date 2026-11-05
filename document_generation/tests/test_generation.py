import tempfile
from datetime import date
from io import BytesIO

from docx import Document as WordDocumentReader
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from asset_management.models import Asset, AssetCategory
from document_management.models import Document
from document_management.services import private_storage
from employee_management.models import Department, Employee
from invoice_app.models import Client

from document_generation.generation import generate_document, prepare_preview
from document_generation.generation_forms import UniversalDocumentForm
from document_generation.models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentFieldDefinition,
    DocumentTemplate,
    DocumentTemplateVersion,
    DocumentType,
    GeneratedDocument,
)


GENERATION_TYPES = (
    'non-disclosure-agreement-nda',
    'master-client-agreement-mca',
    'freelancer-agreement',
    'offer-letter',
    'joining-letter',
    'experience-certificate',
    'relieving-letter',
    'asset-handover-receipt',
)


class UniversalDocumentGenerationTests(TestCase):
    def setUp(self):
        self.private_root = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(EMPLOYEE_PRIVATE_ROOT=self.private_root.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.private_root.cleanup)

        self.user = get_user_model().objects.create_user(username='generator', password='test')
        self.profile = CompanyDocumentProfile.current()
        self.signatory = CompanyDocumentSignatory.objects.create(
            profile=self.profile, name='A. Representative', designation='Partner',
            is_authorized=True, is_active=True,
        )
        self.department = Department.objects.create(name='Engineering', code='ENG')
        self.employee = Employee.objects.create(
            employee_id='QB-E-100', first_name='Asha', last_name='Rao',
            department=self.department, designation='Engineer',
            date_of_joining=date(2022, 5, 2), current_address='4 Sample Road',
            city='Pune', state='Maharashtra', pin_code='411001',
            work_email='asha@example.invalid', employment_type='full_time',
        )
        self.client_record = Client.objects.create(
            name='Northwind Labs', address='10 Market Street', contact_person='N. Contact',
            email='contact@example.invalid', phone='9876543210', gstin='',
        )
        self.asset_category = AssetCategory.objects.create(name='Laptop', code='LAP')
        self.asset = Asset.objects.create(
            asset_code='QB-LAP-001', name='Company laptop', category=self.asset_category,
            current_employee=self.employee,
        )

    def tearDown(self):
        for document in Document.objects.filter(source_type='document_generation_output'):
            private_storage.delete(document.file_path)

    def _create_approved_template(self, document_type, body_template=None, name='Test template'):
        definitions = list(document_type.field_definitions.filter(is_active=True))
        if body_template is None:
            placeholders = ' '.join(f'{{{{ {definition.field_key} }}}}' for definition in definitions)
            body_template = (
                '## Particulars\n{{ company_display_name }}\n'
                '{{ document_number }}\n{{ document_date }}\n' + placeholders
            )
        template = DocumentTemplate.objects.create(document_type=document_type, name=name)
        version = DocumentTemplateVersion.objects.create(
            template=template, version_number=1, status=DocumentTemplateVersion.APPROVED,
            approved_by=self.user, approved_at=timezone.now(), body_template=body_template,
        )
        template.active_version = version
        template.save(update_fields=('active_version', 'updated_at'))
        return version

    def _raw_value(self, definition):
        if definition.value_type == 'date':
            return '2026-11-04'
        if definition.value_type in {'decimal', 'currency'}:
            return '25000.00'
        if definition.value_type == 'integer':
            return '30'
        if definition.value_type == 'boolean':
            return 'on'
        if definition.value_type == 'email':
            return 'party@example.invalid'
        if definition.value_type == 'select':
            return definition.choices[0] if definition.choices else ''
        return f'Test {definition.label}'

    def _form_data(self, document_type, version, category=None, revision_of=None):
        category = category or document_type.categories.filter(is_active=True).first()
        data = {
            'category': str(category.pk),
            'document_type': str(document_type.pk),
            'template_version': str(version.pk),
            'document_date': '2026-11-04',
            'effective_date': '2026-11-05',
            'authorised_signatory': str(self.signatory.pk),
            'revision_of': str(revision_of or ''),
            'related_record': '',
            'related_asset': '',
        }
        for definition in document_type.field_definitions.filter(is_active=True):
            data[definition.field_key] = self._raw_value(definition)
        if document_type.code in {
            'joining-letter', 'experience-certificate', 'relieving-letter',
            'asset-handover-receipt',
        }:
            data['related_record'] = f'employee:{self.employee.pk}'
        elif document_type.code in {'non-disclosure-agreement-nda', 'master-client-agreement-mca'}:
            data['related_record'] = f'client:{self.client_record.pk}'
        if document_type.code == 'asset-handover-receipt':
            data['related_asset'] = str(self.asset.pk)
        return data

    def _valid_form(self, document_type, version, **kwargs):
        data = self._form_data(document_type, version, **kwargs)
        form = UniversalDocumentForm(
            data, document_type=document_type, related_record=data['related_record'],
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_seeded_document_types_have_configurable_fields(self):
        for code in GENERATION_TYPES:
            with self.subTest(code=code):
                document_type = DocumentType.objects.get(code=code)
                self.assertGreater(document_type.field_definitions.count(), 0)

    def test_related_employee_client_and_asset_fields_are_populated(self):
        employee_type = DocumentType.objects.get(code='experience-certificate')
        employee_form = UniversalDocumentForm(
            document_type=employee_type, related_record=f'employee:{self.employee.pk}',
        )
        self.assertEqual(employee_form['employee_name'].value(), 'Asha Rao')
        self.assertEqual(employee_form['employee_id'].value(), 'QB-E-100')
        self.assertEqual(employee_form['department'].value(), 'Engineering')
        self.assertEqual(employee_form['date_of_joining'].value(), '2022-05-02')

        client_type = DocumentType.objects.get(code='master-client-agreement-mca')
        client_form = UniversalDocumentForm(
            document_type=client_type, related_record=f'client:{self.client_record.pk}',
        )
        self.assertEqual(client_form['client_name'].value(), 'Northwind Labs')
        self.assertIn(client_form['client_gstin'].value(), (None, ''))

        nda_type = DocumentType.objects.get(code='non-disclosure-agreement-nda')
        nda_form = UniversalDocumentForm(document_type=nda_type)
        self.assertIn('Quantbud LLP, operating under the brand name Synfowebs Technology',
                      nda_form['first_party_name'].value())

        asset_type = DocumentType.objects.get(code='asset-handover-receipt')
        asset_form = UniversalDocumentForm(
            document_type=asset_type,
            related_record=f'employee:{self.employee.pk}',
            related_asset=str(self.asset.pk),
        )
        self.assertEqual(asset_form['employee_name'].value(), 'Asha Rao')
        self.assertEqual(asset_form['asset_code'].value(), 'QB-LAP-001')
        self.assertEqual(asset_form['asset_category'].value(), 'Laptop')

    def test_candidate_and_freelancer_documents_fall_back_to_manual_entry(self):
        for code in ('offer-letter', 'freelancer-agreement'):
            with self.subTest(code=code):
                document_type = DocumentType.objects.get(code=code)
                form = UniversalDocumentForm(document_type=document_type)
                self.assertEqual(
                    list(form.fields['related_record'].choices),
                    [('', 'Enter party details manually')],
                )
                self.assertTrue(form.field_definitions)

    def test_conditional_fields_are_required_only_when_visible(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        requires_probation = DocumentFieldDefinition.objects.create(
            document_type=document_type,
            field_key='requires_probation',
            label='Probation applies',
            value_type='boolean',
            display_order=100,
        )
        probation_period = document_type.field_definitions.get(field_key='probation_period')
        probation_period.is_required = True
        probation_period.visibility_condition = {
            'field': requires_probation.field_key,
            'operator': 'equals',
            'value': True,
        }
        probation_period.save()
        version = self._create_approved_template(document_type)

        data = self._form_data(document_type, version)
        data['requires_probation'] = 'on'
        data.pop('probation_period')
        form = UniversalDocumentForm(data, document_type=document_type)
        self.assertFalse(form.is_valid())
        self.assertIn('probation_period', form.errors)

        data['requires_probation'] = ''
        form = UniversalDocumentForm(data, document_type=document_type)
        self.assertTrue(form.is_valid(), form.errors)

    def test_all_eight_document_types_preview_and_generate_both_formats(self):
        for code in GENERATION_TYPES:
            with self.subTest(code=code):
                document_type = DocumentType.objects.get(code=code)
                version = self._create_approved_template(document_type)
                form = self._valid_form(document_type, version)
                payload = prepare_preview(form)
                generated = generate_document(payload, 'both', self.user)

                self.assertEqual(generated.status, 'draft')
                self.assertEqual(generated.template_version, version)
                self.assertIn('Quantbud LLP, operating under the brand name Synfowebs Technology',
                              generated.rendered_content)
                self.assertIsNotNone(generated.word_file_document)
                self.assertIsNotNone(generated.pdf_file_document)
                self.assertEqual(generated.parties.count(), 2 if code == 'asset-handover-receipt' else 1)
                for reference, signature in (
                    (generated.word_file_document, b'PK'),
                    (generated.pdf_file_document, b'%PDF'),
                ):
                    self.assertTrue(private_storage.exists(reference.file_path))
                    with private_storage.open(reference.file_path, 'rb') as output:
                        self.assertTrue(output.read(4).startswith(signature))
                    self.assertEqual(
                        Document.objects.get(pk=reference.pk).content_type.model,
                        'generateddocument',
                    )

    def test_word_and_pdf_outputs_are_individually_supported(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        version = self._create_approved_template(document_type)
        form = self._valid_form(document_type, version)
        word = generate_document(prepare_preview(form), 'docx', self.user)
        self.assertIsNotNone(word.word_file_document)
        self.assertIsNone(word.pdf_file_document)
        with private_storage.open(word.word_file_document.file_path, 'rb') as stream:
            parsed = WordDocumentReader(BytesIO(stream.read()))
        self.assertIn('Test Candidate name', '\n'.join(p.text for p in parsed.paragraphs))

        form = self._valid_form(document_type, version)
        pdf = generate_document(prepare_preview(form), 'pdf', self.user)
        self.assertIsNone(pdf.word_file_document)
        self.assertIsNotNone(pdf.pdf_file_document)
        with private_storage.open(pdf.pdf_file_document.file_path, 'rb') as stream:
            self.assertTrue(stream.read().startswith(b'%PDF'))

    def test_required_field_validation_and_unknown_placeholders(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        version = self._create_approved_template(document_type)
        data = self._form_data(document_type, version)
        data.pop('candidate_name')
        form = UniversalDocumentForm(data, document_type=document_type)
        self.assertFalse(form.is_valid())
        self.assertIn('candidate_name', form.errors)

        invalid_version = self._create_approved_template(
            document_type, '{{ not_configured }}', name='Unknown placeholder template',
        )
        valid_form = self._valid_form(document_type, invalid_version)
        with self.assertRaisesMessage(ValidationError, 'unconfigured placeholders'):
            prepare_preview(valid_form)

    def test_template_engine_rejects_expressions_and_supports_conditionals(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        version = self._create_approved_template(
            document_type,
            'Hello {{ candidate_name }}\n[[if monthly_salary]]Monthly: {{ monthly_salary }}[[endif]]',
        )
        form = self._valid_form(document_type, version)
        payload = prepare_preview(form)
        self.assertIn('Monthly:', payload['rendered_content'])

        invalid_version = self._create_approved_template(
            document_type, '{{ python.__import__ }}', name='Unsafe placeholder template',
        )
        with self.assertRaisesMessage(ValidationError, 'invalid placeholder'):
            prepare_preview(self._valid_form(document_type, invalid_version))

    def test_revision_creates_new_immutable_document_version(self):
        document_type = DocumentType.objects.get(code='joining-letter')
        version = self._create_approved_template(document_type)
        original = generate_document(
            prepare_preview(self._valid_form(document_type, version)), 'pdf', self.user,
        )
        revised = generate_document(
            prepare_preview(self._valid_form(document_type, version, revision_of=original.pk)),
            'pdf',
            self.user,
        )
        original.refresh_from_db()
        self.assertEqual(revised.version_number, 2)
        self.assertEqual(revised.supersedes, original)
        self.assertNotEqual(revised.document_number, original.document_number)
        self.assertEqual(original.version_number, 1)

    def test_preview_and_download_views_use_private_document_storage(self):
        document_type = DocumentType.objects.get(code='offer-letter')
        version = self._create_approved_template(document_type)
        form_data = self._form_data(document_type, version)
        preview = self.client.post(
            '/documents/generate/', {'action': 'preview', **form_data},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, 'Quantbud LLP, operating under the brand name Synfowebs Technology')
        token = preview.context['preview_token']
        generated_response = self.client.post('/documents/generate/', {
            'action': 'generate', 'output_format': 'both', 'preview_token': token,
        })
        self.assertEqual(generated_response.status_code, 200)
        generated = GeneratedDocument.objects.get(document_number=preview.context['payload']['document_number'])
        word = self.client.get(f'/documents/download/{generated.pk}/docx/')
        pdf = self.client.get(f'/documents/download/{generated.pk}/pdf/')
        self.assertEqual(word.status_code, 200)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(word['Cache-Control'], 'no-store, private')
        self.assertTrue(b''.join(word.streaming_content).startswith(b'PK'))
        self.assertTrue(b''.join(pdf.streaming_content).startswith(b'%PDF'))
        self.assertEqual(
            self.client.post('/documents/generate/', {
                'action': 'generate', 'output_format': 'pdf', 'preview_token': token,
            }).status_code,
            404,
        )

    def test_source_data_edits_do_not_mutate_business_records(self):
        document_type = DocumentType.objects.get(code='joining-letter')
        version = self._create_approved_template(document_type)
        data = self._form_data(document_type, version)
        data['employee_name'] = 'Corrected document name'
        form = UniversalDocumentForm(
            data, document_type=document_type, related_record=data['related_record'],
        )
        self.assertTrue(form.is_valid(), form.errors)
        generated = generate_document(prepare_preview(form), 'pdf', self.user)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.full_name, 'Asha Rao')
        self.assertEqual(generated.content_snapshot['field_values']['employee_name'], 'Corrected document name')

    def test_long_content_and_tables_render_without_overflow_errors(self):
        document_type = DocumentType.objects.get(code='master-client-agreement-mca')
        table = '| Item | Description |\n| --- | --- |\n' + '\n'.join(
            f'| {index} | A long description for row {index} |' for index in range(35)
        )
        paragraphs = '\n\n'.join(
            f'## Section {index}\n' + ('Long agreement text with wrapped words. ' * 35)
            for index in range(20)
        )
        version = self._create_approved_template(
            document_type, f'## Long content\n{{{{ client_name }}}}\n{table}\n{paragraphs}',
        )
        generated = generate_document(
            prepare_preview(self._valid_form(document_type, version)), 'both', self.user,
        )
        self.assertGreater(generated.pdf_file_document.size, 5000)
        with private_storage.open(generated.pdf_file_document.file_path, 'rb') as pdf:
            self.assertTrue(pdf.read().startswith(b'%PDF'))
        with private_storage.open(generated.word_file_document.file_path, 'rb') as word:
            parsed = WordDocumentReader(BytesIO(word.read()))
        self.assertGreater(len(parsed.paragraphs), 30)
        self.assertGreaterEqual(len(parsed.tables[0].rows), 35)
