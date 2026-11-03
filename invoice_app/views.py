from datetime import date
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST

from . import billing, descriptions, gst, ledger, services
from .forms import (BusinessProfileForm, ClientForm, CreditNoteForm, InvoiceDescriptionTemplateForm, InvoiceFormatTemplateForm, PaymentForm, PdfOverrideForm,
                    ProjectTemplateForm)
from .models import BusinessProfile, Client, CreditNote, Invoice, InvoiceDescriptionTemplate, InvoiceFormatTemplate, InvoicePayment, ProjectTemplate
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data
from .pdf.formats import render_for_invoice


def dashboard(request):
    return render(request, 'invoice_app/dashboard.html', {
        'profile': BusinessProfile.current(), 'clients': Client.objects.all()[:6],
        'invoices': Invoice.objects.select_related('client')[:8]})


def profile_edit(request):
    profile = BusinessProfile.current()
    form = BusinessProfileForm(request.POST or None, request.FILES or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Business profile saved.')
        return redirect('profile_edit')
    return render(request, 'invoice_app/profile.html', {'form': form, 'profile': profile})


def clients(request):
    qs, status = _status_filter(request, Client.objects.all())
    return render(request, 'invoice_app/clients.html', {'clients': qs, 'status': status})


def client_detail(request, pk):
    client = get_object_or_404(Client, pk=pk)
    return render(request, 'invoice_app/client_detail.html', {
        'client': client, 'projects': client.projects.all(),
        'invoices': Invoice.objects.filter(client=client)})


def client_edit(request, pk=None):
    client = get_object_or_404(Client, pk=pk) if pk else None
    form = ClientForm(request.POST or None, instance=client)
    if request.method == 'POST' and form.is_valid():
        client = form.save()
        messages.success(request, 'Customer saved.')
        return redirect('client_detail', pk=client.pk)
    return render(request, 'invoice_app/form_page.html', {
        'form': form, 'title': 'Edit customer' if client else 'Add customer',
        'back': 'clients'})


@require_POST
def client_delete(request, pk):
    try:
        get_object_or_404(Client, pk=pk).delete()
        messages.success(request, 'Customer deleted.')
    except ProtectedError:
        messages.error(request, 'This customer has invoices and cannot be deleted.')
    return redirect('clients')


def _status_filter(request, qs):
    status = request.GET.get('status', 'all')
    if status == 'active':
        qs = qs.filter(active=True)
    elif status == 'inactive':
        qs = qs.filter(active=False)
    return qs, status


def projects(request, client_id):
    client = get_object_or_404(Client, pk=client_id)
    qs, status = _status_filter(request, client.projects.all())
    return render(request, 'invoice_app/projects.html', {'client': client, 'projects': qs, 'status': status})


def projects_hub(request):
    """All saved Consultant -Project Names across customers, filterable."""
    qs, status = _status_filter(request, ProjectTemplate.objects.select_related('client').order_by('client__name', 'name'))
    return render(request, 'invoice_app/projects.html', {'client': None, 'projects': qs, 'status': status,
                                                         'clients': Client.objects.all()})


def _back(request, fallback):
    nxt = request.POST.get('next', '')
    return redirect(nxt) if nxt.startswith('/') and not nxt.startswith('//') else redirect(fallback)


@require_POST
def client_toggle(request, pk):
    c = get_object_or_404(Client, pk=pk)
    c.active = not c.active
    c.save(update_fields=['active'])
    messages.success(request, f'{c.name} is now {"active" if c.active else "inactive"}.')
    return _back(request, 'clients')


@require_POST
def project_toggle(request, client_id, pk):
    p = get_object_or_404(ProjectTemplate, pk=pk, client_id=client_id)
    p.active = not p.active
    p.save(update_fields=['active'])
    messages.success(request, f'{p.name} is now {"active" if p.active else "inactive"}.')
    return _back(request, reverse('projects', args=[client_id]))


def project_edit(request, client_id, pk=None):
    client = get_object_or_404(Client, pk=client_id)
    project = get_object_or_404(ProjectTemplate, pk=pk, client=client) if pk else None
    form = ProjectTemplateForm(request.POST or None, instance=project, client=client)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.client = client
        obj.save()
        messages.success(request, 'Project template saved.')
        return redirect('projects', client_id=client.pk)
    return render(request, 'invoice_app/project_form.html', {
        'form': form, 'title': f"{'Edit' if project else 'Add'} project for {client.name}",
        'back_url': f'/customers/{client.pk}/projects/', 'project_help': client.get_billing_type_display()})


@require_POST
def project_delete(request, client_id, pk):
    get_object_or_404(ProjectTemplate, pk=pk, client_id=client_id).delete()
    messages.success(request, 'Project template deleted.')
    return redirect('projects', client_id=client_id)


def _dup_names(clients_qs):
    from collections import Counter
    n = Counter(c.name for c in clients_qs)
    return {k for k, v in n.items() if v > 1}


def _last_invoices(exclude_pk=None):
    """client_id -> (invoice_number, pdf_number_override) of the most recently created invoice."""
    qs = Invoice.objects.order_by('client_id', '-created_at', '-id')
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    out = {}
    for cid, num, pdf in qs.values_list('client_id', 'invoice_number', 'pdf_number_override'):
        out.setdefault(cid, (num, pdf))
    return out


def _projects_json(clients_qs, keep_project_ids=(), exclude_invoice=None):
    """Only active projects are offered; inactive ones already on the invoice being edited are kept."""
    data = {}
    last = _last_invoices(exclude_invoice)
    for c in clients_qs:
        num, pdf = last.get(c.pk, ('', ''))
        data[c.pk] = {'last': num, 'last_pdf': pdf,
            'terms_mode': c.terms_mode, 'terms_text': c.terms_text, 'billing': c.billing_type, 'billing_label': c.get_billing_type_display(),
            'treatment': c.tax_treatment, 'state_code': c.state_code, 'gstin': c.gstin, 'lut': c.lut_number, 'projects': [{
                'id': p.pk, 'name': p.name, 'description': p.description, 'desc_type': p.invoice_description_type, 'type': p.pricing_type,
                'monthly': str(p.monthly_amount), 'daily': str(p.daily_rate), 'basis': p.proration_basis,
                'full': p.fixed_monthly_even_if_partial, 'label': p.method_label,
                'sac': p.sac_code or gst.DEFAULT_SAC, 'gst_rate': str(p.gst_rate)} for p in c.projects.all() if p.active or p.pk in keep_project_ids]}
    return data


def _pdf_response(pdf_bytes, filename, download):
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f"{'attachment' if download else 'inline'}; filename=\"{get_valid_filename(filename)}\""
    return resp


def _ym(d):
    return d.strftime('%Y-%m') if d else ''


MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
               'October', 'November', 'December']


def _ym_parts(d):
    return {'billing_month': _ym(d), 'billing_year': str(d.year) if d else '', 'billing_mon': f'{d.month:02d}' if d else ''}


def _year_options(selected=''):
    """Current year (Asia/Kolkata) -3 .. +1, newest first, plus any saved year outside that range."""
    from django.utils import timezone
    y = timezone.localdate().year
    years = {str(x) for x in range(y - 3, y + 2)}
    if selected:
        years.add(selected)
    return sorted(years, reverse=True)


def invoice_edit(request, pk=None):
    """Create a new invoice or edit/reopen an existing one.

    action=preview  -> PDF of the unsaved form data (no DB write, no number used)
    action=save     -> saves; a NEW invoice consumes exactly one sequence number"""
    profile = BusinessProfile.current()
    invoice = get_object_or_404(Invoice, pk=pk) if pk else None
    # Bill To offers active customers only (plus the current customer when editing an invoice).
    from django.db.models import Q
    all_clients = Client.objects.filter(Q(active=True) | Q(pk=invoice.client_id if invoice else 0)).prefetch_related('projects')
    keep_ids = set(invoice.lines.exclude(project=None).values_list('project_id', flat=True)) if invoice else set()
    data = None
    if request.method == 'POST':
        client = Client.objects.filter(pk=request.POST.get('client') or 0).first()
        if client is None:
            messages.error(request, 'Select a customer.')
        else:
            data = services.parse_invoice_post(request.POST, client, profile)
            for e in data.errors:
                messages.error(request, e)
            if not data.errors:
                action = request.POST.get('action', 'save')
                if action in ('preview', 'download'):
                    number = invoice.invoice_number if invoice else profile.next_number()
                    inv, lines = services.unsaved_invoice(data, number)
                    pdf = render_for_invoice(inv, profile, lines=lines)
                    return _pdf_response(pdf, f'{inv.display_number}-preview.pdf', action == 'download')
                try:
                    invoice = services.save_invoice(data, invoice)
                except ValidationError as e:
                    for msg in e.messages:
                        messages.error(request, msg)
                    invoice = Invoice.objects.get(pk=invoice.pk) if invoice else None
                    return redirect('invoice_edit', pk=invoice.pk) if invoice else redirect('new_invoice')
                for w in services.column_overflow_warnings(data.lines):
                    messages.warning(request, w)
                messages.success(request, f'Invoice {invoice.invoice_number} saved.')
                return redirect('invoice_detail', pk=invoice.pk)
    initial = {}
    if data is not None:
        initial = {'client': data.client.pk, 'invoice_date': data.invoice_date, 'terms_mode': data.terms_mode,
                   'terms_text': data.terms_text or ('' if data.terms_days is None else data.terms_days),
                   'due_date': data.due_date, 'pdf_number_override': data.pdf_number_override,
                   **_ym_parts(data.billing_month),
                   'tax_treatment': data.tax_treatment, 'place_of_supply': data.place_of_supply,
                   'po_number': data.po_number, 'format_template': request.POST.get('format_template', ''),
                   'lines': [dict(project=l.project.pk if l.project else '', name=l.project_name, desc=l.description,
                                  start=l.from_date, end=l.to_date, days=l.days,
                                  unit=l.unit_override if l.unit_override is not None else '',
                                  amount=l.amount_override if l.amount_override is not None else '') for l in data.lines]}
    elif invoice is not None:
        initial = {'client': invoice.client_id, 'invoice_date': invoice.invoice_date, 'terms_mode': invoice.terms_mode,
                   'terms_text': invoice.terms_display,
                   'due_date': invoice.due_date, 'pdf_number_override': invoice.pdf_number_override,
                   **_ym_parts(invoice.billing_month),
                   'tax_treatment': invoice.tax_treatment, 'place_of_supply': invoice.place_of_supply,
                   'po_number': invoice.po_number,
                   'format_template': (str(invoice.format_template_id) if invoice.format_template_id and
                                       invoice.format_template_id != invoice.client.invoice_format_id else ''),
                   'lines': [dict(project=l.project_id or '', name=l.project_name, desc=l.description, start=l.from_date,
                                  end=l.to_date, days=l.quantity_days, unit='' if (l.project_id or l.unit_price is None) else l.unit_price,
                                  amount=l.amount if (l.amount_overridden or not l.project_id) else '')
                             for l in invoice.lines.all()]}
    else:
        from django.utils import timezone
        today = timezone.localdate()
        initial = {'client': request.GET.get('client', ''), 'invoice_date': today,
                   'terms_mode': 'custom', 'terms_text': str(profile.payment_terms_days),
                   'profile_terms': str(profile.payment_terms_days),
                   'due_date': billing.due_date_for(today, profile.payment_terms_days),
                   **_ym_parts(services.previous_month(today)), 'lines': []}
    js_init = json.dumps({**initial, 'profile_terms': str(profile.payment_terms_days), 'lines': initial.get('lines', [])}, default=str)
    return render(request, 'invoice_app/invoice_form.html', {
        'invoice': invoice, 'profile': profile, 'clients': all_clients,
        'next_number': invoice.invoice_number if invoice else profile.next_number(),
        'projects_json': json.dumps(_projects_json(all_clients, keep_ids, invoice.pk if invoice else None)), 'init_json': js_init,
        'rounding': profile.amount_rounding, 'initial': initial,
        'year_options': _year_options(initial.get('billing_year')),
        'mon_options': [(f'{i:02d}', n) for i, n in enumerate(MONTH_NAMES, 1)],
        'dup_names': _dup_names(all_clients), 'tax_types': Invoice.TAX_TYPES,
        'formats': InvoiceFormatTemplate.objects.filter(
            Q(is_active=True) | Q(pk=invoice.format_template_id if invoice and invoice.format_template_id else 0)),
        'format_locked': bool(invoice and invoice.status != Invoice.DRAFT),
        'client_formats_json': json.dumps({str(c.pk): c.invoice_format.name if c.invoice_format_id else ''
                                           for c in all_clients.select_related('invoice_format')}),
        'treatments': gst.TREATMENTS, 'states': gst.STATE_CHOICES, 'biz_state': gst.business_state(profile),
        'biz_state_label': gst.state_label(gst.business_state(profile)),
        'init_treatment': initial.get('tax_treatment') or gst.EXPORT,
        # Legacy manual tax inputs are only offered for an invoice that was saved with manual export tax.
        'legacy_tax': bool(invoice and invoice.tax_treatment == gst.EXPORT and invoice.tax_type != Invoice.TAX_NONE)})


def _with_pay(qs):
    out = list(qs.select_related('client').prefetch_related('payments'))
    for i in out:
        i.pay = i.payment_summary()
    return out


def invoice_list(request):
    return render(request, 'invoice_app/invoice_list.html', {'invoices': _with_pay(Invoice.objects.all())})


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    form = PdfOverrideForm(request.POST or None, instance=invoice)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'PDF number override saved (the saved invoice number and sequence are unchanged).')
        return redirect('invoice_detail', pk=pk)
    return render(request, 'invoice_app/invoice_detail.html', {
        'invoice': invoice, 'lines': invoice.lines.all(), 'form': form,
        'total_indian': billing.format_indian(invoice.total), 'pay': invoice.payment_summary(),
        'payments': invoice.payments.select_related('bank_account'), 'pay_form': PaymentForm(invoice=invoice, initial={
            'receipt_date': max(date.today(), invoice.invoice_date)}),
        'ledger_entry': invoice.ledger_entry, 'credit_notes': invoice.credit_notes.all(),
        'other_formats': InvoiceFormatTemplate.objects.filter(is_active=True),
        'cn_form': CreditNoteForm(initial={'date': max(date.today(), invoice.invoice_date)})})


def _errors(request, e):
    for msg in getattr(e, 'messages', [str(e)]):
        messages.error(request, msg)


@require_POST
def invoice_finalize(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        ledger.finalize_invoice(inv)
        messages.success(request, f'Invoice {inv.invoice_number} finalized and posted to the ledger.')
    except ValidationError as e:
        _errors(request, e)
    return redirect('invoice_detail', pk=pk)


@require_POST
def invoice_post_ledger(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        ledger.post_to_ledger(inv)
        messages.success(request, f'Invoice {inv.invoice_number} and its receipts posted to the ledger.')
    except ValidationError as e:
        _errors(request, e)
    return redirect('invoice_detail', pk=pk)


@require_POST
def invoice_cancel(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        ledger.cancel_invoice(inv, request.POST.get('reason'))
        messages.success(request, f'Invoice {inv.invoice_number} cancelled.')
    except ValidationError as e:
        _errors(request, e)
    return redirect('invoice_detail', pk=pk)


@require_POST
def credit_note_add(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    form = CreditNoteForm(request.POST)
    if not form.is_valid():
        for errs in form.errors.values():
            _errors(request, ValidationError(errs))
        return redirect('invoice_detail', pk=pk)
    try:
        cn = ledger.create_credit_note(inv, **form.cleaned_data)
        messages.success(request, f'Credit note {cn.number} for {cn.total:,.2f} issued.')
    except ValidationError as e:
        _errors(request, e)
    return redirect('invoice_detail', pk=pk)


@require_POST
def credit_note_cancel(request, pk, cn_pk):
    cn = get_object_or_404(CreditNote, pk=cn_pk, invoice_id=pk)
    try:
        ledger.cancel_credit_note(cn, request.POST.get('reason'))
        messages.success(request, f'Credit note {cn.number} cancelled.')
    except ValidationError as e:
        _errors(request, e)
    return redirect('invoice_detail', pk=pk)


def payment_edit(request, pk, payment_pk=None):
    invoice = get_object_or_404(Invoice, pk=pk)
    payment = get_object_or_404(InvoicePayment, pk=payment_pk, invoice=invoice) if payment_pk else None
    form = PaymentForm(request.POST or None, instance=payment, invoice=invoice)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.invoice = invoice
        try:
            obj.save()
        except ValidationError as e:
            _errors(request, e)
            return redirect('invoice_detail', pk=pk)
        if form.overpay_warning:
            messages.warning(request, form.overpay_warning)
        messages.success(request, f'Payment of {obj.amount:,.2f} saved.')
        return redirect('invoice_detail', pk=pk)
    return render(request, 'invoice_app/payment_form.html', {'invoice': invoice, 'form': form, 'payment': payment})


def payment_delete(request, pk, payment_pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    payment = get_object_or_404(InvoicePayment, pk=payment_pk, invoice=invoice)
    if request.method == 'POST':
        try:
            payment.delete()
        except ValidationError as e:
            _errors(request, e)
            return redirect('invoice_detail', pk=pk)
        messages.success(request, 'Payment deleted.')
        return redirect('invoice_detail', pk=pk)
    return render(request, 'invoice_app/payment_delete.html', {'invoice': invoice, 'payment': payment})


def receipts(request):
    g = request.GET
    qs = InvoicePayment.objects.select_related('invoice', 'invoice__client')
    f = {k: (g.get(k) or '').strip() for k in ('from', 'to', 'client', 'mode', 'q')}
    if f['from']:
        qs = qs.filter(receipt_date__gte=f['from'])
    if f['to']:
        qs = qs.filter(receipt_date__lte=f['to'])
    if f['client'].isdigit():
        qs = qs.filter(invoice__client_id=int(f['client']))
    if f['mode']:
        qs = qs.filter(mode=f['mode'])
    if f['q']:
        from django.db.models import Q
        qs = qs.filter(Q(reference__icontains=f['q']) | Q(invoice__invoice_number__icontains=f['q']) | Q(notes__icontains=f['q']))
    rows = list(qs)
    return render(request, 'invoice_app/receipts.html', {
        'rows': rows, 'f': f, 'clients': Client.objects.all(), 'modes': InvoicePayment.MODES,
        'tot_amount': sum((r.amount for r in rows), 0), 'tot_ded': sum((r.deductions for r in rows), 0)})


def invoice_pdf(request, pk, manual_number=None):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    number = manual_number.strip() if manual_number else None
    template, suffix = None, ''
    if request.GET.get('format'):
        # Ad-hoc "download in another format": uses the template's current settings; nothing is saved.
        template = get_object_or_404(InvoiceFormatTemplate, pk=request.GET['format'] if
                                     request.GET['format'].isdigit() else 0, is_active=True)
        suffix = f'-{template.code}'
    pdf = render_for_invoice(invoice, BusinessProfile.current(), number=number, template=template)
    return _pdf_response(pdf, f'{number or invoice.display_number}{suffix}.pdf', request.GET.get('download') == '1')


# --- Invoice Format Template Master ---

def format_templates(request):
    from django.db.models import Count
    qs = InvoiceFormatTemplate.objects.annotate(n_clients=Count('clients', distinct=True),
                                                n_invoices=Count('invoices', distinct=True))
    status = request.GET.get('status', 'all')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    return render(request, 'invoice_app/format_templates.html', {'templates': qs, 'status': status})


def format_template_edit(request, pk=None):
    """Add/edit a format. action=preview renders a sample PDF of the (unsaved) form values: nothing is
    saved and no invoice number is used."""
    obj = get_object_or_404(InvoiceFormatTemplate, pk=pk) if pk else None
    form = InvoiceFormatTemplateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        if request.POST.get('action') == 'preview':
            return _format_sample_response(form.save(commit=False), request.POST.get('kind'))
        form.save()
        messages.success(request, 'Invoice format saved. Saved invoices keep the format snapshot they were saved '
                                  'with.')
        return redirect('format_templates')
    return render(request, 'invoice_app/format_template_form.html', {
        'form': form, 'obj': obj, 'title': 'Edit invoice format' if obj else 'Add invoice format',
        'kinds': _SAMPLE_KINDS})


_SAMPLE_KINDS = [('export', 'Export'), ('lut', 'Export under LUT'), ('gst_intra', 'Domestic GST (CGST+SGST)'),
                 ('gst_inter', 'Domestic GST (IGST)')]


def _format_sample_response(template, kind):
    from .pdf.samples import KINDS, sample_invoice
    kind = kind if kind in KINDS else 'export'
    inv, lines = sample_invoice(kind)
    pdf = render_for_invoice(inv, BusinessProfile.current(), lines=lines, template=template)
    return _pdf_response(pdf, f'format-preview-{template.code or "new"}-{kind}.pdf', False)


def format_template_preview(request, pk):
    return _format_sample_response(get_object_or_404(InvoiceFormatTemplate, pk=pk), request.GET.get('kind'))


@require_POST
def format_template_toggle(request, pk):
    t = get_object_or_404(InvoiceFormatTemplate, pk=pk)
    t.is_active = not t.is_active
    t.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'{t.name} is now {"active" if t.is_active else "inactive"}.')
    return redirect('format_templates')


@require_POST
def format_template_delete(request, pk):
    t = get_object_or_404(InvoiceFormatTemplate, pk=pk)
    try:
        t.delete()
        messages.success(request, 'Invoice format deleted.')
    except ProtectedError:
        messages.error(request, f'"{t.name}" is used by customers or saved invoices and cannot be deleted. '
                                'Deactivate it instead.')
    return redirect('format_templates')


# --- Description Template Master + Memo/Description rendering endpoint ---

def description_templates(request):
    from django.db.models import Count
    qs = InvoiceDescriptionTemplate.objects.annotate(n_projects=Count('projects', distinct=True),
                                                     n_lines=Count('used_on_lines', distinct=True))
    status = request.GET.get('status', 'all')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    return render(request, 'invoice_app/description_templates.html', {
        'templates': qs, 'status': status, 'placeholders': descriptions.ALLOWED_PLACEHOLDERS})


def description_template_edit(request, pk=None):
    obj = get_object_or_404(InvoiceDescriptionTemplate, pk=pk) if pk else None
    form = InvoiceDescriptionTemplateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Description template saved. Existing invoices keep their saved descriptions.')
        return redirect('description_templates')
    return render(request, 'invoice_app/form_page.html', {
        'form': form, 'title': 'Edit description template' if obj else 'Add description template',
        'back': 'description_templates'})


@require_POST
def description_template_toggle(request, pk):
    t = get_object_or_404(InvoiceDescriptionTemplate, pk=pk)
    t.is_active = not t.is_active
    t.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'{t.name} is now {"active" if t.is_active else "inactive"}.')
    return redirect('description_templates')


@require_POST
def description_template_delete(request, pk):
    t = get_object_or_404(InvoiceDescriptionTemplate, pk=pk)
    try:
        t.delete()
        messages.success(request, 'Description template deleted.')
    except ProtectedError:
        messages.error(request, f'"{t.name}" is assigned to projects or used on saved invoices and cannot be '
                                'deleted. Deactivate it instead.')
    return redirect('description_templates')


def line_description(request):
    """GET (read-only, no CSRF needed): the server-rendered default Memo/Description for one row.
    ?project=<id>&from=YYYY-MM-DD&to=YYYY-MM-DD&billing_month=YYYY-MM"""
    project = ProjectTemplate.objects.select_related('client', 'description_template').filter(
        pk=request.GET.get('project') if (request.GET.get('project') or '').isdigit() else 0).first()
    month = None
    try:
        month = services.parse_month(request.GET['billing_month']) if request.GET.get('billing_month') else None
    except ValueError:
        month = None
    text = descriptions.project_default_description(
        project, start_date=services._date(request.GET.get('from')), end_date=services._date(request.GET.get('to')),
        billing_month=month)
    return JsonResponse({'project': project.pk if project else None,
                         'type': project.invoice_description_type if project else '', 'description': text})
