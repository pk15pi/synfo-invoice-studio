"""Email the asset handover receipt PDF from the private document index."""
import hashlib
import os
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

from document_management.models import Document, full_path, safe_rel_path
from employee_management import email_config as EC
from employee_management.storage import private_storage

from .models import AssetAssignment
from .receipt_pdf import issued_items, manual_accessories, receipt_number, render_handover_receipt


def employee_email(employee):
    return (employee.work_email or employee.personal_email or '').strip()


def _company_name():
    try:
        from invoice_app.models import BusinessProfile
        profile = BusinessProfile.objects.filter(pk=1).first()
        if profile:
            return (profile.brand_name or profile.name or '').strip() or 'Company'
    except Exception:
        pass
    return 'Company'


def _document_defaults(assignment, rel, size, sha):
    asset = assignment.asset
    return {
        'category': 'asset',
        'title': f'Handover receipt \u2013 {asset.asset_code} \u2013 {assignment.employee.full_name}'[:200],
        'content_type': ContentType.objects.get_for_model(AssetAssignment),
        'object_id': str(assignment.pk),
        'file_path': rel,
        'original_filename': f'handover-{receipt_number(assignment)}.pdf'[:255],
        'mime_type': 'application/pdf',
        'sensitivity': Document.NORMAL,
        'tags': f'asset,receipt,handover,{asset.asset_code}'[:240],
        'uploaded_at': timezone.now(),
        'size': size,
        'sha256': sha,
        'missing': False,
        'check_note': '',
        'last_checked': timezone.now(),
    }


def ensure_receipt_document(assignment):
    """Return the signed receipt if uploaded, else a freshly generated handover receipt PDF."""
    signed = assignment.acknowledgements.select_related('document').filter(
        document__isnull=False,
    ).order_by('-acknowledged_at', '-id').first()
    if signed and signed.document.exists():
        return signed.document

    previous = Document.objects.filter(
        source_type='asset_handover_receipt', source_id=str(assignment.pk),
    ).first()
    pdf_bytes = render_handover_receipt(assignment)
    rel = safe_rel_path(private_storage.save(
        f'asset_receipts/{uuid.uuid4().hex}.pdf', ContentFile(pdf_bytes),
    ))
    doc, _ = Document.objects.update_or_create(
        source_type='asset_handover_receipt', source_id=str(assignment.pk),
        defaults=_document_defaults(
            assignment, rel, len(pdf_bytes), hashlib.sha256(pdf_bytes).hexdigest(),
        ),
    )
    if previous and previous.file_path and previous.file_path != rel:
        try:
            private_storage.delete(previous.file_path)
        except Exception:
            pass
    return doc


def _item_lines(items):
    lines = []
    for number, (asset, quantity) in enumerate(items, 1):
        serial = f' (Serial: {asset.serial_number.strip()})' if (asset.serial_number or '').strip() else ''
        lines.append(f'{number}. {asset.asset_code} - {asset.name}{serial} | Qty: {quantity}')
    return lines


def compose_message(assignment):
    company = _company_name()
    asset = assignment.asset
    employee = assignment.employee
    items = issued_items(assignment)
    accessories = manual_accessories(assignment)
    subject = f'Asset handover receipt {receipt_number(assignment)} - {asset.asset_code}'
    lines = [
        f'Dear {employee.full_name},',
        '',
        'Please find attached the handover receipt for the items issued to you.',
        '',
        f'Handover no: {receipt_number(assignment)}',
        f'Issued on: {assignment.issue_date:%d-%m-%Y}',
        f'Condition: {assignment.get_issue_condition_display()}',
        '',
        'Items issued:',
        *_item_lines(items),
    ]
    if accessories:
        lines += ['', 'Other accessories: ' + ', '.join(accessories)]
    if assignment.expected_return_date:
        lines += ['', f'Expected return date: {assignment.expected_return_date:%d-%m-%Y}']
    lines += [
        '',
        f'Total items received: {len(items) + len(accessories)}',
        '',
        'If anything is incorrect, reply to this email or contact your IT administrator.',
        '',
        'Regards,',
        company,
        '',
    ]
    return subject, '\n'.join(lines)


def _record(assignment, recipient, *, error='', sent=False):
    assignment.receipt_email_to = recipient
    assignment.receipt_email_error = error[:500]
    if sent:
        assignment.receipt_emailed_at = timezone.now()
    assignment.save(update_fields=[
        'receipt_email_to', 'receipt_email_error', 'receipt_emailed_at', 'updated_at',
    ])


def send_handover_receipt(assignment, *, connection=None):
    """Send the receipt PDF. Never raises; failures are stored on the assignment."""
    recipient = employee_email(assignment.employee)
    if not recipient:
        message = 'This employee has no work or personal email address on file.'
        _record(assignment, '', error=message)
        return False, message
    cfg = None
    try:
        cfg = EC.load_config()
        mapped = EC.require_ready(cfg)
        doc = ensure_receipt_document(assignment)
        with open(full_path(doc.file_path), 'rb') as fh:
            payload = fh.read()
        filename = doc.original_filename or os.path.basename(doc.file_path)
        connection = connection or get_connection(
            host=mapped['HOST'], port=mapped['PORT'], username=mapped['HOST_USER'],
            password=mapped['HOST_PASSWORD'], use_tls=mapped['USE_TLS'], use_ssl=mapped['USE_SSL'],
            fail_silently=False,
        )
        subject, body = compose_message(assignment)
        message = EmailMessage(
            subject=subject, body=body, from_email=mapped['FROM'], to=[recipient],
            connection=connection,
        )
        message.attach(filename, payload, doc.mime_type or 'application/pdf')
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - always record the outcome
        error = EC._clean(exc, cfg)
        _record(assignment, recipient, error=error)
        return False, error
    _record(assignment, recipient, sent=True)
    return True, ''
