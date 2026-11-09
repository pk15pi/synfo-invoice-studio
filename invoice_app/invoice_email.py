"""Email finalized invoice PDFs to customer addresses via Microsoft 365 SMTP.

Reuses employee_management.email_config (same Shared → Settings SMTP + send gap).
Supports To/CC/BCC and per-customer subject/body templates or custom text.
"""
import calendar
import re
from email.utils import parseaddr

from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage, get_connection
from django.core.validators import validate_email
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

PLACEHOLDERS = (
    '{invoice_number}',
    '{customer_name}',
    '{customer_brand}',
    '{invoice_date}',
    '{invoice_month}',
    '{invoice_year}',
    '{grand_total}',
    '{month}',
    '{year}',
    '{employee_name}',
    '{employee_id}',
    '{salary_month}',
    '{salary_year}',
)

_SPLIT_RE = re.compile(r'[,;]+')


def parse_address_list(raw):
    """Split comma/semicolon list into unique validated emails (order preserved)."""
    text = (raw or '').strip()
    if not text:
        return []
    seen, out = set(), []
    for part in _SPLIT_RE.split(text):
        token = part.strip()
        if not token:
            continue
        _name, addr = parseaddr(token)
        addr = (addr or token).strip()
        try:
            validate_email(addr)
        except ValidationError as exc:
            raise ValidationError(f'Invalid email address: {token}') from exc
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            out.append(addr)
    return out


def resolve_recipients(client):
    """Return (to_list, cc_list, bcc_list). To falls back to Client.email."""
    to_list = parse_address_list(getattr(client, 'invoice_email_to', '') or '')
    if not to_list:
        fallback = (getattr(client, 'email', None) or '').strip()
        if fallback:
            try:
                validate_email(fallback)
                to_list = [fallback]
            except ValidationError:
                to_list = []
    cc_list = parse_address_list(getattr(client, 'invoice_email_cc', '') or '')
    bcc_list = parse_address_list(getattr(client, 'invoice_email_bcc', '') or '')
    return to_list, cc_list, bcc_list


def customer_email(client):
    """Primary To address (first of To list / Client.email) — used for quick checks."""
    to_list, _cc, _bcc = resolve_recipients(client)
    return to_list[0] if to_list else ''


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
        .select_related(
            'client',
            'client__invoice_email_subject_template',
            'client__invoice_email_body_template',
        )
        .order_by('invoice_date', 'invoice_number', 'id')
    )
    search = (search or '').strip()
    if search:
        qs = qs.filter(
            Q(invoice_number__icontains=search)
            | Q(client__name__icontains=search)
            | Q(client__brand_name__icontains=search)
            | Q(client__email__icontains=search)
            | Q(client__invoice_email_to__icontains=search)
            | Q(client__invoice_email_cc__icontains=search)
            | Q(client__invoice_email_bcc__icontains=search)
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
        to_list, _cc, _bcc = resolve_recipients(inv.client)
        if inv.last_sent_at:
            skipped_sent.append(inv.pk)
        elif not to_list:
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


def placeholder_values(invoice):
    client = invoice.client
    brand = (getattr(client, 'brand_name', None) or '').strip()
    inv_date = invoice.invoice_date
    month = calendar.month_name[inv_date.month] if inv_date else ''
    year = str(inv_date.year) if inv_date else ''
    return {
        '{invoice_number}': invoice.invoice_number or '',
        '{customer_name}': client.name or '',
        '{customer_brand}': brand,
        '{invoice_date}': inv_date.strftime('%d %b %Y') if inv_date else '',
        '{invoice_month}': month,
        '{invoice_year}': year,
        '{grand_total}': f'{invoice.grand_total:.2f}' if invoice.grand_total is not None else '',
        '{month}': month,
        '{year}': year,
    }


def apply_placeholder_map(text, values):
    result = text or ''
    for key, value in values.items():
        result = result.replace(key, value)
    return result


def apply_placeholders(text, invoice):
    return apply_placeholder_map(text, placeholder_values(invoice))


def resolve_subject(client, invoice):
    if getattr(client, 'invoice_email_use_custom_subject', False):
        custom = (getattr(client, 'invoice_email_custom_subject', '') or '').strip()
        if custom:
            return apply_placeholders(custom, invoice)
    tpl = getattr(client, 'invoice_email_subject_template', None)
    if tpl is not None and (tpl.text or '').strip():
        return apply_placeholders(tpl.text, invoice)
    return f'Invoice — {invoice.invoice_number}'


def resolve_body(client, invoice, company_name='QuantBud'):
    if getattr(client, 'invoice_email_use_custom_body', False):
        custom = (getattr(client, 'invoice_email_custom_body', '') or '').strip()
        if custom:
            return apply_placeholders(custom, invoice)
    tpl = getattr(client, 'invoice_email_body_template', None)
    if tpl is not None and (tpl.text or '').strip():
        return apply_placeholders(tpl.text, invoice)
    number = invoice.invoice_number
    return (
        f'Dear {client.name},\n\n'
        f'Please find attached invoice {number} dated {invoice.invoice_date:%d %b %Y}.\n\n'
        f'This message was sent by {company_name}. '
        f'If you have questions about this invoice, reply to this email.\n\n'
        f'Regards,\n{company_name}\n'
    )


def send_invoice(invoice, *, connection=None):
    """Attach the existing invoice PDF and email it (one message with To/CC/BCC)."""
    client = invoice.client
    number = invoice.invoice_number
    try:
        to_list, cc_list, bcc_list = resolve_recipients(client)
    except ValidationError as exc:
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email='',
            success=False,
            error=str(exc.message if hasattr(exc, 'message') else exc)[:500],
            invoice_number=number,
        )
    to_joined = ', '.join(to_list)
    if not to_list:
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email='',
            success=False,
            error='Customer has no invoice To address or email on file.',
            invoice_number=number,
        )
    cfg = None
    try:
        cfg = EC.load_config()
        mapped = EC.require_ready(cfg)
        pdf_bytes = render_for_invoice(invoice, BusinessProfile.current())
        company = _company_name()
        subject = resolve_subject(client, invoice)
        body = resolve_body(client, invoice, company_name=company)
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
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            connection=conn,
        )
        message.attach(f'{number}.pdf', pdf_bytes, 'application/pdf')
        message.send(fail_silently=False)
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email=to_joined,
            recipient_cc=', '.join(cc_list),
            recipient_bcc=', '.join(bcc_list),
            subject=subject[:200],
            success=True,
            error='',
            invoice_number=number,
        )
    except Exception as exc:  # noqa: BLE001
        return InvoiceEmailLog.objects.create(
            invoice=invoice,
            client=client,
            recipient_email=to_joined,
            recipient_cc=', '.join(cc_list),
            recipient_bcc=', '.join(bcc_list),
            subject='',
            success=False,
            error=EC._clean(exc, cfg)[:500],
            invoice_number=number,
        )


def resolve_page_period(params):
    return report_periods.resolve_standard(params, default='current_fy')
