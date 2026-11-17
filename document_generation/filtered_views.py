"""Filtered list views for the 10 nav sections that previously showed a placeholder page."""

from datetime import date

from django.db.models import Q
from django.shortcuts import render

from employee_management.security import local_only
from invoice_app.pagination import paginate as qb_paginate
from invoice_app.table_sorting import apply_queryset_sort

from .lifecycle_views import _user_document_scope
from .models import (
    DocumentShare,
    DocumentShareSignatory,
    DocumentActivityLog,
    GeneratedDocument,
    DocumentType,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ACTIVE_STATUSES = ('issued', 'sent', 'signed', 'acknowledged')


def _party_role_view(request, role, page_title, eyebrow_label):
    """Generic view for sections 1–6: party-role filtered document lists."""
    documents = (
        _user_document_scope(request)
        .filter(parties__role=role)
        .select_related('document_type', 'created_by')
        .prefetch_related('parties')
        .distinct()
    )

    q = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()
    type_id = request.GET.get('type', '').strip()

    if q:
        documents = documents.filter(
            Q(document_number__icontains=q) | Q(parties__display_name__icontains=q)
        ).distinct()
    if status:
        documents = documents.filter(status=status)
    if type_id.isdigit():
        documents = documents.filter(document_type_id=int(type_id))

    documents, sort_key, direction = apply_queryset_sort(documents, request, {
        'number': 'document_number',
        'type': 'document_type__name',
        'created': 'created_at',
        'status': 'status',
        'issue_date': 'issue_date',
    })

    page = qb_paginate(request, documents)
    return render(request, 'document_generation/party_role_list.html', {
        'page': page,
        'page_title': page_title,
        'eyebrow': eyebrow_label,
        'role': role,
        'q': q,
        'status': status,
        'type_id': type_id,
        'sort_key': sort_key,
        'direction': direction,
        'types': DocumentType.objects.filter(is_active=True),
        'statuses': GeneratedDocument._meta.get_field('status').choices,
    })


# ---------------------------------------------------------------------------
# Sections 1–6: party-role filtered lists
# ---------------------------------------------------------------------------

@local_only
def client_documents(request):
    return _party_role_view(request, 'client', 'Client Documents', 'CLIENT DOCUMENTS')


@local_only
def freelancer_documents(request):
    return _party_role_view(request, 'freelancer', 'Freelancer Documents', 'FREELANCER DOCUMENTS')


@local_only
def candidate_documents(request):
    return _party_role_view(request, 'candidate', 'Candidate Documents', 'CANDIDATE DOCUMENTS')


@local_only
def employee_documents(request):
    return _party_role_view(request, 'employee', 'Employee Documents', 'EMPLOYEE DOCUMENTS')


@local_only
def internal_documents(request):
    return _party_role_view(request, 'internal', 'Internal Documents', 'INTERNAL DOCUMENTS')


@local_only
def vendor_documents(request):
    return _party_role_view(request, 'vendor', 'Vendor Documents', 'VENDOR DOCUMENTS')


# ---------------------------------------------------------------------------
# Section 7: Digital Acknowledgements
# ---------------------------------------------------------------------------

@local_only
def acknowledgements(request):
    shares = (
        DocumentShare.objects.exclude(acknowledgement_status='not_requested')
        .select_related('generated_document__document_type', 'shared_by')
        .order_by('-shared_at')
    )

    q = request.GET.get('q', '').strip()[:100]
    ack_status = request.GET.get('ack_status', '').strip()

    if q:
        shares = shares.filter(
            Q(generated_document__document_number__icontains=q)
            | Q(recipient_name__icontains=q)
            | Q(generated_document__document_type__name__icontains=q)
        )
    if ack_status:
        shares = shares.filter(acknowledgement_status=ack_status)

    shares, sort_key, direction = apply_queryset_sort(shares, request, {
        'number': 'generated_document__document_number',
        'type': 'generated_document__document_type__name',
        'recipient': 'recipient_name',
        'shared': 'shared_at',
        'ack_status': 'acknowledgement_status',
    })

    page = qb_paginate(request, shares)
    return render(request, 'document_generation/acknowledgements_list.html', {
        'page': page,
        'q': q,
        'ack_status': ack_status,
        'sort_key': sort_key,
        'direction': direction,
        'ack_statuses': DocumentShare._meta.get_field('acknowledgement_status').choices,
    })


# ---------------------------------------------------------------------------
# Section 8: Signed Documents
# ---------------------------------------------------------------------------

@local_only
def signed_documents(request):
    signatories = (
        DocumentShareSignatory.objects.select_related(
            'share__generated_document__document_type',
            'share__shared_by',
        )
        .order_by('-share__shared_at', '-id')
    )

    q = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()

    if q:
        signatories = signatories.filter(
            Q(name__icontains=q)
            | Q(share__generated_document__document_number__icontains=q)
            | Q(share__recipient_name__icontains=q)
        )
    if status:
        signatories = signatories.filter(status=status)

    signatories, sort_key, direction = apply_queryset_sort(signatories, request, {
        'name': 'name',
        'number': 'share__generated_document__document_number',
        'recipient': 'share__recipient_name',
        'status': 'status',
        'signed_at': 'signed_at',
    })

    page = qb_paginate(request, signatories)
    return render(request, 'document_generation/signed_list.html', {
        'page': page,
        'q': q,
        'status': status,
        'sort_key': sort_key,
        'direction': direction,
        'statuses': DocumentShareSignatory._meta.get_field('status').choices,
    })


# ---------------------------------------------------------------------------
# Section 9: Expiring Agreements
# ---------------------------------------------------------------------------

@local_only
def expiring_agreements(request):
    today = date.today()

    documents = (
        _user_document_scope(request)
        .filter(expiry_date__isnull=False)
        .filter(
            Q(status__in=_ACTIVE_STATUSES) | Q(status='expired')
        )
        .select_related('document_type', 'created_by')
        .prefetch_related('parties')
        .order_by('expiry_date')
    )

    q = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()

    if q:
        documents = documents.filter(
            Q(document_number__icontains=q) | Q(parties__display_name__icontains=q)
        ).distinct()
    if status:
        documents = documents.filter(status=status)

    documents, sort_key, direction = apply_queryset_sort(documents, request, {
        'number': 'document_number',
        'type': 'document_type__name',
        'expiry_date': 'expiry_date',
        'status': 'status',
    })

    page = qb_paginate(request, documents)

    # Annotate days_until_expiry in Python after pagination to avoid an extra DB pass.
    for doc in page.object_list:
        if doc.expiry_date:
            doc.days_until_expiry = (doc.expiry_date - today).days
        else:
            doc.days_until_expiry = None

    expiring_statuses = [
        ('issued', 'Issued'), ('sent', 'Sent'),
        ('signed', 'Signed'), ('acknowledged', 'Acknowledged'), ('expired', 'Expired'),
    ]
    return render(request, 'document_generation/expiring_list.html', {
        'page': page,
        'today': today,
        'q': q,
        'status': status,
        'sort_key': sort_key,
        'direction': direction,
        'statuses': expiring_statuses,
    })


# ---------------------------------------------------------------------------
# Section 10: Document History (audit log)
# ---------------------------------------------------------------------------

@local_only
def document_history(request):
    activity = (
        DocumentActivityLog.objects
        .select_related('generated_document__document_type', 'actor')
        .order_by('-created_at', '-id')
    )

    q = request.GET.get('q', '').strip()[:100]
    action_filter = request.GET.get('action', '').strip()[:40]

    if q:
        activity = activity.filter(
            Q(generated_document__document_number__icontains=q)
            | Q(generated_document__document_type__name__icontains=q)
            | Q(actor_label__icontains=q)
        )
    if action_filter:
        activity = activity.filter(action__icontains=action_filter)

    activity, sort_key, direction = apply_queryset_sort(activity, request, {
        'number': 'generated_document__document_number',
        'type': 'generated_document__document_type__name',
        'action': 'action',
        'actor': 'actor_label',
        'timestamp': 'created_at',
    })

    page = qb_paginate(request, activity)
    return render(request, 'document_generation/history_list.html', {
        'page': page,
        'q': q,
        'action_filter': action_filter,
        'sort_key': sort_key,
        'direction': direction,
    })
