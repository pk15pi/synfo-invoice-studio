"""Local management pages for issued-document sharing and signed-copy tracking."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Count, F
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.pagination import paginate as qb_paginate
from invoice_app.table_sorting import apply_queryset_sort
from document_management.models import full_path
from document_management.services import private_storage

from .forms import (
    DocumentAcknowledgementForm, DocumentDeliveryForm, DocumentReminderForm, DocumentShareForm,
    DocumentVerificationForm, RevokeShareForm, SignedCopyUploadForm, SignatoryDecisionForm,
)
from .lifecycle_services import can_view_all_documents, can_view_document, is_document_manager
from .models import DocumentShare, DocumentShareSignatory, DocumentSignedCopy, GeneratedDocument
from .sharing_services import (
    can_manage_share, create_share, record_acknowledgement, record_delivery, record_reminder,
    revoke_share, upload_signed_copy, verify_signed_copy, decline_signature, expire_signature,
)


def _visible_shares(user):
    queryset = DocumentShare.objects.select_related(
        'generated_document__document_type', 'generated_document__created_by', 'shared_by', 'party',
    ).prefetch_related('signatories', 'events').annotate(
        signatory_total=Count('signatories', distinct=True),
        signatory_signed=Count('signatories', filter=Q(signatories__status='signed'), distinct=True),
    )
    if can_view_all_documents(user):
        return queryset
    if not user.is_authenticated:
        return queryset.none()
    return queryset.filter(
        Q(generated_document__created_by=user) | Q(generated_document__approvals__approver=user)
        | Q(shared_by=user),
    ).distinct()


@local_only
def sharing_dashboard(request):
    shares = _visible_shares(request.user)
    q = request.GET.get('q', '').strip()[:100]
    method = request.GET.get('method', '').strip()
    ack = request.GET.get('acknowledgement', '').strip()
    sig = request.GET.get('signature', '').strip()
    type_id = request.GET.get('type', '').strip()
    recipient = request.GET.get('recipient', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if q:
        shares = shares.filter(
            Q(generated_document__document_number__icontains=q)
            | Q(generated_document__document_type__name__icontains=q)
            | Q(recipient_name__icontains=q) | Q(recipient_email__icontains=q)
        )
    if method:
        shares = shares.filter(sharing_method=method)
    if ack:
        shares = shares.filter(acknowledgement_status=ack)
    if sig == 'not_required':
        shares = shares.filter(signature_required=False)
    elif sig == 'pending':
        shares = shares.filter(signature_required=True, signatory_signed=0).exclude(
            signatories__status__in=('declined', 'expired'))
    elif sig == 'partially_signed':
        shares = shares.filter(signature_required=True, signatory_signed__gt=0).exclude(
            signatory_signed=F('signatory_total'))
    elif sig == 'fully_signed':
        shares = shares.filter(signature_required=True, signatory_total__gt=0,
                               signatory_signed=F('signatory_total'))
    elif sig == 'declined':
        shares = shares.filter(signatories__status='declined').distinct()
    elif sig == 'expired':
        shares = shares.filter(signatories__status='expired').distinct()
    if type_id.isdigit():
        shares = shares.filter(generated_document__document_type_id=int(type_id))
    if recipient:
        shares = shares.filter(recipient_type=recipient)
    from django.utils.dateparse import parse_date
    if parse_date(date_from):
        shares = shares.filter(shared_at__date__gte=parse_date(date_from))
    if parse_date(date_to):
        shares = shares.filter(shared_at__date__lte=parse_date(date_to))
    shares, _, _ = apply_queryset_sort(shares, request, {
        'number': 'generated_document__document_number', 'type': 'generated_document__document_type__name',
        'recipient': 'recipient_name', 'shared': 'shared_at', 'method': 'sharing_method',
        'ack': 'acknowledgement_status',
    })
    page = qb_paginate(request, shares)
    for share in page.object_list:
        share.lifecycle_signature_status = share.signature_status
    base = _visible_shares(request.user)
    followups = []
    today = timezone.localdate()
    for item in base.filter(
        Q(acknowledgement_status='pending')
        | Q(signature_required=True, signatories__status__in=('pending', 'submitted')),
        revoked_at__isnull=True,
    ).distinct().prefetch_related('events', 'signatories'):
        last_reminder = item.events.filter(action__in=('reminder_recorded', 'automated_reminder')).order_by('-created_at').first()
        item.days_pending = max(0, (today - item.shared_at.date()).days)
        item.last_reminder_at = last_reminder.created_at if last_reminder else None
        followups.append(item)
    return render(request, 'document_generation/sharing_dashboard.html', {
        'page': page, 'q': q, 'method': method, 'ack': ack, 'sig': sig, 'type_id': type_id,
        'recipient': recipient, 'date_from': date_from, 'date_to': date_to,
        'types': GeneratedDocument.objects.values('document_type_id', 'document_type__name').distinct(),
        'methods': DocumentShare.METHODS, 'ack_statuses': DocumentShare.ACK_STATUSES,
        'recipient_types': DocumentShare.RECIPIENT_TYPES, 'can_manage': is_document_manager(request.user),
        'cards': {
            'shared': base.count(),
            'ack_pending': base.filter(acknowledgement_status='pending').count(),
            'acknowledged': base.filter(acknowledgement_status='acknowledged').count(),
            'signature_pending': sum(1 for item in base if item.signature_status == 'pending'),
            'partially_signed': sum(1 for item in base if item.signature_status == 'partially_signed'),
            'fully_signed': sum(1 for item in base if item.signature_status == 'fully_signed'),
            'declined': sum(1 for item in base if item.signature_status == 'declined'),
            'awaiting_verification': DocumentShareSignatory.objects.filter(
                share__in=base, verification_status='awaiting').count(),
            'verified': DocumentShareSignatory.objects.filter(
                share__in=base, verification_status='verified').count(),
            'revoked': base.filter(revoked_at__isnull=False).count(),
        },
        'external_links_enabled': False,
        'followups': followups,
    })


@local_only
def share_document(request, document_id):
    document = get_object_or_404(GeneratedDocument.objects.select_related('document_type'), pk=document_id)
    if not can_manage_share(request.user, document) or not can_view_document(request.user, document):
        raise PermissionDenied('You are not authorised to share this document.')
    form = DocumentShareForm(request.POST or None, document=document)
    if request.method == 'POST' and form.is_valid():
        try:
            share = create_share(document, request.user, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Sharing record created. Email or physical delivery is tracked manually; nothing was sent by the application.')
            return redirect('document_generation:share_detail', share_id=share.pk)
    return render(request, 'document_generation/share_form.html', {'form': form, 'document': document})


@local_only
def share_detail(request, share_id):
    share = get_object_or_404(_visible_shares(request.user), pk=share_id)
    can_manage = can_manage_share(request.user, share.generated_document)
    if not can_manage and not can_view_document(request.user, share.generated_document):
        raise PermissionDenied
    return render(request, 'document_generation/share_detail.html', {
        'share': share, 'events': share.events.select_related('actor').all(),
        'signatories': share.signatories.select_related('signed_document', 'uploaded_by', 'verified_by')
        .prefetch_related('signed_copies__document').all(),
        'can_manage': can_manage, 'delivery_form': DocumentDeliveryForm(),
        'ack_form': DocumentAcknowledgementForm(initial={'acknowledgement_name': share.recipient_name}),
        'reminder_form': DocumentReminderForm(), 'verify_form': DocumentVerificationForm(),
        'upload_form': SignedCopyUploadForm(), 'revoke_form': RevokeShareForm(),
        'decline_form': SignatoryDecisionForm(),
        'has_follow_up': share.acknowledgement_status == 'pending' or share.signature_status in {'pending', 'partially_signed'},
    })


@local_only
@require_POST
def share_action(request, share_id, action):
    share = get_object_or_404(DocumentShare.objects.select_related('generated_document'), pk=share_id)
    if not can_manage_share(request.user, share.generated_document):
        raise PermissionDenied
    try:
        if action == 'delivery':
            form = DocumentDeliveryForm(request.POST, request.FILES)
            if not form.is_valid():
                raise ValidationError('; '.join(form.errors.get_json_data().keys()))
            record_delivery(share, request.user, form.cleaned_data['remarks'], form.cleaned_data['evidence'])
            messages.success(request, 'Delivery recorded. Receipt and signature status remain separate.')
        elif action == 'acknowledgement':
            form = DocumentAcknowledgementForm(request.POST, request.FILES)
            if not form.is_valid():
                raise ValidationError('; '.join(form.errors.get_json_data().keys()))
            record_acknowledgement(share, request.user, form.cleaned_data)
            messages.success(request, 'Acknowledgement status and evidence recorded.')
        elif action == 'reminder':
            form = DocumentReminderForm(request.POST)
            if not form.is_valid():
                raise ValidationError('; '.join(form.errors.get_json_data().keys()))
            record_reminder(share, request.user, form.cleaned_data['method'], form.cleaned_data['remarks'],
                            form.cleaned_data['next_follow_up_date'])
            messages.success(request, 'Reminder recorded in the sharing history.')
        elif action == 'revoke':
            form = RevokeShareForm(request.POST)
            if not form.is_valid():
                raise ValidationError('A revocation reason is required.')
            revoke_share(share, request.user, form.cleaned_data['reason'])
            messages.success(request, 'Sharing access record revoked. Any copies already delivered cannot be recalled.')
        elif action == 'upload-signed':
            form = SignedCopyUploadForm(request.POST, request.FILES)
            signatory = get_object_or_404(DocumentShareSignatory, pk=request.POST.get('signatory_id'), share=share)
            if not form.is_valid():
                raise ValidationError('Select a valid PDF, JPG or PNG signed copy.')
            upload_signed_copy(signatory, request.user, form.cleaned_data['file'])
            messages.success(request, 'Signed copy uploaded privately and is awaiting management verification.')
        elif action == 'verify-signed':
            form = DocumentVerificationForm(request.POST)
            signatory = get_object_or_404(DocumentShareSignatory, pk=request.POST.get('signatory_id'), share=share)
            if not form.is_valid():
                raise ValidationError('Choose a verification result; a rejection also requires a reason.')
            verify_signed_copy(signatory, request.user, form.cleaned_data['decision'], form.cleaned_data['reason'])
            messages.success(request, 'Signed-copy verification recorded.')
        elif action == 'decline-signature':
            form = SignatoryDecisionForm(request.POST)
            signatory = get_object_or_404(DocumentShareSignatory, pk=request.POST.get('signatory_id'), share=share)
            if not form.is_valid():
                raise ValidationError('A reason is required for a declined signature.')
            decline_signature(signatory, request.user, form.cleaned_data['reason'])
            messages.success(request, 'Signature decline recorded with its reason.')
        elif action == 'expire-signature':
            form = SignatoryDecisionForm(request.POST)
            signatory = get_object_or_404(DocumentShareSignatory, pk=request.POST.get('signatory_id'), share=share)
            if not form.is_valid():
                raise ValidationError('Enter why this signature request expired.')
            expire_signature(signatory, request.user, form.cleaned_data['reason'])
            messages.success(request, 'Signature expiry recorded.')
        else:
            raise Http404
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    return redirect('document_generation:share_detail', share_id=share.pk)


@local_only
def signed_copy_download(request, copy_id):
    copy = get_object_or_404(DocumentSignedCopy.objects.select_related(
        'signatory__share__generated_document', 'document'), pk=copy_id)
    if not can_view_document(request.user, copy.signatory.share.generated_document):
        raise PermissionDenied
    if copy.document.missing:
        raise Http404('Signed copy is missing from private storage.')
    try:
        full_path(copy.document.file_path)
        handle = private_storage.open(copy.document.file_path, 'rb')
    except OSError as error:
        raise Http404('Signed copy is missing from private storage.') from error
    from .sharing_services import _event
    _event(copy.signatory.share, 'signed_copy_downloaded_by_authorised_user', request.user,
           signatory=copy.signatory.name, sha256=copy.sha256, signed_copy_id=copy.pk)
    response = FileResponse(handle, as_attachment=True,
                            filename=copy.document.original_filename,
                            content_type=copy.document.mime_type or 'application/octet-stream')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@local_only
def share_evidence_download(request, share_id, document_id):
    from document_management.models import Document
    share = get_object_or_404(DocumentShare.objects.select_related('generated_document'), pk=share_id)
    if not can_view_document(request.user, share.generated_document):
        raise PermissionDenied
    linked = (share.delivery_evidence_id == document_id
              or share.acknowledgement_evidence_id == document_id
              or share.events.filter(details__evidence_document_id=document_id).exists()
              or share.signatories.filter(signed_copies__document_id=document_id).exists())
    if not linked:
        raise Http404
    evidence = get_object_or_404(Document, pk=document_id)
    if evidence.missing:
        raise Http404('Evidence file is missing from private storage.')
    try:
        full_path(evidence.file_path)
        handle = private_storage.open(evidence.file_path, 'rb')
    except OSError as error:
        raise Http404('Evidence file is missing from private storage.') from error
    from .sharing_services import _event
    _event(share, 'evidence_downloaded_by_authorised_user', request.user,
           evidence_document_id=evidence.pk, sha256=evidence.sha256)
    response = FileResponse(handle, as_attachment=True, filename=evidence.original_filename,
                            content_type=evidence.mime_type or 'application/octet-stream')
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
