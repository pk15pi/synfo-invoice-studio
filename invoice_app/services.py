import re
"""Invoice business logic shared by views, tests and the sample command."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import billing, descriptions, gst
from .models import BusinessProfile, Client, Invoice, InvoiceFormatTemplate, InvoiceLine, ProjectTemplate


@dataclass
class LineInput:
    project: ProjectTemplate | None
    project_name: str
    description: str
    days: Decimal
    from_date: date | None
    to_date: date | None
    unit_price: Decimal | None = Decimal('0.00')
    amount: Decimal = Decimal('0.00')
    amount_override: Decimal | None = None
    unit_override: Decimal | None = None
    sac_code: str = ''
    gst_rate: Decimal = Decimal('0.00')

    @property
    def amount_overridden(self):
        return self.amount_override is not None


@dataclass
class InvoiceInput:
    client: object
    invoice_date: date
    terms_days: object
    due_date: object
    pdf_number_override: str = ''
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    terms_mode: str = 'custom'
    terms_text: str = ''
    billing_month: date | None = None
    tax_type: str = 'none'
    tax_rate: Decimal = Decimal('0')
    taxable_override: Decimal | None = None
    cancelled: bool = False
    cancelled_date: date | None = None
    tax_treatment: str = gst.EXPORT
    treatment_posted: bool = False  # False = older form/script: keep the legacy manual tax fields
    place_of_supply: str = ''
    po_number: str = ''
    format_template: object = None   # InvoiceFormatTemplate or None (= default output)

    @property
    def total(self):
        return billing.q2(sum((l.amount for l in self.lines), Decimal('0')))


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _dec(value):
    if value in (None, ''):
        return None
    try:
        return billing.to_decimal(value)
    except (InvalidOperation, ValueError):
        return None


def compute_line(line: LineInput, invoice_date, rounding, billing_month=None):
    """Server-side (authoritative) calculation for one line."""
    if line.project is not None:
        calc = billing.calculate_for_project(line.project, line.days, period_end=line.to_date,
                                             fallback_date=line.from_date or invoice_date, rounding=rounding)
        line.unit_price, line.amount = calc.unit_price, calc.amount
    else:
        line.unit_price = billing.q2(line.unit_override) if line.unit_override is not None else None
        line.amount = billing.round_amount((line.unit_price or 0) * line.days, rounding)
    blank_unit = line.project is not None and line.project.client.billing_type == billing.MONTHLY_FIXED
    if line.unit_override is not None and not blank_unit:
        line.unit_price = billing.q2(line.unit_override)
    if line.amount_override is not None:
        line.amount = billing.q2(line.amount_override)
    if line.project is not None:
        line.sac_code = line.project.sac_code or gst.DEFAULT_SAC
        line.gst_rate = billing.q2(line.project.gst_rate if line.project.gst_rate is not None else gst.DEFAULT_RATE)
    else:  # custom (typed) line: the business default service code and rate
        line.sac_code, line.gst_rate = gst.DEFAULT_SAC, gst.DEFAULT_RATE
    if not line.description and line.project is not None:
        # Project default (template / custom / blank) via the single rendering service.
        line.description = descriptions.project_default_description(
            line.project, start_date=line.from_date, end_date=line.to_date, billing_month=billing_month)
    return line


def default_terms(client, profile):
    """(mode, text) a new invoice for this customer starts with."""
    if client is not None and client.terms_mode == 'month_days':
        return 'month_days', ''
    if client is not None and client.terms_text:
        return 'custom', client.terms_text
    return 'custom', str(profile.payment_terms_days)


def previous_month(d):
    """First day of the calendar month before ``d``."""
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


def parse_month(value):
    """'YYYY-MM' (or an ISO date) -> first day of that month; raises ValueError."""
    v = str(value).strip()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})(?:-\d{1,2})?', v)
    if not m:
        raise ValueError(v)
    return date(int(m.group(1)), int(m.group(2)), 1)


def parse_invoice_post(post, client, profile):
    """Build an InvoiceInput from the invoice form POST (arrays line_*)."""
    errors = []
    invoice_date = _date(post.get('invoice_date')) or date.today()
    mode, text = default_terms(client, profile)
    if post.get('terms_mode') in ('month_days', 'custom'):
        mode = post.get('terms_mode')
    raw = post.get('terms_text')
    if raw is None:
        raw = post.get('terms_days')  # older form field name
    if raw is not None and str(raw).strip() != '':
        text = str(raw).strip()[:40]
    terms_text, terms = billing.resolve_terms(mode, text, invoice_date)
    # Due Date stays editable; auto-filled only when Terms resolves to a whole number.
    due = _date(post.get('due_date')) or (billing.due_date_for(invoice_date, terms) if terms is not None else None)
    data = InvoiceInput(client, invoice_date, terms, due, (post.get('pdf_number_override') or '').strip(),
                        terms_mode=mode, terms_text=terms_text if mode == 'custom' else '')
    # Invoice for (month): missing key (older forms/scripts) -> month before the invoice date; blank -> not set.
    raw_month = post.get('billing_month')
    if 'billing_year' in post or 'billing_mon' in post:  # two-field form: both needed, either blank = not set
        y, m = (post.get('billing_year') or '').strip(), (post.get('billing_mon') or '').strip()
        raw_month = f'{y}-{m}' if (y and m) else ''
    if raw_month is None:
        data.billing_month = previous_month(invoice_date)
    elif str(raw_month).strip():
        try:
            data.billing_month = parse_month(raw_month)
        except ValueError:
            errors.append('Invoice for (month): pick a valid month.')
    get = post.getlist if hasattr(post, 'getlist') else (lambda k: post.get(k, []))
    cols = {k: get(k) for k in ('line_project', 'line_name', 'line_desc', 'line_from', 'line_to',
                                 'line_days', 'line_unit', 'line_amount')}
    n = max((len(v) for v in cols.values()), default=0)
    projects = {str(p.pk): p for p in ProjectTemplate.objects.filter(client=client)} if client else {}
    for i in range(n):
        val = lambda k: (cols[k][i] if i < len(cols[k]) else '').strip()
        project = projects.get(val('line_project'))
        if project is None and val('line_name'):
            # Typed text that exactly matches an active saved project links to it; anything else is custom text.
            typed = val('line_name').casefold()
            project = next((p for p in projects.values() if p.active and p.name.casefold() == typed), None)
        name = val('line_name') or (project.name if project else '')
        days = _dec(val('line_days')) or Decimal('0')
        amount_ov = _dec(val('line_amount'))
        unit_ov = _dec(val('line_unit'))
        if not name and not project:
            continue
        if days <= 0 and amount_ov is None:
            errors.append(f'Row {i + 1} ({name}): enter billable days or an amount.')
            continue
        line = LineInput(project, name, val('line_desc'), days, _date(val('line_from')), _date(val('line_to')),
                         amount_override=amount_ov, unit_override=unit_ov)
        if line.from_date and line.to_date and line.to_date < line.from_date:
            errors.append(f'Row {i + 1} ({name}): "to" date is before "from" date.')
        data.lines.append(compute_line(line, invoice_date, profile.amount_rounding, billing_month=data.billing_month))
    if not data.lines:
        errors.append('Add at least one project row with billable days.')
    # Tax (optional; absent keys = no tax, which keeps older forms/scripts unchanged)
    data.tax_type = post.get('tax_type') if post.get('tax_type') in ('none', 'cgst_sgst', 'igst') else 'none'
    data.tax_rate = _dec(post.get('tax_rate') or '') or Decimal('0')
    if data.tax_rate < 0 or data.tax_rate > 100:
        errors.append('Tax rate must be between 0 and 100.')
    data.taxable_override = _dec(post.get('taxable_value') or '')
    if data.taxable_override is not None and data.taxable_override < 0:
        errors.append('Taxable value cannot be negative.')
    # GST treatment: posted value, else the customer's default (else export = no tax).
    posted = post.get('tax_treatment')
    data.treatment_posted = posted in gst.TREATMENT_KEYS
    data.tax_treatment = posted if data.treatment_posted else (
        getattr(client, 'tax_treatment', '') or gst.EXPORT)
    if data.tax_treatment not in gst.TREATMENT_KEYS:
        data.tax_treatment = gst.EXPORT
    if data.tax_treatment == gst.DOMESTIC_GST:
        pos = (post.get('place_of_supply') or '').strip() or (getattr(client, 'state_code', '') or '')
        if not pos:
            errors.append('Domestic GST: choose the place of supply (or set the customer state / GSTIN).')
        elif pos not in gst.STATES:
            errors.append(f'Place of supply "{pos}" is not a valid GST state code.')
        data.place_of_supply = pos if pos in gst.STATES else ''
    data.cancelled = str(post.get('cancelled', '')).lower() in ('1', 'on', 'true')
    data.cancelled_date = _date(post.get('cancelled_date')) if data.cancelled else None
    if data.cancelled and not data.cancelled_date:
        from django.utils import timezone
        data.cancelled_date = timezone.localdate()
    data.po_number = (post.get('po_number') or '').strip()[:60]
    if len((post.get('po_number') or '').strip()) > 60:
        errors.append('PO number: at most 60 characters.')
    raw_fmt = (post.get('format_template') or '').strip()
    if raw_fmt:
        data.format_template = InvoiceFormatTemplate.objects.filter(pk=raw_fmt if raw_fmt.isdigit() else 0,
                                                                    is_active=True).first()
        if data.format_template is None:
            errors.append('Invoice format: choose an active format.')
    else:
        data.format_template = getattr(client, 'invoice_format', None) if client else None
    data.errors = errors
    return data


def _write_lines(invoice, lines):
    invoice.lines.all().delete()
    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=invoice, project=l.project, position=i, project_name=l.project_name,
                    description=l.description, quantity_days=billing.q2(l.days), from_date=l.from_date,
                    description_template_id=(l.project.description_template_id if l.project is not None and
                                             l.project.invoice_description_type == 'template' else None),
                    to_date=l.to_date, unit_price=l.unit_price, amount=l.amount,
                    amount_overridden=l.amount_overridden, sac_code=l.sac_code, gst_rate=l.gst_rate)
        for i, l in enumerate(lines)])


def _keep_line_snapshots(invoice, lines):
    """A non-draft invoice keeps the SAC/GST rate its lines were saved with (by position), so editing a
    project's rate later can never change a finalized invoice's tax."""
    if not invoice.pk or invoice.status == Invoice.DRAFT:
        return
    old = {l.position: l for l in InvoiceLine.objects.filter(invoice=invoice)}
    for i, l in enumerate(lines):
        if i in old:
            l.sac_code, l.gst_rate = old[i].sac_code, old[i].gst_rate


def save_invoice(data: InvoiceInput, invoice: Invoice | None = None) -> Invoice:
    """Create (consuming exactly ONE sequence number) or update an invoice.

    The number is taken under ``select_for_update`` inside ``transaction.atomic`` so
    concurrent saves cannot reuse or skip numbers. Editing never consumes a number,
    and ``pdf_number_override`` never touches ``invoice_number`` or the sequence."""
    with transaction.atomic():
        if invoice is None:
            BusinessProfile.current()
            profile = BusinessProfile.objects.select_for_update().get(pk=1)
            number = profile.next_number()
            while Invoice.objects.filter(invoice_number=number).exists():  # never collide
                profile.next_invoice_number += 1
                number = profile.next_number()
            profile.next_invoice_number += 1
            profile.save(update_fields=['next_invoice_number'])
            invoice = Invoice(invoice_number=number)
        invoice.client = data.client
        invoice.invoice_date = data.invoice_date
        invoice.terms_days = data.terms_days
        invoice.terms_mode = data.terms_mode
        invoice.terms_text = data.terms_text
        invoice.due_date = data.due_date
        invoice.pdf_number_override = data.pdf_number_override
        invoice.billing_month = data.billing_month
        invoice.total = data.total
        _keep_line_snapshots(invoice, data.lines)
        apply_format_input(invoice, data)
        apply_tax_input(invoice, data)
        invoice.save()
        _write_lines(invoice, data.lines)
        if data.pdf_number_override:
            Client.objects.filter(pk=data.client.pk).update(last_pdf_ref_no=data.pdf_number_override)
    return invoice


def apply_format_input(invoice, data):
    """PO number + PDF format. A draft/new invoice takes the chosen format and a fresh snapshot of its
    settings; a finalized or cancelled invoice keeps the format snapshot it was saved with."""
    if invoice.pk and invoice.status != Invoice.DRAFT:
        return
    invoice.po_number = data.po_number
    invoice.format_template = data.format_template
    from .pdf.formats import snapshot_for
    invoice.format_snapshot = snapshot_for(data.format_template, data.client)


def apply_tax_input(invoice, data):
    """Fill the invoice's tax fields (the ledger's source of truth) from the input.

    * Domestic GST: computed per line by gst.apply_to_invoice (CGST+SGST or IGST from place of supply).
    * Export / Export under LUT: no tax (taxable = line total or the explicit taxable override).
    * Export keeps the legacy manual tax_type/tax_rate inputs (absent = no tax), so older scripts and
      invoices saved with manual tax behave exactly as before."""
    profile = BusinessProfile.current()
    if invoice.pk and invoice.status != Invoice.DRAFT:
        # Finalized/cancelled: GST is never recomputed; stored tax figures stay exactly as posted.
        invoice.cancelled, invoice.cancelled_date = data.cancelled, data.cancelled_date
        return
    invoice.tax_treatment = data.tax_treatment
    invoice.place_of_supply = data.place_of_supply if data.tax_treatment == gst.DOMESTIC_GST else ''
    invoice.cancelled, invoice.cancelled_date = data.cancelled, data.cancelled_date
    if data.tax_treatment == gst.DOMESTIC_GST:
        gst.apply_to_invoice(invoice, [(l.amount, l.gst_rate) for l in data.lines], profile)
        return
    invoice.taxable_value = data.taxable_override if data.taxable_override is not None else data.total
    if data.tax_treatment == gst.EXPORT_LUT:
        invoice.tax_type, invoice.tax_rate = Invoice.TAX_NONE, Decimal('0')
    else:
        invoice.tax_type, invoice.tax_rate = data.tax_type, data.tax_rate
    invoice.apply_tax(profile.amount_rounding)


def unsaved_invoice(data: InvoiceInput, number: str):
    """In-memory Invoice + lines for PDF preview (no DB writes, no number consumed)."""
    inv = Invoice(client=data.client, invoice_number=number, pdf_number_override=data.pdf_number_override,
                  invoice_date=data.invoice_date, terms_days=data.terms_days, due_date=data.due_date,
                  terms_mode=data.terms_mode, terms_text=data.terms_text, billing_month=data.billing_month,
                  total=data.total)
    apply_format_input(inv, data)
    apply_tax_input(inv, data)
    lines = [InvoiceLine(project_name=l.project_name, description=l.description, quantity_days=billing.q2(l.days),
                         from_date=l.from_date, to_date=l.to_date, unit_price=l.unit_price, amount=l.amount,
                         sac_code=l.sac_code, gst_rate=l.gst_rate)
             for l in data.lines]
    return inv, lines


def column_overflow_warnings(lines):
    """Warn when a value is wider than its PDF cell (it would get an ellipsis)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .pdf import layout as L
    from .pdf.fonts import font
    t = L.TABLE
    edges = [t['left']] + t['separators'] + [t['right']]
    f, s = t['body_font']
    out = []
    for l in lines:
        for text, i in ((l.project_name, 0), (l.description, 1)):
            if stringWidth(text or '', font(f), s) > edges[i + 1] - edges[i] - 2 * t['text_pad_left']:
                out.append(f'"{text}" is too long for the PDF column and will be shortened.')
    return out
