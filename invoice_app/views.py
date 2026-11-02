from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import calendar
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from .forms import BusinessProfileForm, ClientForm, ProjectTemplateForm
from .models import BusinessProfile, Client, ProjectTemplate, Invoice, InvoiceLine

CENT = Decimal('0.01')
def money(value): return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)

def dashboard(request):
    return render(request, 'invoice_app/dashboard.html', {'profile': BusinessProfile.current(), 'clients': Client.objects.count(), 'invoices': Invoice.objects.order_by('-created_at')[:8]})

def profile_edit(request):
    form = BusinessProfileForm(request.POST or None, instance=BusinessProfile.current())
    if request.method == 'POST' and form.is_valid():
        form.save(); messages.success(request, 'Business details and defaults saved.'); return redirect('dashboard')
    return render(request, 'invoice_app/form_page.html', {'form': form, 'heading': 'Your business details', 'back': 'dashboard'})

def clients(request): return render(request, 'invoice_app/clients.html', {'clients': Client.objects.all().order_by('name')})
def client_edit(request, pk=None):
    obj = get_object_or_404(Client, pk=pk) if pk else None
    form = ClientForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid(): form.save(); messages.success(request, 'Customer details saved.'); return redirect('clients')
    return render(request, 'invoice_app/form_page.html', {'form': form, 'heading': 'Edit customer' if obj else 'Add customer', 'back': 'clients'})
@require_POST
def client_delete(request, pk): get_object_or_404(Client, pk=pk).delete(); messages.success(request, 'Customer deleted.'); return redirect('clients')
def projects(request, client_id):
    client = get_object_or_404(Client, pk=client_id)
    return render(request, 'invoice_app/projects.html', {'client': client, 'projects': client.projects.all()})
def project_edit(request, client_id, pk=None):
    client = get_object_or_404(Client, pk=client_id); obj = get_object_or_404(ProjectTemplate, pk=pk, client=client) if pk else None
    form = ProjectTemplateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        project = form.save(commit=False); project.client = client; project.save(); messages.success(request, 'Project defaults saved.'); return redirect('projects', client_id=client.pk)
    return render(request, 'invoice_app/form_page.html', {'form': form, 'heading': 'Edit project' if obj else 'Add project', 'back': 'projects', 'back_args': [client.pk]})
@require_POST
def project_delete(request, client_id, pk):
    get_object_or_404(ProjectTemplate, pk=pk, client_id=client_id).delete(); return redirect('projects', client_id=client_id)

def new_invoice(request):
    profile = BusinessProfile.current(); client_list = Client.objects.prefetch_related('projects').order_by('name')
    if request.method == 'POST':
        client = get_object_or_404(Client, pk=request.POST.get('client'))
        invoice_date = timezone.datetime.fromisoformat(request.POST.get('invoice_date')).date()
        terms = int(request.POST.get('terms_days') or client.default_terms_days or profile.payment_terms_days)
        due_date = invoice_date + timedelta(days=terms)
        lines = []; total = Decimal('0.00')
        for key in request.POST:
            if not key.startswith('line_') or not key.endswith('_days'): continue
            pid = key[5:-5]
            try: project = ProjectTemplate.objects.get(pk=int(pid), client=client)
            except (ValueError, ProjectTemplate.DoesNotExist): continue
            days = Decimal(request.POST.get(key) or '0')
            if days <= 0: continue
            start = request.POST.get(f'line_{pid}_from') or None; end = request.POST.get(f'line_{pid}_to') or None
            if project.pricing_type == ProjectTemplate.VARIABLE:
                unit = money(project.daily_rate); amount = money(unit * days)
            else:
                divisor = Decimal('30') if project.proration_basis == ProjectTemplate.THIRTY else Decimal(str(calendar.monthrange(invoice_date.year, invoice_date.month)[1]))
                unit = money(project.monthly_amount / divisor)
                amount = money(project.monthly_amount if project.fixed_monthly_even_if_partial else unit * days)
            lines.append((project, days, start, end, unit, amount)); total += amount
        if not lines:
            messages.error(request, 'Add at least one project line with billable days.')
        else:
            with transaction.atomic():
                profile = BusinessProfile.objects.select_for_update().get(pk=1)
                automatic_number = profile.next_number(); profile.next_invoice_number += 1
                profile.save(update_fields=['next_invoice_number'])
                invoice = Invoice.objects.create(client=client, invoice_number=automatic_number, invoice_date=invoice_date, terms_days=terms, due_date=due_date, total=money(total))
                for project, days, start, end, unit, amount in lines:
                    InvoiceLine.objects.create(invoice=invoice, project_name=project.name, description=project.description, quantity_days=days, from_date=start, to_date=end, unit_price=unit, amount=amount)
            override = request.POST.get('manual_invoice_number', '').strip()
            return redirect('invoice_pdf_manual', pk=invoice.pk, manual_number=override) if override else redirect('invoice_pdf', pk=invoice.pk)
    return render(request, 'invoice_app/new_invoice.html', {'profile': profile, 'clients': client_list, 'today': timezone.localdate().isoformat()})

def invoice_pdf(request, pk, manual_number=None):
    """Render the one-page QuantBud invoice to match the supplied QB020 PDF."""
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm

    invoice = get_object_or_404(Invoice.objects.prefetch_related('lines'), pk=pk)
    profile = BusinessProfile.current()
    display_number = manual_number or invoice.invoice_number
    buffer = BytesIO()
    page_w, page_h = letter
    c = pdfcanvas.Canvas(buffer, pagesize=letter)

    navy = colors.HexColor(profile.primary_color or '#12558D')
    alt = colors.HexColor(profile.alternate_row_color or '#D5E6F3')
    ink = colors.HexColor('#111111')
    white = colors.white

    def x(mm_value): return mm_value * mm
    def y_from_top(mm_value): return page_h - x(mm_value)
    def txt(value, x_mm, top_mm, size=6.5, font='Helvetica', color=ink):
        c.setFillColor(color); c.setFont(font, size)
        c.drawString(x(x_mm), y_from_top(top_mm) - size*0.8, str(value or ''))
    def right(value, right_mm, top_mm, size=6.5, font='Helvetica', color=ink):
        c.setFillColor(color); c.setFont(font, size)
        c.drawRightString(x(right_mm), y_from_top(top_mm) - size*0.8, str(value or ''))
    def centered(value, center_mm, top_mm, size=6.5, font='Helvetica', color=ink):
        c.setFillColor(color); c.setFont(font, size)
        c.drawCentredString(x(center_mm), y_from_top(top_mm) - size*0.8, str(value or ''))
    def fit(value, max_chars):
        value = str(value or '')
        return value if len(value) <= max_chars else value[:max_chars-1] + '…'

    # Page title and the thin blue frame match the source's compact, upper-page layout.
    centered('Invoice', 107.9, 1.0, 7.5)
    c.setStrokeColor(navy); c.setLineWidth(1.0)
    c.rect(x(18.1), y_from_top(146.9), x(177.6), x(130.1), stroke=1, fill=0)

    # Issuer details at upper left; invoice badge at upper right.
    txt(profile.name.upper(), 18.6, 21.5, 8.0, 'Helvetica-Bold')
    issuer_lines = (profile.address or '').splitlines()
    line_top = 25.6
    for line in issuer_lines[:3]:
        txt(line, 18.4, line_top, 6.3); line_top += 3.2
    txt('Contact Number - ' + (profile.phone or ''), 18.2, line_top, 6.3); line_top += 3.2
    txt('Email ID: ' + (profile.email or ''), 18.2, line_top, 6.3); line_top += 3.2
    txt('GSTIN : ' + (profile.gstin or ''), 18.2, line_top, 6.3)

    badge_x, badge_top, badge_w, badge_h = 175.8, 21.5, 19.8, 9.0
    c.setFillColor(navy); c.rect(x(badge_x), y_from_top(badge_top + badge_h), x(badge_w), x(badge_h), stroke=0, fill=1)
    txt('INVOICE', badge_x + 1.2, badge_top + 4.2, 10.0, 'Helvetica-Bold', white)
    txt('#' + display_number, badge_x + 1.2, badge_top + 7.6, 6.2, 'Helvetica-Bold', white)

    # Bill-to block and three invoice metadata fields.
    client = invoice.client
    txt('Bill To:', 18.5, 50.7, 7.0, 'Helvetica-Bold', navy)
    txt(client.name, 18.4, 53.7, 6.5, 'Helvetica-Bold')
    address_lines = (client.address or '').splitlines()
    for i, line in enumerate(address_lines[:4]):
        txt(line, 18.4, 56.7 + i*2.8, 6.2)

    meta_top = 67.7
    txt('Invoice Date', 18.5, meta_top, 6.6, 'Helvetica-Bold', navy)
    invoice_date_label = invoice.invoice_date.strftime('%B %d, %Y').replace(' 0', ' ' )
    txt(invoice_date_label, 18.5, meta_top+2.8, 6.5)
    centered('Terms', 107.4, meta_top, 6.6, 'Helvetica-Bold', navy)
    centered(str(invoice.terms_days), 107.4, meta_top+2.8, 6.5)
    txt('Due Date', 151.2, meta_top, 6.6, 'Helvetica-Bold', navy)
    due_date_label = invoice.due_date.strftime('%B %d, %Y').replace(' 0', ' ')
    txt(due_date_label, 151.2, meta_top+2.8, 6.5)

    # Exact five-column table from the source PDF. Date range is part of Memo/Description.
    left = 18.1
    widths = [61.7, 55.5, 15.8, 23.6, 20.4]
    edges = [left]
    for width in widths: edges.append(edges[-1] + width)
    table_top = 76.8
    header_h = 6.8
    row_h = 2.84
    invoice_lines = list(invoice.lines.all())
    visible_rows = max(10, len(invoice_lines))
    row_h = min(row_h, 28.4 / visible_rows)
    table_bottom = table_top + header_h + visible_rows*row_h

    c.setFillColor(navy)
    c.rect(x(left), y_from_top(table_top+header_h), x(sum(widths)), x(header_h), stroke=0, fill=1)
    headers = ['Consultant -Project Name', 'Memo/Description', 'Qty', 'Unit Price', 'Amount']
    for i, heading in enumerate(headers):
        if i == 1:
            centered(heading, (edges[i] + edges[i+1]) / 2, table_top+2.5, 6.5, 'Helvetica-Bold', white)
        else:
            txt(heading, edges[i]+0.8, table_top+2.5, 6.5, 'Helvetica-Bold', white)

    invoice_lines = invoice_lines[:visible_rows]
    for row in range(visible_rows):
        row_top = table_top + header_h + row*row_h
        if row % 2 == 1:
            c.setFillColor(alt)
            c.rect(x(left), y_from_top(row_top+row_h), x(sum(widths)), x(row_h), stroke=0, fill=1)
        if row >= len(invoice_lines):
            continue
        line = invoice_lines[row]
        desc = line.description or ''
        if line.from_date and line.to_date:
            date_range = f"{line.from_date.strftime('%B %d')} - {line.to_date.strftime('%B %d, %Y')}"
            desc = (desc + ' - ' if desc else '') + date_range
        baseline = row_top + 0.6
        txt(fit(line.project_name, 42), edges[0]+0.8, baseline, 6.0)
        txt(fit(desc, 55), edges[1]+0.8, baseline, 5.9)
        right(f'{line.quantity_days:.2f}', edges[3]-0.8, baseline, 6.0)
        right(f'{line.unit_price:,.2f}', edges[4]-0.8, baseline, 6.0)
        right(f'{line.amount:,.2f}', edges[5]-0.8, baseline, 6.0)

    # The sample uses vertical separators and only a strong bottom/total rule,
    # not a full grid around every row.
    c.setStrokeColor(ink); c.setLineWidth(0.75)
    for edge in edges:
        c.line(x(edge), y_from_top(table_top), x(edge), y_from_top(table_bottom))
    c.line(x(left), y_from_top(table_top), x(edges[-1]), y_from_top(table_top))
    c.line(x(left), y_from_top(table_top+header_h), x(edges[-1]), y_from_top(table_top+header_h))
    c.line(x(left), y_from_top(table_bottom), x(edges[-1]), y_from_top(table_bottom))
    right(f'{invoice.total:,.2f}', edges[-1]-0.8, table_bottom-2.6, 6.5, 'Helvetica-Bold')
    right(invoice.currency, edges[-1]-0.8, table_bottom+2.2, 6.2)

    # Payment information on the left; separate blue total strip on the right.
    payment_top = 116.8
    txt('Payment Details', 18.5, payment_top, 7.0, 'Helvetica-Bold')
    payment_lines = [
        'Bank Name: ' + (profile.bank_name or ''),
        'Account Holder name : ' + (profile.account_holder or ''),
        'Account no: - ' + (profile.account_number or ''),
        'Bank Country Name - ' + (profile.bank_country or ''),
        'IFSC code : ' + (profile.ifsc or ''),
    ]
    for i, line in enumerate(payment_lines):
        offsets = [3.5, 6.5, 10.3, 14.1, 17.8]
        txt(line, 18.4, payment_top+offsets[i], 6.2)

    strip_x, strip_top, strip_w, strip_h = 150.7, 116.6, 44.8, 3.2
    c.setFillColor(navy); c.rect(x(strip_x), y_from_top(strip_top+strip_h), x(strip_w), x(strip_h), stroke=0, fill=1)
    txt('Total', strip_x+1.0, strip_top+0.2, 6.5, 'Helvetica-Bold', white)
    right(f'{invoice.total:,.2f}', strip_x+strip_w-1.0, strip_top+0.2, 6.5, 'Helvetica-Bold', white)

    # Signature remains at lower right; footer is centered as in the supplied page.
    centered(profile.signature_text or profile.name, 168.2, 134.6, 7.0, 'Helvetica')
    centered(profile.footer_text or 'Thank you for your business!', 107.9, 143.6, 7.0, 'Helvetica-Oblique')
    centered('Page 1', 107.9, 274.5, 7.0, 'Helvetica')

    c.showPage(); c.save()
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Invoice-{display_number}.pdf"'
    return response
