"""Invoice business logic shared by views, tests and the sample command."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import billing
from .models import BusinessProfile, Invoice, InvoiceLine, ProjectTemplate


@dataclass
class LineInput:
    project: ProjectTemplate | None
    project_name: str
    description: str
    days: Decimal
    from_date: date | None
    to_date: date | None
    unit_price: Decimal = Decimal('0.00')
    amount: Decimal = Decimal('0.00')
    amount_override: Decimal | None = None
    unit_override: Decimal | None = None

    @property
    def amount_overridden(self):
        return self.amount_override is not None


@dataclass
class InvoiceInput:
    client: object
    invoice_date: date
    terms_days: int
    due_date: date
    pdf_number_override: str = ''
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)

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


def compute_line(line: LineInput, invoice_date, rounding):
    """Server-side (authoritative) calculation for one line."""
    if line.project is not None:
        calc = billing.calculate_for_project(line.project, line.days, period_end=line.to_date,
                                             fallback_date=line.from_date or invoice_date, rounding=rounding)
        line.unit_price, line.amount = calc.unit_price, calc.amount
    else:
        line.unit_price = billing.q2(line.unit_override or 0)
        line.amount = billing.round_amount(line.unit_price * line.days, rounding)
    if line.unit_override is not None:
        line.unit_price = billing.q2(line.unit_override)
    if line.amount_override is not None:
        line.amount = billing.q2(line.amount_override)
    if not line.description and line.project is not None:
        line.description = billing.build_description(line.project.description, line.from_date, line.to_date)
    return line


def parse_invoice_post(post, client, profile):
    """Build an InvoiceInput from the invoice form POST (arrays line_*)."""
    errors = []
    invoice_date = _date(post.get('invoice_date')) or date.today()
    try:
        terms = int(post.get('terms_days') or (client.default_terms_days if client and client.default_terms_days is not None else profile.payment_terms_days))
    except ValueError:
        terms = profile.payment_terms_days
        errors.append('Terms must be a whole number of days.')
    due = _date(post.get('due_date')) or billing.due_date_for(invoice_date, terms)
    data = InvoiceInput(client, invoice_date, terms, due, (post.get('pdf_number_override') or '').strip())
    get = post.getlist if hasattr(post, 'getlist') else (lambda k: post.get(k, []))
    cols = {k: get(k) for k in ('line_project', 'line_name', 'line_desc', 'line_from', 'line_to',
                                 'line_days', 'line_unit', 'line_amount')}
    n = max((len(v) for v in cols.values()), default=0)
    projects = {str(p.pk): p for p in ProjectTemplate.objects.filter(client=client)} if client else {}
    for i in range(n):
        val = lambda k: (cols[k][i] if i < len(cols[k]) else '').strip()
        project = projects.get(val('line_project'))
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
        data.lines.append(compute_line(line, invoice_date, profile.amount_rounding))
    if not data.lines:
        errors.append('Add at least one project row with billable days.')
    data.errors = errors
    return data


def _write_lines(invoice, lines):
    invoice.lines.all().delete()
    InvoiceLine.objects.bulk_create([
        InvoiceLine(invoice=invoice, project=l.project, position=i, project_name=l.project_name,
                    description=l.description, quantity_days=billing.q2(l.days), from_date=l.from_date,
                    to_date=l.to_date, unit_price=l.unit_price, amount=l.amount,
                    amount_overridden=l.amount_overridden)
        for i, l in enumerate(lines)])


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
        invoice.due_date = data.due_date
        invoice.pdf_number_override = data.pdf_number_override
        invoice.total = data.total
        invoice.save()
        _write_lines(invoice, data.lines)
    return invoice


def unsaved_invoice(data: InvoiceInput, number: str):
    """In-memory Invoice + lines for PDF preview (no DB writes, no number consumed)."""
    inv = Invoice(client=data.client, invoice_number=number, pdf_number_override=data.pdf_number_override,
                  invoice_date=data.invoice_date, terms_days=data.terms_days, due_date=data.due_date,
                  total=data.total)
    lines = [InvoiceLine(project_name=l.project_name, description=l.description, quantity_days=billing.q2(l.days),
                         from_date=l.from_date, to_date=l.to_date, unit_price=l.unit_price, amount=l.amount)
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
