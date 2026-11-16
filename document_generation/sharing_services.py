"""Controlled document sharing, follow-up, acknowledgement and signature services."""
import os
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from access_control.permissions import DOCUMENTS, can_access, is_read_only_role, role_for
from document_management.models import Document, full_path, safe_rel_path, sha256_of
from employee_management.storage import private_storage
from employee_management.validators import sniff_upload

from .lifecycle_services import can_view_document, is_document_manager
from .models import (
    DocumentActivityLog, DocumentShare, DocumentShareEvent, DocumentShareSignatory,
    DocumentRenewal, DocumentSignedCopy, GeneratedDocument,
)

MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
MIME_TYPES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg'}
SHAREABLE_STATUSES = {'issued', 'sent', 'signed', 'acknowledged'}


def can_manage_share(user, document):
    if is_document_manager(user):
        return True
    return bool(
        user and user.is_authenticated and can_access(user, DOCUMENTS)
        and not is_read_only_role(role_for(user))
    )


def require_share_manager(actor, share):
    if (not can_manage_share(actor, share.generated_document)
            or not can_view_document(actor, share.generated_document)):
        raise PermissionDenied('You are not authorised to manage sharing for this document.')


def _actor_label(actor):
    return actor.get_full_name() or actor.get_username() if actor and actor.is_authenticated else 'System'


def _event(share, action, actor, **details):
    return DocumentShareEvent.objects.create(
        share=share, action=action, actor=actor if actor and actor.is_authenticated else None,
        actor_label=_actor_label(actor), details=details,
    )


def _activity(document, action, actor, previous_status=None, **details):
    return DocumentActivityLog.objects.create(
        generated_document=document, action=action,
        actor=actor if actor and actor.is_authenticated else None,
        actor_label=_actor_label(actor),
        details={'previous_status': previous_status or document.status,
                 'new_status': document.status, 'version_number': document.version_number, **details},
    )


def store_evidence(upload, title, actor, tag):
    if not upload:
        return None
    ext = sniff_upload(upload, MAX_EVIDENCE_BYTES)
    rel = f'document_generation/evidence/{uuid.uuid4().hex}.{ext}'
    upload.seek(0)
    rel = safe_rel_path(private_storage.save(rel, upload))
    return Document.objects.create(
        category='agreement', title=title[:200], source_type='document_share_evidence',
        source_id=uuid.uuid4().hex, file_path=rel,
        original_filename=os.path.basename(upload.name or '')[:255],
        mime_type=MIME_TYPES[ext], sensitivity=Document.CONFIDENTIAL,
        tags=f'document_generation,{tag}', uploaded_at=timezone.now(),
        size=os.path.getsize(full_path(rel)), sha256=sha256_of(rel), last_checked=timezone.now(),
    )


def _require_shareable(document):
    if document.status not in SHAREABLE_STATUSES:
        raise ValidationError('Only an approved and issued document can be shared.')
    if document.expiry_date and document.expiry_date < timezone.localdate():
        raise ValidationError('An expired document cannot be shared.')
    if document.superseding_documents.filter(status__in=SHAREABLE_STATUSES | {'approved'}).exists():
        raise ValidationError('Only the current issued version can be shared.')
    if not (document.pdf_file_document_id or document.word_file_document_id or document.file_document_id):
        raise ValidationError('This issued document has no generated file to share.')


@transaction.atomic
def create_share(document, actor, cleaned_data):
    if not can_manage_share(actor, document) or not can_view_document(actor, document):
        raise PermissionDenied('You are not authorised to share this document.')
    document = GeneratedDocument.objects.select_for_update().get(pk=document.pk)
    _require_shareable(document)
    now = timezone.now()
    ack_requested = bool(cleaned_data.get('acknowledgement_requested'))
    share = DocumentShare(
        generated_document=document, party=cleaned_data.get('party'),
        recipient_type=cleaned_data['recipient_type'], recipient_name=cleaned_data['recipient_name'],
        recipient_email=cleaned_data.get('recipient_email', ''),
        sharing_method=cleaned_data['sharing_method'], shared_at=now, shared_by=actor,
        remarks=cleaned_data.get('remarks', ''),
        acknowledgement_status='pending' if ack_requested else 'not_requested',
        acknowledgement_requested_at=now if ack_requested else None,
        signature_required=bool(cleaned_data.get('signature_required')),
        next_follow_up_date=cleaned_data.get('next_follow_up_date'),
    )
    share.save()
    for name, role in cleaned_data.get('parsed_signatories', []):
        DocumentShareSignatory.objects.create(share=share, name=name, role=role)
    _event(share, 'shared', actor, document_number=document.document_number,
           version=document.version_number, method=share.sharing_method,
           recipient=share.recipient_name, recipient_email=share.recipient_email,
           email_sent=False)
    _activity(document, 'shared', actor, recipient=share.recipient_name,
              recipient_type=share.recipient_type, sharing_method=share.sharing_method,
              share_id=share.pk, email_sent=False)
    return share


@transaction.atomic
def record_delivery(share, actor, remarks='', evidence_upload=None):
    require_share_manager(actor, share)
    share = DocumentShare.objects.select_for_update().get(pk=share.pk)
    if share.revoked_at:
        raise ValidationError('This sharing record has been revoked.')
    evidence = store_evidence(evidence_upload, f'Delivery evidence - {share.generated_document.document_number}', actor, 'delivery')
    share.delivery_status = 'delivered'
    if evidence:
        share.delivery_evidence = evidence
    share.save()
    _event(share, 'delivered', actor, remarks=remarks.strip(), evidence_document_id=evidence.pk if evidence else None)
    return share


@transaction.atomic
def record_acknowledgement(share, actor, cleaned_data):
    require_share_manager(actor, share)
    share = DocumentShare.objects.select_for_update().get(pk=share.pk)
    if share.acknowledgement_status == 'not_requested':
        raise ValidationError('Acknowledgement was not requested for this share.')
    evidence = store_evidence(cleaned_data.get('evidence'),
                              f'Acknowledgement evidence - {share.generated_document.document_number}', actor,
                              'acknowledgement')
    previous = share.acknowledgement_status
    share.acknowledgement_status = cleaned_data['status']
    share.acknowledgement_name = cleaned_data['acknowledgement_name'].strip()
    share.acknowledgement_method = cleaned_data['method']
    share.acknowledgement_remarks = cleaned_data.get('remarks', '').strip()
    share.acknowledged_at = timezone.now() if cleaned_data['status'] == 'acknowledged' else None
    share.acknowledgement_updated_by = actor
    if evidence:
        share.acknowledgement_evidence = evidence
    share.save()
    _event(share, 'acknowledgement_updated', actor, previous_status=previous,
           status=share.acknowledgement_status, recipient_name=share.acknowledgement_name,
           method=share.acknowledgement_method, remarks=share.acknowledgement_remarks,
           evidence_document_id=evidence.pk if evidence else None)
    return share


@transaction.atomic
def record_reminder(share, actor, method, remarks='', next_follow_up_date=None):
    require_share_manager(actor, share)
    if share.revoked_at:
        raise ValidationError('A reminder cannot be recorded for a revoked share.')
    if (share.acknowledgement_status != 'pending'
            and share.signature_status not in {'pending', 'partially_signed'}):
        raise ValidationError('There is no pending acknowledgement or signature to follow up.')
    share.next_follow_up_date = next_follow_up_date
    share.save()
    _event(share, 'reminder_recorded', actor, method=method, remarks=remarks.strip(),
           next_follow_up_date=next_follow_up_date.isoformat() if next_follow_up_date else '')
    return share


@transaction.atomic
def revoke_share(share, actor, reason):
    require_share_manager(actor, share)
    if not reason.strip():
        raise ValidationError('Enter a reason for revoking this sharing record.')
    share = DocumentShare.objects.select_for_update().get(pk=share.pk)
    if share.revoked_at:
        raise ValidationError('This sharing record is already revoked.')
    share.revoked_at = timezone.now()
    share.revoked_by = actor
    share.revocation_reason = reason.strip()
    share.save()
    _event(share, 'revoked', actor, reason=share.revocation_reason)
    _activity(share.generated_document, 'sharing_revoked', actor,
              recipient=share.recipient_name, share_id=share.pk, reason=share.revocation_reason)
    return share


@transaction.atomic
def upload_signed_copy(signatory, actor, upload):
    share = signatory.share
    require_share_manager(actor, share)
    if not share.signature_required:
        raise ValidationError('This share does not require a signature.')
    if signatory.status in {'signed', 'declined', 'expired'}:
        raise ValidationError('This signatory is no longer awaiting a signed copy.')
    signed_doc = store_evidence(upload, f'Signed copy - {share.generated_document.document_number}', actor, 'signed-copy')
    copy_record = DocumentSignedCopy.objects.create(
        signatory=signatory, document=signed_doc, uploaded_by=actor,
        uploaded_at=timezone.now(), sha256=signed_doc.sha256, verification_status='awaiting',
    )
    signatory.signed_document = signed_doc
    signatory.uploaded_by = actor
    signatory.uploaded_at = timezone.now()
    signatory.file_sha256 = signed_doc.sha256
    signatory.status = 'submitted'
    signatory.verification_status = 'awaiting'
    signatory.verification_reason = ''
    signatory.save()
    _event(share, 'signed_copy_uploaded', actor, signatory=signatory.name,
           document_id=signed_doc.pk, signed_copy_id=copy_record.pk,
           sha256=signed_doc.sha256, verification='awaiting')
    _activity(share.generated_document, 'signed_copy_uploaded', actor,
              recipient=share.recipient_name, signatory=signatory.name, sha256=signed_doc.sha256)
    return signatory


@transaction.atomic
def verify_signed_copy(signatory, actor, decision, reason=''):
    share = signatory.share
    require_share_manager(actor, share)
    signatory = DocumentShareSignatory.objects.select_for_update().get(pk=signatory.pk)
    if not signatory.signed_document_id or signatory.verification_status not in {'awaiting', 'rejected'}:
        raise ValidationError('There is no signed copy awaiting verification.')
    if decision == 'rejected' and not reason.strip():
        raise ValidationError('A reason is required when verification is rejected.')
    if decision not in {'verified', 'rejected'}:
        raise ValidationError('Choose a valid verification outcome.')
    copy_record = signatory.signed_copies.filter(
        document_id=signatory.signed_document_id,
    ).order_by('-uploaded_at', '-id').first()
    if not copy_record or copy_record.verification_status != 'awaiting':
        raise ValidationError('The latest signed copy is not awaiting verification.')
    copy_record.verification_status = decision
    copy_record.verification_reason = reason.strip()
    copy_record.verified_by = actor
    copy_record.verified_at = timezone.now()
    copy_record.save()
    signatory.verification_status = decision
    signatory.verification_reason = reason.strip()
    signatory.verified_by = actor
    signatory.verified_at = timezone.now()
    if decision == 'verified':
        signatory.status = 'signed'
        signatory.signed_at = signatory.uploaded_at or timezone.now()
    else:
        signatory.status = 'pending'
        signatory.signed_at = None
    signatory.save()
    _event(share, 'signed_copy_' + decision, actor, signatory=signatory.name, reason=reason.strip(),
           sha256=signatory.file_sha256, signed_copy_id=copy_record.pk)
    if share.signature_status == 'fully_signed':
        document = share.generated_document
        if document.status in {'issued', 'sent'}:
            previous = document.status
            document.status = 'signed'
            document.save()
            _activity(document, 'all_signatories_verified', actor, previous,
                      share_id=share.pk, signatories=list(share.signatories.values_list('name', flat=True)))
    return signatory


@transaction.atomic
def decline_signature(signatory, actor, reason):
    share = signatory.share
    require_share_manager(actor, share)
    if not reason.strip():
        raise ValidationError('A reason is required when a signatory declines.')
    signatory = DocumentShareSignatory.objects.select_for_update().get(pk=signatory.pk)
    if signatory.status in {'signed', 'expired'}:
        raise ValidationError('A completed or expired signature cannot be changed.')
    signatory.status = 'declined'
    signatory.verification_status = 'not_submitted'
    signatory.verification_reason = reason.strip()
    signatory.save()
    _event(share, 'signature_declined', actor, signatory=signatory.name, reason=reason.strip())
    return signatory


@transaction.atomic
def expire_signature(signatory, actor, reason):
    share = signatory.share
    require_share_manager(actor, share)
    if not reason.strip():
        raise ValidationError('Enter why this signature request expired.')
    signatory = DocumentShareSignatory.objects.select_for_update().get(pk=signatory.pk)
    if signatory.status in {'signed', 'declined', 'expired'}:
        raise ValidationError('This signatory is not awaiting a signature.')
    signatory.status = 'expired'
    signatory.verification_status = 'not_submitted'
    signatory.verification_reason = reason.strip()
    signatory.save()
    _event(share, 'signature_expired', actor, signatory=signatory.name, reason=reason.strip())
    return signatory


@transaction.atomic
def update_renewal(document, actor, cleaned_data):
    if not can_manage_share(actor, document):
        raise PermissionDenied('You are not authorised to update renewal tracking.')
    if not document.document_type.renewal_enabled:
        raise ValidationError('Renewal tracking is not enabled for this document type.')
    renewal, _ = DocumentRenewal.objects.select_for_update().get_or_create(
        generated_document=document,
        defaults={'lead_time_days': document.document_type.renewal_lead_time_days},
    )
    renewal.status = cleaned_data['status']
    renewal.lead_time_days = cleaned_data['lead_time_days']
    renewal.renewal_document = cleaned_data.get('renewal_document')
    renewal.renewal_date = cleaned_data.get('renewal_date')
    renewal.new_expiry_date = cleaned_data.get('new_expiry_date')
    if renewal.status == 'renewed':
        new_document = renewal.renewal_document
        if not new_document or new_document.status not in {'issued', 'sent', 'signed', 'acknowledged'}:
            raise ValidationError('A renewed document must be issued through the normal approval and issue workflow.')
        if not renewal.renewal_date:
            renewal.renewal_date = new_document.official_issue_date or timezone.localdate()
        if not renewal.new_expiry_date:
            renewal.new_expiry_date = new_document.expiry_date
    renewal.initiated_by = actor
    renewal.initiated_at = timezone.now()
    renewal.remarks = cleaned_data.get('remarks', '').strip()
    renewal.full_clean()
    renewal.save()
    _activity(document, 'renewal_' + renewal.status, actor, renewal_status=renewal.status,
              lead_time_days=renewal.lead_time_days, remarks=renewal.remarks)
    return renewal


def create_renewal_tracking(document):
    if not document.document_type.renewal_enabled or not document.expiry_date:
        return None
    renewal, created = DocumentRenewal.objects.get_or_create(
        generated_document=document,
        defaults={'lead_time_days': document.document_type.renewal_lead_time_days},
    )
    today = timezone.localdate()
    if renewal.status in {'not_due', 'due', 'overdue'}:
        delta = (document.expiry_date - today).days
        renewal.status = 'overdue' if delta < 0 else ('due' if delta <= renewal.lead_time_days else 'not_due')
        renewal.save(update_fields=['status', 'updated_at'])
    return renewal
