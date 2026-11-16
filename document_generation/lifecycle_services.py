"""Transactional lifecycle services for generated document versions."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from access_control.permissions import DOCUMENTS, Role, can_access, is_read_only_role, role_for

from .models import (
    DocumentActivityLog, DocumentApproval, DocumentNotification, DocumentSignature,
    GeneratedDocument,
)


MANAGER_ROLES = {
    Role.ADMIN, Role.DIRECTOR, Role.HEAD_HR, Role.HR, Role.DOCUMENT_CONTROLLER,
}
READ_ALL_ROLES = MANAGER_ROLES | {Role.MANAGEMENT, Role.AUDITOR}
EXPIRING_STATUSES = {'issued', 'sent', 'acknowledged', 'signed'}


def is_document_manager(user):
    return bool(user and user.is_authenticated and (user.is_superuser or role_for(user) in MANAGER_ROLES))


def can_view_document(user, document):
    if user and user.is_authenticated and (user.is_superuser or role_for(user) in READ_ALL_ROLES):
        return True
    if not user or not user.is_authenticated:
        return False
    return bool(document.created_by_id == user.pk or document.approvals.filter(approver=user).exists())


def can_view_all_documents(user):
    return bool(user and user.is_authenticated and (user.is_superuser or role_for(user) in READ_ALL_ROLES))


def _require_manager(actor):
    if not is_document_manager(actor):
        raise PermissionDenied('Only document management users can perform this action.')


def _actor_label(actor):
    if not actor or not actor.is_authenticated:
        return 'System'
    return actor.get_full_name() or actor.get_username()


def _log(document, action, actor, previous_status, comments='', **details):
    values = {
        'previous_status': previous_status,
        'new_status': document.status,
        'version_number': document.version_number,
    }
    if comments:
        values['comments'] = comments.strip()[:2000]
    values.update(details)
    return DocumentActivityLog.objects.create(
        generated_document=document, action=action,
        actor=actor if getattr(actor, 'is_authenticated', False) else None,
        actor_label=_actor_label(actor), details=values,
    )


def _notify(user, document, action, message):
    if user and user.is_active:
        DocumentNotification.objects.create(
            user=user, generated_document=document, action=action, message=message[:240],
        )


def _notify_creator(document, action, message):
    _notify(document.created_by, document, action, message)


def _notify_document_managers(document, action, message):
    User = get_user_model()
    for user in User.objects.filter(is_active=True):
        if not (user.is_superuser or role_for(user) in MANAGER_ROLES):
            continue
        DocumentNotification.objects.get_or_create(
            dedupe_key=f'lifecycle:{action}:{document.pk}:{user.pk}'[:180],
            defaults={'user': user, 'generated_document': document, 'action': action,
                      'subject': document.document_number, 'message': message[:240],
                      'trigger_event': action, 'delivery_method': 'in_app', 'delivery_status': 'sent'},
        )


def _workflow_users(document):
    if not document.document_type.approval_required:
        return []
    User = get_user_model()
    steps = document.document_type.approval_workflow or []
    if not isinstance(steps, list):
        raise ValidationError('Configure approval levels for this document type before submission.')
    users = []
    for index, step in enumerate(steps, start=1):
        try:
            user_id = int(step.get('user_id'))
        except (AttributeError, TypeError, ValueError) as error:
            raise ValidationError(f'Approval level {index} needs a valid approver.') from error
        try:
            user = User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist as error:
            raise ValidationError(f'The approver configured for level {index} is no longer active.') from error
        if not user.is_superuser and (
            not can_access(user, DOCUMENTS) or is_read_only_role(role_for(user))
        ):
            raise ValidationError(
                f'The user configured for level {index} needs writable Document module access.'
            )
        users.append(user)
    if document.document_type.approval_required and not users:
        raise ValidationError('This document type requires approval but has no configured approval levels.')
    return users


@transaction.atomic
def submit_document(document, actor):
    document = GeneratedDocument.objects.select_for_update().select_related('document_type').get(pk=document.pk)
    if document.status != 'draft':
        raise ValidationError('Only a draft version can be submitted for approval.')
    if document.superseding_documents.exists():
        raise ValidationError('Only the latest document version can be submitted.')
    if document.created_by_id != getattr(actor, 'pk', None) and not is_document_manager(actor):
        raise PermissionDenied('Only the document creator or document management can submit it.')
    steps = _workflow_users(document)
    previous = document.status
    if document.document_type.approval_required:
        now = timezone.now()
        for level, approver in enumerate(steps, start=1):
            DocumentApproval.objects.create(
                generated_document=document, approver=approver,
                approver_name=_actor_label(approver), level=level,
                status=DocumentApproval.PENDING if level == 1 else DocumentApproval.WAITING,
                requested_at=now,
            )
        document.status = 'pending_approval'
        action = 'submitted_for_approval'
    else:
        document.status = 'approved'
        document.approved_by = actor
        document.approved_at = timezone.now()
        action = 'approved_without_workflow'
    document.save()
    _log(document, action, actor, previous)
    if steps:
        for level, approver in enumerate(steps, start=1):
            _log(document, 'approval_assigned', actor, document.status,
                 approval_level=level, approver_id=approver.pk,
                 approver_name=_actor_label(approver))
        _notify(steps[0], document, 'approval_assigned',
                f'Approval level 1 is assigned: {document.document_number}.')
    _notify_creator(document, action, f'{document.document_number} was submitted for approval.')
    if document.status == 'approved':
        _notify_document_managers(document, 'issuance_task',
                                  f'{document.document_number} is approved and ready for an authorised issuance decision.')
    return document


@transaction.atomic
def decide_approval(approval, actor, decision, comments=''):
    approval = DocumentApproval.objects.select_for_update().select_related(
        'generated_document__document_type', 'approver',
    ).get(pk=approval.pk)
    document = GeneratedDocument.objects.select_for_update().get(pk=approval.generated_document_id)
    if decision not in {'approve', 'reject', 'revise'}:
        raise ValidationError('Choose approve, reject, or request revision.')
    if document.status != 'pending_approval' or approval.status != DocumentApproval.PENDING:
        raise ValidationError('This approval is no longer pending.')
    if not is_document_manager(actor) and approval.approver_id != getattr(actor, 'pk', None):
        raise PermissionDenied('This approval is assigned to another user.')
    first_pending = document.approvals.filter(status=DocumentApproval.PENDING).order_by('level').first()
    if not first_pending or first_pending.pk != approval.pk:
        raise ValidationError('Approval levels must be completed in sequence.')
    if decision in {'reject', 'revise'} and not comments.strip():
        raise ValidationError('A reason is required for rejection or a revision request.')
    previous = document.status
    now = timezone.now()
    approval.status = {
        'approve': DocumentApproval.APPROVED,
        'reject': DocumentApproval.REJECTED,
        'revise': DocumentApproval.REVISION,
    }[decision]
    approval.comments = comments.strip()
    approval.decided_at = now
    approval.save()
    event = {'approve': 'approved', 'reject': 'rejected', 'revise': 'revision_requested'}[decision]
    if decision == 'approve':
        next_step = document.approvals.filter(status=DocumentApproval.WAITING).order_by('level').first()
        if next_step:
            next_step.status = DocumentApproval.PENDING
            next_step.requested_at = now
            next_step.save(update_fields=('status', 'requested_at'))
            document.save(update_fields=('updated_at',))
            _log(document, 'approval_assigned', actor, document.status,
                 approval_level=next_step.level, approver_id=next_step.approver_id,
                 approver_name=next_step.approver_name)
            _notify(next_step.approver, document, 'approval_assigned',
                    f'Approval level {next_step.level} is assigned: {document.document_number}.')
        else:
            document.status = 'approved'
            document.approved_by = actor
            document.approved_at = now
            document.save()
    else:
        document.status = 'rejected' if decision == 'reject' else 'revision_requested'
        document.save()
        for waiting in document.approvals.filter(status__in=(DocumentApproval.WAITING, DocumentApproval.PENDING)):
            if waiting.pk != approval.pk:
                waiting.status = DocumentApproval.WITHDRAWN
                waiting.comments = f'Workflow stopped after level {approval.level} {event}.'
                waiting.decided_at = now
                waiting.save()
    _log(document, event, actor, previous, comments,
         approval_level=approval.level, approver_id=approval.approver_id,
         assigned_approver=approval.approver_name)
    _notify_creator(document, event, f'{document.document_number}: {event.replace("_", " ")}.')
    if document.status == 'approved':
        _notify_document_managers(document, 'issuance_task',
                                  f'{document.document_number} is approved and ready for an authorised issuance decision.')
    return document


@transaction.atomic
def issue_document(document, actor, official_issue_date=None, expiry_date=None):
    _require_manager(actor)
    document = GeneratedDocument.objects.select_for_update().select_related('document_type').get(pk=document.pk)
    if document.status != 'approved':
        raise ValidationError('Only a fully approved document version can be issued.')
    if document.superseding_documents.exists():
        raise ValidationError('A newer version exists; only the latest version can be issued.')
    if document.document_type.approval_required:
        if not document.approvals.exists() or document.approvals.exclude(
            status=DocumentApproval.APPROVED,
        ).exists():
            raise ValidationError('Every required approval level must be completed before issuance.')
    file_references = [ref for ref in (document.pdf_file_document, document.word_file_document) if ref]
    if not file_references or not any(ref.exists() and not ref.missing for ref in file_references):
        raise ValidationError('The approved document has no preserved output file.')
    issue_date = official_issue_date or timezone.localdate()
    expiry_date = expiry_date or document.expiry_date
    if document.document_type.requires_expiry and not expiry_date:
        raise ValidationError('An expiry date is required for this document type.')
    if expiry_date and expiry_date < issue_date:
        raise ValidationError('Expiry date cannot be before the issue date.')
    previous = document.status
    document.status = 'issued'
    document.official_issue_date = issue_date
    document.issued_at = timezone.now()
    document.issued_by = actor
    document.expiry_date = expiry_date
    document.save()
    _log(document, 'issued', actor, previous, official_issue_date=issue_date.isoformat(),
         expiry_date=expiry_date.isoformat() if expiry_date else '')
    _notify_creator(document, 'issued', f'{document.document_number} has been issued.')
    _notify_document_managers(document, 'sharing_task',
                              f'{document.document_number} was issued; review whether recipient sharing is required.')
    old = document.supersedes
    if old and old.status in {'issued', 'sent', 'acknowledged', 'signed'}:
        old_status = old.status
        old.status = 'superseded'
        old.save()
        revision_event = document.activity.filter(action='amendment_created').order_by('-created_at').first()
        reason = revision_event.details.get('revision_reason', '') if revision_event else ''
        _log(old, 'superseded', actor, old_status,
             replacement_document=document.document_number,
             reason=reason or 'Replaced by a newly issued version.')
        _notify_creator(old, 'superseded', f'{old.document_number} was superseded by {document.document_number}.')
    return document


@transaction.atomic
def mark_document_sent(document, actor, recipient, delivery_method, remarks='', sent_date=None):
    _require_manager(actor)
    document = GeneratedDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != 'issued':
        raise ValidationError('Only an issued document can be marked as sent.')
    if not recipient.strip():
        raise ValidationError('Enter the intended recipient.')
    if delivery_method not in {'email', 'physical_copy', 'hand_delivery', 'other'}:
        raise ValidationError('Choose a supported delivery method.')
    previous = document.status
    document.status = 'sent'
    document.sent_recipient = recipient.strip()[:180]
    document.delivery_method = delivery_method
    document.sent_remarks = remarks.strip()
    document.sent_at = timezone.make_aware(
        timezone.datetime.combine(sent_date or timezone.localdate(), timezone.datetime.min.time()),
    )
    document.sent_by = actor
    document.save()
    _log(document, 'marked_sent', actor, previous, remarks,
         recipient=document.sent_recipient, delivery_method=delivery_method,
         sent_at=document.sent_at.isoformat())
    _notify_creator(document, 'marked_sent', f'{document.document_number} was marked as sent.')
    return document


@transaction.atomic
def confirm_document_signed(document, actor, signer_name, comments=''):
    _require_manager(actor)
    document = GeneratedDocument.objects.select_for_update().get(pk=document.pk)
    if document.status not in {'issued', 'sent'}:
        raise ValidationError('Only an issued or sent document can be marked as manually signed.')
    if not signer_name.strip():
        raise ValidationError('Enter the name of the person who confirmed signing.')
    previous = document.status
    signature = DocumentSignature.objects.create(
        generated_document=document, signer_name=signer_name.strip(), method=DocumentSignature.MANUAL,
        status=DocumentSignature.SIGNED, signed_at=timezone.now(), metadata={'confirmation': comments.strip()},
    )
    document.status = 'signed'
    document.save()
    _log(document, 'signed_manually_confirmed', actor, previous, comments,
         signer=signature.signer_name, signed_at=signature.signed_at.isoformat(),
         electronic_signature=False)
    _notify_creator(document, 'signed', f'{document.document_number} was marked as manually signed.')
    return document


@transaction.atomic
def cancel_document(document, actor, reason):
    _require_manager(actor)
    document = GeneratedDocument.objects.select_for_update().get(pk=document.pk)
    if document.status in {'cancelled', 'superseded', 'expired'}:
        raise ValidationError('This document cannot be cancelled from its current status.')
    if document.status in {'issued', 'sent', 'acknowledged', 'signed'} and not is_document_manager(actor):
        raise PermissionDenied('Cancelling an issued document requires document management authority.')
    if not reason.strip():
        raise ValidationError('A cancellation reason is required.')
    previous = document.status
    document.status = 'cancelled'
    document.cancellation_reason = reason.strip()
    document.cancelled_at = timezone.now()
    document.cancelled_by = actor
    document.save()
    for approval in document.approvals.filter(status__in=(DocumentApproval.WAITING, DocumentApproval.PENDING)):
        approval.status = DocumentApproval.WITHDRAWN
        approval.comments = 'Workflow stopped because the document was cancelled.'
        approval.decided_at = timezone.now()
        approval.save()
    _log(document, 'cancelled', actor, previous, reason)
    _notify_creator(document, 'cancelled', f'{document.document_number} was cancelled.')
    return document


@transaction.atomic
def evaluate_expired_documents(queryset=None):
    today = timezone.localdate()
    candidates = (queryset or GeneratedDocument.objects.all()).filter(
        status__in=EXPIRING_STATUSES, expiry_date__lt=today,
    ).select_related('created_by')
    for document in candidates:
        previous = document.status
        document.status = 'expired'
        document.save()
        _log(document, 'expired', None, previous,
             expiry_date=document.expiry_date.isoformat() if document.expiry_date else '')
        _notify_creator(document, 'expired', f'{document.document_number} has expired.')


@transaction.atomic
def notify_upcoming_expiries():
    today = timezone.localdate()
    upcoming = GeneratedDocument.objects.filter(
        status__in=EXPIRING_STATUSES, expiry_date__gte=today,
        expiry_date__lte=today + timedelta(days=30), created_by__isnull=False,
    ).select_related('created_by')
    for document in upcoming:
        if not DocumentNotification.objects.filter(
            user=document.created_by, generated_document=document, action='expiring_soon',
        ).exists():
            _notify(document.created_by, document, 'expiring_soon',
                    f'{document.document_number} expires on {document.expiry_date:%d %b %Y}.')
