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
from .receipt_pdf import receipt_number, render_handover_receipt


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
    """Return the signed receipt if uploaded, else a generated handover receipt PDF in Documents."""
    signed = assignment.acknowledgements.select_related('document').filter(
        document__isnull=False,
    ).order_by('-acknowledged_at', '-id').first()
    if signed and signed.document.exists():
        return signed.document

    existing = Document.objects.filter(
        source_type='asset_handover_receipt', source_id=str(assignment.pk),
    ).first()
    if existing and existing.exists():
        return existing

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
    return doc


def compose_message(assignment):
    company = _company_name()
    asset = assignment.asset
    employee = assignment.employee
    accessories = ', '.join(assignment.accessories_handed_over()) or 'None'
    subject = f'Asset handover receipt {receipt_number(assignment)} - {asset.asset_code}'
    body = (
        f'Dear {employee.full_name},\n\n'
        'Please find attached the handover receipt for the asset issued to you.\n\n'
        f'Handover no: {receipt_number(assignment)}\n'
        f'Asset: {asset.asset_code} - {asset.name}\n'
        f'Issued on: {assignment.issue_date:%d-%m-%Y}\n'
        f'Condition: {assignment.get_issue_condition_display()}\n'
        f'Accessories: {accessories}\n\n'
        'If anything is incorrect, reply to this email or contact your IT administrator.\n\n'
        f'Regards,\n{company}\n'
    )
    return subject, body


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
