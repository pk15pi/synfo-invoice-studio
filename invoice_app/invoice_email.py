"""Email finalized invoice PDFs to customer email addresses via Microsoft 365 SMTP.

Reuses employee_management.email_config (same Shared → Settings SMTP + send gap).
"""
from django.core.mail import EmailMessage, get_connection
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from reporting import periods as report_periods

from employee_management import email_config as EC

from .models import BusinessProfile, Invoice, InvoiceEmailLog
from .pdf.formats import render_for_invoice

PERIOD_PARAM_KEYS = (
    'period_mode', 'calendar_year', 'calendar_month', 'fy', 'period_type',
    'period_value', 'date_from', 'date_to',
)


def customer_email(client):
    return (getattr(client, 'email', None) or '').strip()


def has_period_params(params):
    return any((params.get(key) or '').strip() for key in PERIOD_PARAM_KEYS)


def previous_month_params(ref=None):
    today = ref or timezone.localdate()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1
    return {
        'period_mode': 'calendar',
        'calendar_year': str(year),
        'calendar_month': str(month),
    }


def invoices_for_period(period, search=''):
    """Finalized, non-cancelled invoices whose invoice_date falls in ``period``."""
    qs = (
        Invoice.objects.filter(
            status=Invoice.FINALIZED,
            cancelled=False,
            invoice_date__gte=period.start,
            invoice_date__lte=period.end,
        )
        .select_related('client')
        .order_by('invoice_date', 'invoice_number', 'id')
    )
    search = (search or '').strip()
    if search:
        qs = qs.filter(
            Q(invoice_number__icontains=search)
            | Q(client__name__icontains=search)
            | Q(client__brand_name__icontains=search)
            | Q(client__email__icontains=search)
        )
    latest_ok = (
        InvoiceEmailLog.objects.filter(invoice_id=OuterRef('pk'), success=True)
        .order_by('-sent_at')
        .values('sent_at')[:1]
    )
    any_attempt = InvoiceEmailLog.objects.filter(invoice_id=OuterRef('pk'))
    return qs.annotate(
        last_sent_at=Subquery(latest_ok),
        email_attempted=Exists(any_attempt),
    )


def bulk_queue(invoices):
    pending, skipped_sent, skipped_email = [], [], []
    for inv in invoices:
        email = customer_email(inv.client)
        if inv.last_sent_at:
            skipped_sent.append(inv.pk)
        elif not email:
            skipped_email.append(inv.pk)
        else:
            pending.append(inv.pk)
    return {
        'pending_ids': pending,
        'skipped_already_sent': len(skipped_sent),
        'skipped_no_email': len(skipped_email),
        'total': len(pending) + len(skipped_sent) + len(skipped_email),
    }


def _company_name():
    try:
        profile = BusinessProfile.objects.filter(pk=1).first()
        if profile:
            return (profile.brand_name or profile.name or '').strip() or 'QuantBud'
    except Exception:
        pass
    return 'QuantBud'


def send_invoice(invoice, *, connection=None):
    """Attach the existing invoice PDF and email it. Always writes an InvoiceEmailLog row."""
    client = invoice.client
    recipient = customer_email(client)
    number = invoice.invoice_number
    if not recipient:
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email='',
            success=False,
            error='Customer has no email address on file.',
            invoice_number=number,
        )
    cfg = None
    try:
        cfg = EC.load_config()
        mapped = EC.require_ready(cfg)
        pdf_bytes = render_for_invoice(invoice, BusinessProfile.current())
        company = _company_name()
        subject = f'Invoice — {number}'
        body = (
            f'Dear {client.name},\n\n'
            f'Please find attached invoice {number} dated {invoice.invoice_date:%d %b %Y}.\n\n'
            f'This message was sent by {company}. '
            f'If you have questions about this invoice, reply to this email.\n\n'
            f'Regards,\n{company}\n'
        )
        conn = connection or get_connection(
            host=mapped['HOST'],
            port=mapped['PORT'],
            username=mapped['HOST_USER'],
            password=mapped['HOST_PASSWORD'],
            use_tls=mapped['USE_TLS'],
            use_ssl=mapped['USE_SSL'],
            fail_silently=False,
        )
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=mapped['FROM'],
            to=[recipient],
            connection=conn,
        )
        message.attach(f'{number}.pdf', pdf_bytes, 'application/pdf')
        message.send(fail_silently=False)
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email=recipient,
            success=True,
            error='',
            invoice_number=number,
        )
    except Exception as exc:  # noqa: BLE001
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email=recipient,
            success=False,
            error=EC._clean(exc, cfg)[:500],
            invoice_number=number,
        )


def resolve_page_period(params):
    return report_periods.resolve_standard(params, default='current_fy')
