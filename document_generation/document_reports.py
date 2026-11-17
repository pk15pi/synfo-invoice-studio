"""Document automation, compliance, lifecycle and notification reports."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import HttpResponse, Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from employee_management.security import local_only
from invoice_app.pagination import paginate as qb_paginate
from .automation_views import _require_oversight
from .lifecycle_services import can_view_all_documents
from .models import (
    DocumentApproval, DocumentAutomationExecution, DocumentComplianceReview,
    DocumentCorrectiveAction, DocumentNotification, DocumentRenewal, DocumentShare,
    DocumentShareSignatory, GeneratedDocument, DocumentType,
)

REPORTS = [
    ('pending_approvals', 'Pending Approvals'), ('overdue_approvals', 'Overdue Approvals'),
    ('expiring', 'Documents Expiring Soon'), ('expired', 'Expired Documents'),
    ('renewals', 'Renewals Due'), ('signatures', 'Pending Signatures'),
    ('acknowledgements', 'Pending Acknowledgements'), ('compliance', 'Compliance Status'),
    ('non_compliant', 'Non-Compliant Records'), ('corrective_actions', 'Corrective Actions'),
    ('automation', 'Automation Execution History'), ('notifications', 'Notification History'),
]


def _visible_documents(user):
    docs = GeneratedDocument.objects.all()
    if can_view_all_documents(user):
        return docs
    if not user.is_authenticated:
        return docs.none()
    return docs.filter(Q(created_by=user) | Q(approvals__approver=user)).distinct()


def _data(kind, user, params):
    today = timezone.localdate()
    docs = _visible_documents(user)
    headers, rows, date_index = [], [], None
    if kind in {'pending_approvals', 'overdue_approvals'}:
        queryset = DocumentApproval.objects.filter(generated_document__in=docs, status='pending',
                                                    generated_document__status='pending_approval').select_related(
            'generated_document__document_type', 'approver')
        if not getattr(user, 'is_authenticated', False):
            queryset = queryset.none()
        elif not can_view_all_documents(user):
            queryset = queryset.filter(approver=user)
        if kind == 'overdue_approvals':
            queryset = queryset.filter(requested_at__date__lt=today)
        headers = ['Document Number', 'Document Type', 'Approver', 'Level', 'Submitted', 'Days Pending']
        date_index = 4
        rows = [[a.generated_document.document_number, a.generated_document.document_type.name,
                 a.approver_name, a.level, a.requested_at.strftime('%Y-%m-%d'),
                 (today - a.requested_at.date()).days] for a in queryset]
    elif kind in {'expiring', 'expired'}:
        queryset = docs.filter(status='expired') if kind == 'expired' else docs.filter(
            status__in=('issued', 'sent', 'signed', 'acknowledged'),
            expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90))
        queryset = queryset.select_related('document_type', 'created_by')
        headers = ['Document Number', 'Document Type', 'Recipient', 'Status', 'Expiry Date', 'Responsible']
        date_index = 4
        rows = [[d.document_number, d.document_type.name,
                 ', '.join(d.parties.values_list('display_name', flat=True)), d.get_status_display(),
                 d.expiry_date.isoformat() if d.expiry_date else '',
                 d.created_by.get_full_name() or d.created_by.username if d.created_by else ''] for d in queryset]
    elif kind == 'renewals':
        queryset = DocumentRenewal.objects.filter(generated_document__in=docs,
                                                  status__in=('due', 'overdue', 'initiated', 'in_progress'))
        queryset = queryset.select_related('generated_document__document_type', 'initiated_by')
        headers = ['Document Number', 'Document Type', 'Expiry Date', 'Renewal Status', 'Lead Days', 'Initiated By']
        date_index = 2
        rows = [[r.generated_document.document_number, r.generated_document.document_type.name,
                 r.generated_document.expiry_date.isoformat() if r.generated_document.expiry_date else '',
                 r.get_status_display(), r.lead_time_days,
                 r.initiated_by.get_full_name() or r.initiated_by.username if r.initiated_by else ''] for r in queryset]
    elif kind in {'signatures', 'acknowledgements'}:
        queryset = DocumentShare.objects.filter(generated_document__in=docs, revoked_at__isnull=True).select_related(
            'generated_document__document_type').prefetch_related('signatories')
        headers = ['Document Number', 'Document Type', 'Recipient', 'Version', 'Shared Date', 'Status']
        date_index = 4
        for share in queryset:
            if kind == 'acknowledgements' and share.acknowledgement_status != 'pending':
                continue
            if kind == 'signatures' and (not share.signature_required or share.signature_status not in {'pending', 'partially_signed'}):
                continue
            rows.append([share.generated_document.document_number, share.generated_document.document_type.name,
                         share.recipient_name, share.generated_document.version_number,
                         share.shared_at.strftime('%Y-%m-%d'),
                         share.get_acknowledgement_status_display() if kind == 'acknowledgements' else share.signature_status])
    elif kind in {'compliance', 'non_compliant'}:
        _require_oversight(user)
        queryset = DocumentComplianceReview.objects.select_related('requirement__document_type',
                                                                    'generated_document', 'party', 'reviewed_by')
        if kind == 'non_compliant':
            queryset = queryset.filter(status='non_compliant')
        headers = ['Checked', 'Requirement', 'Document Number', 'Recipient', 'Status', 'Reason', 'Responsible']
        date_index = 0
        rows = [[r.checked_at.strftime('%Y-%m-%d %H:%M'), r.requirement.name,
                 r.generated_document.document_number if r.generated_document else 'Missing',
                 r.party.display_name if r.party else '', r.get_status_display(), r.reason,
                 r.requirement.responsible_person.get_full_name() if r.requirement.responsible_person else '']
                for r in queryset]
    elif kind == 'corrective_actions':
        _require_oversight(user)
        queryset = DocumentCorrectiveAction.objects.select_related('requirement', 'generated_document',
                                                                    'responsible_person')
        headers = ['Requirement', 'Document Number', 'Problem', 'Responsible', 'Target Date', 'Status', 'Resolution']
        date_index = 4
        rows = [[a.requirement.name, a.generated_document.document_number if a.generated_document else '',
                 a.problem, a.responsible_person.get_full_name() if a.responsible_person else '',
                 a.target_date.isoformat(), a.get_status_display(), a.resolution_notes] for a in queryset]
    elif kind == 'automation':
        _require_oversight(user)
        queryset = DocumentAutomationExecution.objects.select_related('rule', 'generated_document')
        headers = ['Started', 'Automation', 'Document', 'Status', 'Results', 'Failure']
        date_index = 0
        rows = [[e.started_at.strftime('%Y-%m-%d %H:%M'), e.rule.name,
                 e.generated_document.document_number if e.generated_document else 'Batch',
                 e.get_status_display(), e.result_count, e.error] for e in queryset]
    elif kind == 'notifications':
        queryset = DocumentNotification.objects.select_related('user', 'generated_document')
        if not can_view_all_documents(user):
            queryset = queryset.filter(user=user) if user.is_authenticated else queryset.none()
        headers = ['Created', 'Notification', 'Document', 'Recipient', 'Channel', 'Status', 'Read']
        date_index = 0
        rows = [[n.created_at.strftime('%Y-%m-%d %H:%M'), n.subject or n.action,
                 n.generated_document.document_number, n.user.get_full_name() or n.user.username,
                 n.delivery_method, n.get_delivery_status_display(), bool(n.read_at)] for n in queryset]
    else:
        raise Http404
    date_from, date_to = params.get('date_from', ''), params.get('date_to', '')
    type_id, status, q = params.get('type', ''), params.get('status', ''), params.get('q', '').strip().lower()
    recipient = params.get('recipient', '').strip().lower()
    filtered = []
    for row in rows:
        if date_from and date_index is not None:
            compare = str(row[date_index])
            if compare[:10] < date_from:
                continue
        if date_to and date_index is not None:
            compare = str(row[date_index])
            if compare[:10] > date_to:
                continue
        if q and not any(q in str(cell).lower() for cell in row):
            continue
        recipient_index = next((headers.index(name) for name in ('Recipient', 'Approver', 'Responsible')
                                if name in headers), None)
        if recipient and (recipient_index is None or recipient not in str(row[recipient_index]).lower()):
            continue
        if status and not any(status.lower() in str(cell).lower() for cell in row):
            continue
        if type_id:
            try:
                type_name = DocumentType.objects.get(pk=type_id).name
            except (DocumentType.DoesNotExist, ValueError):
                continue
            if type_name not in [str(cell) for cell in row]:
                continue
        filtered.append(row)
    return headers, filtered


def _export_excel(headers, rows, filename):
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Document Report'
    sheet.append(headers)
    for row in rows:
        sheet.append([str(value) if value is not None else '' for value in row])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        width = min(48, max(14, max(len(str(cell.value or '')) for cell in column) + 2))
        sheet.column_dimensions[letter].width = width
    import io
    output = io.BytesIO()
    workbook.save(output)
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
    return response


def _export_pdf(headers, rows, filename):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    import io
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    data = [[Paragraph(str(cell), styles['BodyText']) for cell in headers]]
    for row in rows:
        data.append([Paragraph(str(value if value is not None else ''), styles['BodyText']) for value in row])
    widths = [((landscape(A4)[0] - 24*mm) / max(len(headers), 1))] * len(headers)
    table = Table(data, repeatRows=1, colWidths=widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#17324d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white), ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#ccd5de')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f5f8fa')]),
    ]))
    doc.build([table])
    response = HttpResponse(output.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
    return response


@local_only
@require_GET
def document_reports(request):
    kind = request.GET.get('report', 'pending_approvals')
    labels = dict(REPORTS)
    if kind not in labels:
        raise Http404
    headers, rows = _data(kind, request.user, request.GET)
    export_format = request.GET.get('download')
    if export_format == 'excel':
        return _export_excel(headers, rows, 'document-' + kind)
    if export_format == 'pdf':
        return _export_pdf(headers, rows, 'document-' + kind)
    page = qb_paginate(request, rows)
    return render(request, 'document_generation/document_reports.html', {
        'reports': [item for item in REPORTS if can_view_all_documents(request.user)
                    or item[0] not in {'compliance', 'non_compliant', 'corrective_actions', 'automation'}],
        'report': kind, 'report_label': labels[kind], 'headers': headers,
        'page': page, 'total': len(rows), 'types': DocumentType.objects.order_by('name'),
        'q': request.GET.get('q', ''), 'type_id': request.GET.get('type', ''),
        'status': request.GET.get('status', ''), 'date_from': request.GET.get('date_from', ''),
        'date_to': request.GET.get('date_to', ''), 'recipient': request.GET.get('recipient', ''),
    })
