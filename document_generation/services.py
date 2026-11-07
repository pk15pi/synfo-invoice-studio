from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    DocumentClause, DocumentNumberSequence, DocumentTemplate,
    DocumentTemplateClause, DocumentTemplateVersion, DocumentType,
)


def _audit_library(request, obj, action, changes=None):
    from audit.models import AuditEvent

    AuditEvent.objects.create(
        app_label='document_generation', model=obj.__class__.__name__,
        object_id=str(obj.pk or ''), object_repr=str(obj)[:300], action=action,
        changes=changes or {}, request_path=getattr(request, 'path', ''), source='ui',
    )


@transaction.atomic
def create_template_version(template, actor=None, change_description=''):
    template = DocumentTemplate.objects.select_for_update().get(pk=template.pk)
    previous = template.versions.order_by('-version_number').first()
    if previous and not change_description.strip():
        raise ValidationError('Describe the change before creating a new template version.')
    if previous and previous.status != DocumentTemplateVersion.APPROVED and previous.status != DocumentTemplateVersion.RETIRED:
        raise ValidationError('Finish review or retire the current draft before creating another version.')
    number = (previous.version_number if previous else 0) + 1
    version = DocumentTemplateVersion.objects.create(
        template=template, version_number=number, status=DocumentTemplateVersion.DRAFT,
        header_text=previous.header_text if previous else '',
        body_template=previous.body_template if previous else '',
        footer_text=previous.footer_text if previous else '',
        effective_date=previous.effective_date if previous else template.effective_date,
        output_formats=list(previous.output_formats) if previous else {
            'docx': ['docx'], 'pdf': ['pdf'], 'both': ['docx', 'pdf'],
        }.get(template.default_output_format, ['docx', 'pdf']),
        signature_configuration=dict(previous.signature_configuration) if previous else {},
        change_description=change_description,
        created_by=actor if getattr(actor, 'is_authenticated', False) else None,
    )
    if previous:
        for assignment in previous.template_clauses.select_related('clause'):
            DocumentTemplateClause.objects.create(
                template_version=version, clause=assignment.clause,
                display_order=assignment.display_order, is_mandatory=assignment.is_mandatory,
                text_override=assignment.text_override,
            )
    template.status = (
        DocumentTemplate.STATUS_ACTIVE if template.active_version_id else DocumentTemplate.STATUS_DRAFT
    )
    template.save(update_fields=('status', 'updated_at'))
    return version


@transaction.atomic
def create_clause_version(clause, actor=None):
    clause = DocumentClause.objects.select_for_update().get(pk=clause.pk)
    if clause.approval_status != DocumentClause.APPROVED and not clause.approved_at:
        raise ValidationError('Edit the existing draft clause until it has been approved.')
    version = DocumentClause.objects.create(
        code=clause.code, name=clause.name, category=clause.category,
        clause_text=clause.clause_text, version=clause.version + 1,
        review_date=clause.review_date, admin_notes=clause.admin_notes,
        is_mandatory=clause.is_mandatory, default_inclusion=clause.default_inclusion,
        approval_status=DocumentClause.DRAFT, effective_date=clause.effective_date,
        is_active=True,
    )
    version.applicable_document_types.set(clause.applicable_document_types.all())
    return version


@transaction.atomic
def transition_template_version(version, action, request=None, actor=None):
    if action in {'approve', 'activate', 'deactivate', 'retire'} and not getattr(actor, 'is_staff', False):
        raise PermissionDenied('Sign in as an authorised document administrator to perform this action.')
    version = DocumentTemplateVersion.objects.select_for_update().select_related('template').get(pk=version.pk)
    template = version.template
    old_status = version.status
    was_active = template.active_version_id == version.pk
    if action == 'submit':
        if version.status != DocumentTemplateVersion.DRAFT:
            raise ValidationError('Only a draft template version can be submitted for review.')
        if not version.body_template.strip():
            raise ValidationError('Add the template body before submitting it for review.')
        version.status = DocumentTemplateVersion.REVIEW
        if not template.active_version_id:
            template.status = DocumentTemplate.STATUS_REVIEW
    elif action == 'approve':
        if version.status != DocumentTemplateVersion.REVIEW:
            raise ValidationError('Only a template version under review can be approved.')
        if not version.body_template.strip():
            raise ValidationError('The template body is required before approval.')
        if version.template_clauses.exclude(
            clause__approval_status=DocumentClause.APPROVED, clause__is_active=True,
        ).exists():
            raise ValidationError('Approve and activate every clause assigned to this template first.')
        version.status = DocumentTemplateVersion.APPROVED
        version.approved_by = actor if getattr(actor, 'is_authenticated', False) else None
        version.approved_at = timezone.now()
        if not template.active_version_id:
            template.status = DocumentTemplate.STATUS_APPROVED
    elif action == 'activate':
        if version.status != DocumentTemplateVersion.APPROVED:
            raise ValidationError('Only an approved template version can be activated.')
        if template.category_id and not template.category.is_active:
            raise ValidationError('Activate the template category before activating this template.')
        if not template.document_type.is_active:
            raise ValidationError('Activate the document type before activating this template.')
        template.active_version = version
        template.is_active = True
        template.status = DocumentTemplate.STATUS_ACTIVE
    elif action == 'retire':
        version.status = DocumentTemplateVersion.RETIRED
        has_other_live_versions = template.versions.exclude(pk=version.pk).filter(
            status__in=(DocumentTemplateVersion.DRAFT, DocumentTemplateVersion.REVIEW,
                        DocumentTemplateVersion.APPROVED),
        ).exists()
        if was_active or (not template.active_version_id and not has_other_live_versions):
            template.active_version = None
            template.is_active = False
            template.status = DocumentTemplate.STATUS_RETIRED
            template.retirement_date = template.retirement_date or timezone.localdate()
            version.retirement_date = version.retirement_date or timezone.localdate()
    elif action == 'deactivate':
        if not was_active:
            raise ValidationError('Only the active template version can be deactivated.')
        template.active_version = None
        template.is_active = False
        template.status = DocumentTemplate.STATUS_INACTIVE
    else:
        raise ValidationError('Unsupported template workflow action.')
    version.save()
    template.save()
    _audit_library(request, version, 'update', {'workflow_action': action, 'status': [old_status, version.status]})
    return version


@transaction.atomic
def transition_clause(clause, action, request=None, actor=None):
    if action in {'approve', 'activate', 'deactivate', 'retire'} and not getattr(actor, 'is_staff', False):
        raise PermissionDenied('Sign in as an authorised document administrator to perform this action.')
    clause = DocumentClause.objects.select_for_update().get(pk=clause.pk)
    old_status, old_active = clause.approval_status, clause.is_active
    if action == 'submit':
        if clause.approval_status != DocumentClause.DRAFT:
            raise ValidationError('Only a draft clause can be submitted for review.')
        clause.approval_status = DocumentClause.REVIEW
    elif action == 'approve':
        if clause.approval_status != DocumentClause.REVIEW:
            raise ValidationError('Only a clause under review can be approved.')
        if not clause.clause_text.strip():
            raise ValidationError('Clause content is required before approval.')
        clause.approval_status = DocumentClause.APPROVED
        clause.approved_by = actor if getattr(actor, 'is_authenticated', False) else None
        clause.approved_at = timezone.now()
    elif action == 'deactivate':
        clause.is_active = False
    elif action == 'activate':
        if clause.approval_status != DocumentClause.APPROVED:
            raise ValidationError('Only an approved clause can be activated.')
        clause.is_active = True
    elif action == 'retire':
        clause.approval_status = DocumentClause.RETIRED
        clause.is_active = False
    else:
        raise ValidationError('Unsupported clause workflow action.')
    clause.save()
    _audit_library(request, clause, 'update', {
        'workflow_action': action,
        'approval_status': [old_status, clause.approval_status],
        'is_active': [old_active, clause.is_active],
    })
    return clause


@transaction.atomic
def allocate_document_number(document_type, on_date=None):
    if not document_type.pk:
        raise ValidationError('Save the document type before allocating a number.')
    if not document_type.number_prefix.strip() or document_type.number_padding < 1:
        raise ValidationError('Configure a non-empty prefix and positive number padding.')
    on_date = on_date or timezone.localdate()
    frequency = document_type.reset_frequency
    if frequency == DocumentType.RESET_NEVER:
        period_key = 'all'
    elif frequency == DocumentType.RESET_CALENDAR:
        period_key = str(on_date.year)
    elif frequency == DocumentType.RESET_FINANCIAL:
        start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
        period_key = f'{start_year}-{str(start_year + 1)[-2:]}'
    else:
        raise ValidationError('Unsupported document number reset frequency.')

    if document_type.year_convention == DocumentType.YEAR_CALENDAR:
        if frequency == DocumentType.RESET_FINANCIAL:
            raise ValidationError('Financial-year resets require financial-year numbering.')
        year_label = str(on_date.year)
    elif document_type.year_convention == DocumentType.YEAR_FINANCIAL:
        if frequency == DocumentType.RESET_CALENDAR:
            raise ValidationError('Calendar-year resets require calendar-year numbering.')
        start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
        year_label = f'{start_year}-{str(start_year + 1)[-2:]}'
    elif document_type.year_convention == DocumentType.YEAR_NONE:
        year_label = ''
    else:
        raise ValidationError('Unsupported document number year convention.')
    if frequency != DocumentType.RESET_NEVER and not year_label:
        raise ValidationError('A resetting sequence must include a year in its number.')

    sequence, _ = DocumentNumberSequence.objects.get_or_create(
        document_type=document_type, period_key=period_key,
    )
    DocumentNumberSequence.objects.filter(pk=sequence.pk).update(
        next_value=F('next_value') + 1,
    )
    sequence.refresh_from_db(fields=['next_value'])
    number = sequence.next_value - 1
    suffix = f'{number:0{document_type.number_padding}d}'
    parts = [document_type.number_prefix]
    if year_label:
        parts.append(year_label)
    parts.append(suffix)
    return '-'.join(parts)
