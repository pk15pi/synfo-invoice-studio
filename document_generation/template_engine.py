import re
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils.html import escape

from .models import CompanyDocumentProfile


PLACEHOLDER = re.compile(r'\{\{\s*([a-z][a-z0-9_]*)\s*\}\}')
CONDITIONAL = re.compile(r'\[\[if\s+([a-z][a-z0-9_]*)\]\](.*?)\[\[endif\]\]', re.DOTALL)
BUILTIN_KEYS = {
    'company_legal_name', 'company_brand_name', 'company_display_name', 'company_address',
    'company_email', 'company_phone', 'company_website', 'company_gstin', 'company_jurisdiction',
    'document_number', 'document_date', 'effective_date', 'authorised_signatory',
    'authorised_signatory_designation',
}


def _format_value(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, ',.2f')
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if value is None:
        return ''
    return str(value)


def company_snapshot(profile):
    address = profile.business_address or profile.registered_office_address
    legal_name = profile.legal_entity_name.strip()
    brand = profile.brand_name.strip()
    display_name = legal_name
    if brand:
        display_name = f'{legal_name}, operating under the brand name {brand}'
    return {
        'legal_name': legal_name,
        'brand_name': brand,
        'display_name': display_name,
        'address': address,
        'email': profile.official_email,
        'phone': profile.contact_number,
        'website': profile.website,
        'gstin': profile.gstin,
        'jurisdiction': profile.jurisdiction,
        'logo_path': profile.logo.name if profile.logo else '',
        'document_footer': profile.document_footer,
    }


def _conditional_sections(template_text, context):
    def replace(match):
        key, content = match.groups()
        if key not in context:
            raise ValidationError(f'Template references unknown conditional field "{key}".')
        return content if context[key] not in ('', None, False, [], {}) else ''

    text = CONDITIONAL.sub(replace, template_text)
    if '[[if ' in text or '[[endif]]' in text:
        raise ValidationError('Template contains an invalid conditional section.')
    return text


def _render_placeholders(template_text, context, allowed, required_fields):
    template_text = _conditional_sections(template_text, context)
    placeholders = PLACEHOLDER.findall(template_text)
    remaining = PLACEHOLDER.sub('', template_text)
    if '{{' in remaining or '}}' in remaining:
        raise ValidationError('Template contains an invalid placeholder. Use {{ field_key }} syntax only.')
    invalid = sorted(set(placeholders) - allowed)
    if invalid:
        raise ValidationError(
            'Template contains unconfigured placeholders: ' + ', '.join(invalid)
        )
    missing = sorted(
        key for key in set(placeholders) & required_fields if context.get(key) in ('', None)
    )
    if missing:
        raise ValidationError('Provide values for template placeholders: ' + ', '.join(missing))
    return PLACEHOLDER.sub(
        lambda match: _format_value(context.get(match.group(1), '')),
        template_text,
    )


def resolve_template(
    template_version, values, document_number, document_date, effective_date, signatory, profile,
    allow_review_clauses=False,
):
    company = company_snapshot(profile)
    context = {
        **values,
        'company_legal_name': company['legal_name'],
        'company_brand_name': company['brand_name'],
        'company_display_name': company['display_name'],
        'company_address': company['address'],
        'company_email': company['email'],
        'company_phone': company['phone'],
        'company_website': company['website'],
        'company_gstin': company['gstin'],
        'company_jurisdiction': company['jurisdiction'],
        'document_number': document_number,
        'document_date': _format_value(document_date),
        'effective_date': _format_value(effective_date),
        'authorised_signatory': signatory.name if signatory else '',
        'authorised_signatory_designation': signatory.designation if signatory else '',
    }
    definitions = template_version.template.document_type.field_definitions.filter(is_active=True)
    configured_fields = set(definitions.values_list('field_key', flat=True))
    allowed = BUILTIN_KEYS | configured_fields
    required_keys = set(definitions.filter(is_required=True).values_list('field_key', flat=True))
    for assignment in template_version.template_clauses.select_related('clause').order_by(
        'display_order', 'pk',
    ):
        clause = assignment.clause
        if not clause.is_active:
            raise ValidationError(f'Clause "{clause.name}" is inactive.')
        if clause.approval_status != clause.APPROVED and not allow_review_clauses:
            raise ValidationError(f'Clause "{clause.name}" is not approved and active.')
        if (clause.applicable_document_types.exists()
                and not clause.applicable_document_types.filter(
                    pk=template_version.template.document_type_id,
                ).exists()):
            raise ValidationError(f'Clause "{clause.name}" is not applicable to this document type.')
    body = template_version.body_template.strip()
    if not body:
        raise ValidationError('The approved template has no document content.')
    clause_text = '\n\n'.join(
        f'## {assignment.clause.name}\n{assignment.text_override or assignment.clause.clause_text}'
        for assignment in template_version.template_clauses.select_related('clause').order_by(
            'display_order', 'pk',
        )
    )
    if clause_text:
        body = f'{body}\n\n{clause_text}'
    rendered = _render_placeholders(body, context, allowed, required_keys).strip()
    if not rendered:
        raise ValidationError('The approved template has no document content.')
    header = _render_placeholders(
        template_version.header_text, context, allowed, required_keys,
    ).strip()
    footer = _render_placeholders(
        template_version.footer_text, context, allowed, required_keys,
    ).strip()
    title = template_version.template.name
    full_content = f'# {title}\n\n{rendered}'
    return {
        'title': title,
        'content': full_content,
        'header': header,
        'footer': footer,
        'context': {key: _format_value(value) for key, value in context.items()},
        'company': company,
        'signature_configuration': template_version.signature_configuration,
        'signatory': {
            'name': signatory.name,
            'designation': signatory.designation,
        } if signatory else {},
    }


def html_document_content(text):
    return escape(text)
