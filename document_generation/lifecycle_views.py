from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.pagination import paginate as qb_paginate
from invoice_app.table_sorting import apply_queryset_sort
from django.utils.dateparse import parse_date

from .forms import (
    ApprovalStepFormSet, CancelDocumentForm, IssueDocumentForm,
    SendDocumentForm, SignedDocumentForm,
)
from .lifecycle_services import (
    can_view_all_documents, can_view_document, cancel_document, confirm_document_signed, decide_approval,
    evaluate_expired_documents, is_document_manager, issue_document,
    mark_document_sent, notify_upcoming_expiries, submit_document,
)
from .sharing_services import can_manage_share
from .models import (
    DocumentApproval, DocumentNotification, DocumentType, GeneratedDocument,
)


STATUS_CARDS = (
    ('draft', 'Draft Documents'), ('pending_approval', 'Pending Approval'),
    ('revision_requested', 'Revision Requested'), ('rejected', 'Rejected'),
    ('approved', 'Approved'), ('issued', 'Issued'), ('sent', 'Sent'),
    ('expired', 'Expired'), ('cancelled', 'Cancelled'),
)


def _user_document_scope(request):
    documents = GeneratedDocument.objects.all()
    if can_view_all_documents(request.user):
        return documents
    if not request.user.is_authenticated:
        return documents.none()
    return documents.filter(
        Q(created_by=request.user) | Q(approvals__approver=request.user),
    ).distinct()


@local_only
def lifecycle_dashboard(request):
    evaluate_expired_documents()
    notify_upcoming_expiries()
    documents = _user_document_scope(request).select_related(
        'document_type', 'created_by', 'approved_by', 'issued_by', 'supersedes',
    ).prefetch_related('parties', 'approvals__approver')
    q = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()
    type_id = request.GET.get('type', '').strip()
    approver_id = request.GET.get('approver', '').strip()
    creator_id = request.GET.get('created_by', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if q:
        documents = documents.filter(
            Q(document_number__icontains=q) | Q(document_type__name__icontains=q)
            | Q(parties__display_name__icontains=q)
        )
    if status:
        documents = documents.filter(status=status)
    if type_id.isdigit():
        documents = documents.filter(document_type_id=int(type_id))
    if approver_id.isdigit():
        documents = documents.filter(approvals__approver_id=int(approver_id))
    if creator_id.isdigit():
        documents = documents.filter(created_by_id=int(creator_id))
    if parse_date(date_from):
        documents = documents.filter(created_at__date__gte=parse_date(date_from))
    if parse_date(date_to):
        documents = documents.filter(created_at__date__lte=parse_date(date_to))
    documents = documents.distinct()
    documents, _, _ = apply_queryset_sort(documents, request, {
        'number': 'document_number', 'type': 'document_type__name',
        'created': 'created_at', 'status': 'status', 'updated': 'updated_at',
    })
    page = qb_paginate(request, documents)
    today = timezone.localdate()
    expiry_queryset = _user_document_scope(request).filter(
        status__in=('issued', 'sent', 'signed', 'acknowledged'),
        expiry_date__gte=today, expiry_date__lte=today + timedelta(days=30),
    ).select_related('document_type').order_by('expiry_date')[:10]
    cards = [(key, label, _user_document_scope(request).filter(status=key).count())
             for key, label in STATUS_CARDS]
    user_notifications = (DocumentNotification.objects.filter(user=request.user)
                          if request.user.is_authenticated else DocumentNotification.objects.none())
    unread = user_notifications.filter(read_at__isnull=True)[:10]
    User = get_user_model()
    approvers = User.objects.filter(document_approvals__isnull=False).distinct().order_by(
        'first_name', 'last_name', 'username',
    )
    creators = User.objects.filter(created_generated_documents__isnull=False).distinct().order_by(
        'first_name', 'last_name', 'username',
    )
    return render(request, 'document_generation/lifecycle_dashboard.html', {
        'page': page, 'cards': cards, 'types': DocumentType.objects.filter(is_active=True),
        'approvers': approvers, 'creators': creators, 'q': q, 'status': status,
        'type_id': type_id, 'approver_id': approver_id, 'creator_id': creator_id,
        'date_from': date_from, 'date_to': date_to, 'statuses': GeneratedDocument._meta.get_field('status').choices,
        'upcoming_expiry': expiry_queryset, 'notifications': unread,
        'unread_count': user_notifications.filter(read_at__isnull=True).count(),
        'can_manage': is_document_manager(request.user),
    })


@local_only
def pending_approvals(request):
    evaluate_expired_documents()
    queryset = DocumentApproval.objects.filter(
        status__in=(DocumentApproval.PENDING, DocumentApproval.APPROVED,
                    DocumentApproval.REJECTED, DocumentApproval.REVISION),
    ).select_related('generated_document__document_type', 'generated_document__created_by', 'approver')
    if not request.user.is_authenticated:
        queryset = queryset.none()
    elif not is_document_manager(request.user):
        queryset = queryset.filter(approver=request.user)
    status = request.GET.get('status', DocumentApproval.PENDING).strip()
    if status:
        queryset = queryset.filter(status=status)
    type_id = request.GET.get('type', '').strip()
    if type_id.isdigit():
        queryset = queryset.filter(generated_document__document_type_id=int(type_id))
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if parse_date(date_from):
        queryset = queryset.filter(requested_at__date__gte=parse_date(date_from))
    if parse_date(date_to):
        queryset = queryset.filter(requested_at__date__lte=parse_date(date_to))
    q = request.GET.get('q', '').strip()[:100]
    if q:
        queryset = queryset.filter(
            Q(generated_document__document_number__icontains=q)
            | Q(generated_document__document_type__name__icontains=q)
            | Q(generated_document__parties__display_name__icontains=q)
        ).distinct()
    queryset, _, _ = apply_queryset_sort(queryset, request, {
        'number': 'generated_document__document_number', 'type': 'generated_document__document_type__name',
        'submitted': 'requested_at', 'level': 'level', 'status': 'status',
    })
    page = qb_paginate(request, queryset)
    today = timezone.localdate()
    for approval in page.object_list:
        approval.days_pending = max(0, (today - approval.requested_at.date()).days)
    return render(request, 'document_generation/pending_approvals.html', {
        'page': page, 'types': DocumentType.objects.filter(is_active=True),
        'statuses': DocumentApproval.STATUSES, 'status': status, 'type_id': type_id,
        'date_from': date_from, 'date_to': date_to, 'q': q,
        'can_manage': is_document_manager(request.user),
    })


@local_only
def approval_workflows(request):
    if not is_document_manager(request.user):
        raise PermissionDenied('Only document management users can configure approval workflows.')
    document_types = DocumentType.objects.all().order_by('name')
    selected_id = request.POST.get('document_type') or request.GET.get('type') or ''
    selected = document_types.filter(pk=selected_id).first() if selected_id.isdigit() else None
    formset = None
    saved = False
    formset_error = ''
    if request.method == 'POST' and selected:
        formset = ApprovalStepFormSet(request.POST, prefix='steps')
        if formset.is_valid():
            steps = [form for form in formset.ordered_forms if not form.cleaned_data.get('DELETE')
                     and form.cleaned_data.get('approver')]
            approval_required = request.POST.get('approval_required') == 'on'
            if approval_required and not steps:
                formset_error = 'Add at least one approval level, or turn off mandatory approval.'
            else:
                selected.approval_required = approval_required
                selected.approval_workflow = [
                    {'user_id': form.cleaned_data['approver'].pk,
                     'user_name': form.cleaned_data['approver'].get_full_name()
                     or form.cleaned_data['approver'].get_username()}
                    for form in steps
                ]
                selected.save(update_fields=('approval_required', 'approval_workflow', 'updated_at'))
                messages.success(request, f'Approval workflow saved for {selected.name}.')
                return redirect(f'{request.path}?type={selected.pk}')
    elif selected:
        initial = [{'approver': item.get('user_id'), 'ORDER': level}
                   for level, item in enumerate(selected.approval_workflow or [], start=1)
                   if isinstance(item, dict)]
        formset = ApprovalStepFormSet(initial=initial, prefix='steps')
    return render(request, 'document_generation/approval_workflows.html', {
        'document_types': document_types, 'selected': selected, 'formset': formset,
        'formset_error': formset_error,
    })


@local_only
def document_detail(request, document_id):
    evaluate_expired_documents()
    document = get_object_or_404(GeneratedDocument.objects.select_related(
        'document_type', 'template_version__template', 'created_by', 'approved_by',
        'issued_by', 'cancelled_by', 'sent_by', 'supersedes', 'file_document',
        'word_file_document', 'pdf_file_document',
    ).prefetch_related('parties', 'approvals__approver', 'activity__actor', 'signatures',
                        'superseding_documents', 'notifications'), pk=document_id)
    if not can_view_document(request.user, document):
        raise PermissionDenied('You are not permitted to view this document.')
    active_approval = document.approvals.filter(status=DocumentApproval.PENDING).order_by('level').first()
    versions = {document.pk: document}
    parent = document.supersedes
    while parent and parent.pk not in versions:
        versions[parent.pk] = parent
        parent = parent.supersedes
    frontier = [document]
    while frontier:
        node = frontier.pop()
        for child in node.superseding_documents.select_related('created_by'):
            if child.pk not in versions:
                versions[child.pk] = child
                frontier.append(child)
    activity = list(document.activity.select_related('actor').order_by('created_at', 'id'))
    action_labels = {
        'generated': 'Document generated', 'revision_created': 'Revision created',
        'amendment_created': 'Amendment created', 'submitted_for_approval': 'Submitted for approval',
        'approval_assigned': 'Approval assigned', 'approved': 'Approved',
        'approved_without_workflow': 'Approved without a workflow', 'rejected': 'Rejected',
        'revision_requested': 'Revision requested', 'issued': 'Issued', 'marked_sent': 'Marked as sent',
        'signed_manually_confirmed': 'Manual signing confirmed', 'superseded': 'Superseded',
        'cancelled': 'Cancelled', 'expired': 'Expired',
    }
    for event in activity:
        event.display_action = action_labels.get(event.action, event.action.replace('_', ' ').title())
    return render(request, 'document_generation/document_lifecycle_detail.html', {
        'document': document,
        'activity': activity,
        'approvals': document.approvals.select_related('approver').order_by('level', 'requested_at'),
        'versions': sorted(versions.values(), key=lambda item: (item.version_number, item.created_at)),
        'active_approval': active_approval,
        'can_manage': is_document_manager(request.user),
        'can_submit': document.status == 'draft' and (
            (request.user.is_authenticated and document.created_by_id == request.user.pk)
            or is_document_manager(request.user)),
        'can_decide': bool(active_approval and (
            active_approval.approver_id == request.user.pk or is_document_manager(request.user))),
        'can_edit_version': document.status in {'draft', 'revision_requested', 'rejected'} and (
            (request.user.is_authenticated and document.created_by_id == request.user.pk)
            or is_document_manager(request.user)),
        'issue_form': IssueDocumentForm(initial={'expiry_date': document.expiry_date}),
        'send_form': SendDocumentForm(initial={'sent_recipient': document.sent_recipient}),
        'signed_form': SignedDocumentForm(), 'cancel_form': CancelDocumentForm(),
        'can_issue': document.status == 'approved' and is_document_manager(request.user),
        'can_send': document.status == 'issued' and is_document_manager(request.user),
        'can_sign': document.status in {'issued', 'sent'} and is_document_manager(request.user),
        'can_cancel': document.status not in {'cancelled', 'superseded', 'expired'}
        and is_document_manager(request.user),
        'can_amend': document.status in {'issued', 'sent', 'signed', 'acknowledged'}
        and ((request.user.is_authenticated and document.created_by_id == request.user.pk)
             or is_document_manager(request.user)),
        'can_share': can_manage_share(request.user, document)
        and document.status in {'issued', 'sent', 'signed', 'acknowledged'},
        'shares': document.shares.prefetch_related('signatories').order_by('-shared_at'),
    })


@local_only
@require_POST
def lifecycle_action(request, document_id, action):
    document = get_object_or_404(GeneratedDocument.objects.select_related('document_type'), pk=document_id)
    if not can_view_document(request.user, document):
        raise PermissionDenied('You are not permitted to access this document.')
    try:
        if action == 'submit':
            submit_document(document, request.user)
        elif action in {'approve', 'reject', 'revise'}:
            approval = get_object_or_404(DocumentApproval, pk=request.POST.get('approval_id'),
                                         generated_document=document)
            decide_approval(approval, request.user, action, request.POST.get('comments', ''))
        elif action == 'issue':
            form = IssueDocumentForm(request.POST)
            if not form.is_valid():
                raise ValidationError(form.errors.as_text())
            issue_document(document, request.user, form.cleaned_data['official_issue_date'],
                           form.cleaned_data['expiry_date'])
        elif action == 'send':
            form = SendDocumentForm(request.POST)
            if not form.is_valid():
                raise ValidationError(form.errors.as_text())
            mark_document_sent(document, request.user, form.cleaned_data['sent_recipient'],
                               form.cleaned_data['delivery_method'], form.cleaned_data['sent_remarks'],
                               form.cleaned_data['sent_date'])
        elif action == 'sign':
            form = SignedDocumentForm(request.POST)
            if not form.is_valid():
                raise ValidationError(form.errors.as_text())
            confirm_document_signed(document, request.user, form.cleaned_data['signer_name'],
                                    form.cleaned_data['comments'])
        elif action == 'cancel':
            form = CancelDocumentForm(request.POST)
            if not form.is_valid():
                raise ValidationError(form.errors.as_text())
            cancel_document(document, request.user, form.cleaned_data['reason'])
        else:
            raise ValidationError('Unsupported lifecycle action.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    else:
        messages.success(request, f'{document.document_number} lifecycle status updated.')
    return redirect('document_generation:document_detail', document_id=document.pk)


@local_only
@require_POST
def notification_read(request, notification_id):
    notification = get_object_or_404(DocumentNotification, pk=notification_id, user=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=('read_at',))
    return redirect('document_generation:document_detail', document_id=notification.generated_document_id)
