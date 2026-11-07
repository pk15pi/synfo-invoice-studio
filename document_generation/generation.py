import hashlib
import mimetypes
import uuid
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from document_management.models import Document
from document_management.services import private_storage

from .models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentActivityLog,
    DocumentParty,
    DocumentTemplateVersion,
    DocumentType,
    GeneratedDocument,
)
from .renderers import render_pdf, render_word
from .services import allocate_document_number
from .template_engine import company_snapshot, resolve_template

PREVIEW_SALT = 'document_generation.preview'
PREVIEW_MAX_AGE = 60 * 60


def available_output_formats(template_version):
    formats = template_version.output_formats
    if not formats:
        formats = template_version.template.document_type.default_output_formats or ['docx', 'pdf']
    if not isinstance(formats, list):
        raise ValidationError('The selected template has invalid output format configuration.')
    available = [format_name for format_name in ('docx', 'pdf') if format_name in formats]
    if not available:
        raise ValidationError('The selected template has no available output format.')
    return tuple(available + (['both'] if len(available) == 2 else []))


def _signature_parties(configuration, company, counterparty, signatory):
    roles = configuration.get('parties')
    if roles is None:
        roles = ['issuer']
        if configuration.get('include_counterparty', True):
            roles.append('counterparty')
        if configuration.get('include_witness'):
            roles.append('witness')
    parties = []
    for role in roles:
        if role == 'issuer':
            parties.append({
                'label': f"For {company.get('display_name') or company.get('legal_name', '')}",
                'name': signatory.get('name', ''),
                'designation': signatory.get('designation', ''),
            })
        elif role == 'counterparty':
            parties.append({
                'label': f'For {counterparty or "Second Party"}',
                'name': '',
                'designation': '',
            })
        else:
            parties.append({'label': 'Witness', 'name': '', 'designation': ''})
    return parties


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _party_role(category_code, module):
    if module and module in dict(DocumentParty.ROLES):
        return module
    if category_code in dict(DocumentParty.ROLES):
        return category_code
    return 'other'


def _party_name(category_code, module, values, record_name=''):
    if record_name:
        return record_name[:180]
    keys = {
        'employee': ('employee_name', 'name'),
        'client': ('client_name', 'name'),
        'vendor': ('vendor_name', 'name'),
        'asset': ('asset_name', 'name'),
        'candidate': ('candidate_name', 'name'),
        'freelancer': ('freelancer_name', 'consultant_name', 'name'),
    }.get(module or category_code, ('candidate_name', 'employee_name', 'client_name',
                                     'freelancer_name', 'vendor_name', 'name'))
    for key in keys:
        if values.get(key):
            return str(values[key])[:180]
    return 'Manually entered party'


def prepare_preview(form):
    data = form.cleaned_data
    document_type = data['document_type']
    template_version = data['template_version']
    signatory = data.get('authorised_signatory')
    previous_id = data.get('revision_of')
    previous = None
    version_number = 1
    if previous_id:
        try:
            previous = GeneratedDocument.objects.get(pk=previous_id)
        except GeneratedDocument.DoesNotExist as error:
            raise ValidationError('The document being revised no longer exists.') from error
        if previous.status != 'draft':
            raise ValidationError('Only a draft document can be revised.')
        if previous.document_type_id != document_type.pk:
            raise ValidationError('A revised document must keep its document type.')
        if previous.superseding_documents.exists():
            raise ValidationError('Revise the latest draft version instead of an older version.')
        version_number = previous.version_number + 1

    profile = CompanyDocumentProfile.current()
    values = {key: _json_value(value) for key, value in form.field_values().items()}
    optional_clause_ids = data.get('optional_clauses') if 'optional_clauses' in form.fields else None
    party_records = []
    for module, record in form.selected_records.items():
        snapshot = form.party_snapshot()['records'][module]
        party_records.append({
            'module': module,
            'object_id': str(record.pk),
            'record_name': str(record),
            'snapshot': _json_value(snapshot),
            'role': _party_role(data['category'].code, module),
        })
    primary_module = form.selected_record_module or (
        party_records[0]['module'] if party_records else ''
    )
    party_snapshot = _json_value(form.party_snapshot())
    if not party_records:
        party_records.append({
            'module': '',
            'object_id': '',
            'record_name': '',
            'snapshot': party_snapshot.get('manual_fields', {}),
            'role': _party_role(data['category'].code, ''),
        })
    party = {
        'records': party_records,
        'module': primary_module,
        'object_id': next(
            (row['object_id'] for row in party_records if row['module'] == primary_module), '',
        ),
        'snapshot': party_snapshot,
        'role': _party_role(data['category'].code, primary_module),
    }
    party_name = _party_name(
        data['category'].code,
        party.get('module'),
        values,
        next(
            (record.get('record_name', '') for record in party_records
             if record.get('module') == party.get('module')),
            '',
        ),
    )
    signature_configuration = template_version.signature_configuration or {}
    signatory_snapshot = {
        'name': signatory.name if signatory else '',
        'designation': signatory.designation if signatory else '',
    }
    signatures = _signature_parties(
        signature_configuration, company_snapshot(profile), party_name, signatory_snapshot,
    )

    # Validate template placeholders before consuming a sequence number.
    resolve_template(
        template_version, values, 'DOCUMENT-NUMBER', data['document_date'],
        data.get('effective_date'), signatory, profile,
        optional_clause_ids=optional_clause_ids,
    )
    document_number = allocate_document_number(document_type, data['document_date'])
    resolved = resolve_template(
        template_version, values, document_number, data['document_date'],
        data.get('effective_date'), signatory, profile,
        optional_clause_ids=optional_clause_ids,
    )
    return {
        'document_type_id': document_type.pk,
        'category_id': data['category'].pk,
        'category_code': data['category'].code,
        'template_version_id': template_version.pk,
        'version_number': version_number,
        'previous_document_id': previous.pk if previous else None,
        'document_number': document_number,
        'document_date': data['document_date'].isoformat(),
        'effective_date': data.get('effective_date').isoformat() if data.get('effective_date') else '',
        'signatory_id': signatory.pk if signatory else None,
        'output_formats': list(available_output_formats(template_version)),
        'signature_parties': signatures,
        'field_values': values,
        'party': party,
        'rendered_content': resolved['content'],
        'title': resolved['title'],
        'header_text': resolved['header'],
        'footer_text': resolved['footer'],
        'signature_configuration': resolved['signature_configuration'],
        'clause_snapshot': resolved['clauses'],
        'selected_clause_ids': [int(pk) for pk in optional_clause_ids] if optional_clause_ids is not None else None,
        'context_snapshot': resolved['context'],
        'company_snapshot': resolved['company'],
        'signatory_snapshot': resolved['signatory'],
        'signature_parties': signatures,
    }


def sign_preview(payload):
    return signing.dumps(payload, salt=PREVIEW_SALT, compress=True)


def load_preview(token):
    try:
        return signing.loads(token, salt=PREVIEW_SALT, max_age=PREVIEW_MAX_AGE)
    except signing.SignatureExpired as error:
        raise ValidationError('The preview expired. Please review and preview the document again.') from error
    except signing.BadSignature as error:
        raise ValidationError('The preview could not be verified. Please preview the document again.') from error


def _render_spec(payload):
    company = payload['company_snapshot']
    logo = None
    if company.get('logo_path'):
        try:
            with private_storage.open(company['logo_path'], 'rb') as logo_file:
                logo = logo_file.read()
        except OSError as error:
            raise ValidationError('The configured company logo is missing from private storage.') from error
    party = payload['party']
    party_name = _party_name(
        payload['category_code'],
        party.get('module'),
        payload['field_values'],
        next(
            (record.get('record_name', '') for record in party.get('records', [])
             if record.get('module') == party.get('module')),
            '',
        ),
    )
    return {
        'title': payload['title'],
        'content': payload['rendered_content'],
        'header_text': payload.get('header_text', ''),
        'footer_text': payload.get('footer_text', ''),
        'signature_configuration': payload.get('signature_configuration', {}),
        'signature_parties': payload.get('signature_parties', []),
        'company': company,
        'logo': logo,
        'document_number': payload['document_number'],
        'document_date': payload['document_date'],
        'effective_date': payload.get('effective_date', ''),
        'party_name': party_name,
        'signatory_name': payload['signatory_snapshot'].get('name', ''),
        'signatory_designation': payload['signatory_snapshot'].get('designation', ''),
    }


def _save_output(generated, extension, content):
    file_name = f'{generated.document_number}-v{generated.version_number}.{extension}'
    relative_path = private_storage.save(
        f'document_generation/generated/{uuid.uuid4().hex}.{extension}',
        BytesIO(content),
    )
    try:
        doc = Document.objects.create(
            category='agreement',
            title=f'{generated.document_number} - {generated.document_type.name} ({extension.upper()})'[:200],
            content_type=ContentType.objects.get_for_model(generated),
            object_id=str(generated.pk),
            source_type='document_generation_output',
            source_id=uuid.uuid4().hex,
            file_path=relative_path,
            original_filename=file_name,
            mime_type=mimetypes.guess_type(file_name)[0] or 'application/octet-stream',
            sensitivity=Document.CONFIDENTIAL,
            tags=f'document_generation,{extension}',
            uploaded_at=timezone.now(),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
    except Exception:
        private_storage.delete(relative_path)
        raise
    return doc


@transaction.atomic
def generate_document(payload, output_format, user=None):
    if output_format not in {'docx', 'pdf', 'both'}:
        raise ValidationError('Choose Word, PDF, or both output formats.')
    try:
        document_type = DocumentType.objects.get(pk=payload['document_type_id'], is_active=True)
        template_version = DocumentTemplateVersion.objects.select_related(
            'template',
        ).get(
            pk=payload['template_version_id'],
            template__document_type=document_type,
            template__is_active=True,
            status=DocumentTemplateVersion.APPROVED,
        )
    except (DocumentType.DoesNotExist, DocumentTemplateVersion.DoesNotExist, KeyError) as error:
        raise ValidationError('The selected document type or approved template is no longer available.') from error
    if template_version.template.active_version_id != template_version.pk:
        raise ValidationError('Only the active approved template version can be generated.')
    if output_format not in available_output_formats(template_version):
        raise ValidationError('The selected output format is not enabled for this template.')

    previous = None
    if payload.get('previous_document_id'):
        try:
            previous = GeneratedDocument.objects.get(pk=payload['previous_document_id'])
        except GeneratedDocument.DoesNotExist as error:
            raise ValidationError('The document being revised no longer exists.') from error
        if previous.status != 'draft' or previous.document_type_id != document_type.pk:
            raise ValidationError('Only a draft of this document type can be revised.')
        if previous.superseding_documents.exists():
            raise ValidationError('Revise the latest draft version instead of an older version.')
        if payload['version_number'] != previous.version_number + 1:
            raise ValidationError('The draft version changed. Please create a new preview.')

    spec = _render_spec(payload)
    outputs = {}
    if output_format in {'docx', 'both'}:
        output = BytesIO()
        render_word(spec, output)
        outputs['docx'] = output.getvalue()
    if output_format in {'pdf', 'both'}:
        output = BytesIO()
        render_pdf(spec, output)
        outputs['pdf'] = output.getvalue()

    saved_paths = []
    try:
        generated = GeneratedDocument.objects.create(
            document_number=payload['document_number'],
            document_type=document_type,
            template_version=template_version,
            version_number=payload['version_number'],
            status='draft',
            rendered_content=payload['rendered_content'],
            content_snapshot={
                'field_values': payload['field_values'],
                'party': payload['party']['snapshot'],
                'company': payload['company_snapshot'],
                'context': payload['context_snapshot'],
                'header_text': payload.get('header_text', ''),
                'footer_text': payload.get('footer_text', ''),
                'signature_configuration': payload.get('signature_configuration', {}),
                'template_snapshot': {
                    'template_code': template_version.template.code,
                    'template_name': template_version.template.name,
                    'version_number': template_version.version_number,
                    'change_description': template_version.change_description,
                },
                'clauses': payload.get('clause_snapshot', []),
                'requested_output_format': output_format,
            },
            authorised_signatory_id=payload.get('signatory_id'),
            signatory_snapshot=payload['signatory_snapshot'],
            created_by=user if getattr(user, 'is_authenticated', False) else None,
            issue_date=payload['document_date'],
            effective_date=payload.get('effective_date') or None,
            supersedes=previous,
        )
        party_data = payload['party']
        category_code = payload['category_code']
        from .generation_forms import ENTITY_MODELS
        references = {}
        for record_data in party_data.get('records', []):
            linked_record = None
            module = record_data.get('module')
            object_id = record_data.get('object_id')
            if module and object_id:
                model_data = ENTITY_MODELS.get(module)
                if model_data:
                    model, _ = model_data
                    try:
                        linked_record = model.objects.get(pk=object_id)
                    except (model.DoesNotExist, ValueError):
                        raise ValidationError('A selected related record no longer exists.')
            party_name = _party_name(
                category_code,
                module,
                payload['field_values'],
                record_data.get('record_name', ''),
            )
            party = DocumentParty(
                generated_document=generated,
                role=record_data['role'],
                display_name=party_name,
                contact_snapshot=record_data.get('snapshot', {}),
            )
            if linked_record:
                party.content_type = ContentType.objects.get_for_model(linked_record)
                party.object_id = str(linked_record.pk)
            party.save()
        for extension, content in outputs.items():
            reference = _save_output(generated, extension, content)
            references[extension] = reference
            saved_paths.append(reference.file_path)
        generated.word_file_document = references.get('docx')
        generated.pdf_file_document = references.get('pdf')
        generated.file_document = references.get('pdf') or references.get('docx')
        generated.save(update_fields=(
            'word_file_document', 'pdf_file_document', 'file_document', 'updated_at',
        ))
        DocumentActivityLog.objects.create(
            generated_document=generated,
            action='generated',
            actor=user if getattr(user, 'is_authenticated', False) else None,
            details={
                'format': output_format,
                'template_version': template_version.version_number,
                'version_number': generated.version_number,
                'word_document': generated.word_file_document_id,
                'pdf_document': generated.pdf_file_document_id,
            },
        )
    except Exception:
        for path in saved_paths:
            private_storage.delete(path)
        raise
    return generated
