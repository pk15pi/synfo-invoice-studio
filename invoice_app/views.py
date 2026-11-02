import json

from django.contrib import messages
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST

from . import billing, services
from .forms import BusinessProfileForm, ClientForm, PdfOverrideForm, ProjectTemplateForm
from .models import BusinessProfile, Client, Invoice, ProjectTemplate
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data


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
    return render(request, 'invoice_app/clients.html', {'clients': Client.objects.all()})


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


def projects(request, client_id):
    client = get_object_or_404(Client, pk=client_id)
    return render(request, 'invoice_app/projects.html', {'client': client, 'projects': client.projects.all()})


def project_edit(request, client_id, pk=None):
    client = get_object_or_404(Client, pk=client_id)
    project = get_object_or_404(ProjectTemplate, pk=pk, client=client) if pk else None
    form = ProjectTemplateForm(request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.client = client
        obj.save()
        messages.success(request, 'Project template saved.')
        return redirect('projects', client_id=client.pk)
    return render(request, 'invoice_app/form_page.html', {
        'form': form, 'title': f"{'Edit' if project else 'Add'} project for {client.name}",
        'back_url': f'/customers/{client.pk}/projects/', 'project_help': True})


@require_POST
def project_delete(request, client_id, pk):
    get_object_or_404(ProjectTemplate, pk=pk, client_id=client_id).delete()
    messages.success(request, 'Project template deleted.')
    return redirect('projects', client_id=client_id)


def _projects_json(clients_qs):
    data = {}
    for c in clients_qs:
        data[c.pk] = {
            'terms': c.default_terms_days, 'projects': [{
                'id': p.pk, 'name': p.name, 'description': p.description, 'type': p.pricing_type,
                'monthly': str(p.monthly_amount), 'daily': str(p.daily_rate), 'basis': p.proration_basis,
                'full': p.fixed_monthly_even_if_partial, 'label': p.method_label} for p in c.projects.all()]}
    return data


def _pdf_response(pdf_bytes, filename, download):
    resp = HttpResponse(pdf_bytes, content_type='application/pdf')
    resp['Content-Disposition'] = f"{'attachment' if download else 'inline'}; filename=\"{get_valid_filename(filename)}\""
    return resp


def invoice_edit(request, pk=None):
    """Create a new invoice or edit/reopen an existing one.

    action=preview  -> PDF of the unsaved form data (no DB write, no number used)
    action=save     -> saves; a NEW invoice consumes exactly one sequence number"""
    profile = BusinessProfile.current()
    invoice = get_object_or_404(Invoice, pk=pk) if pk else None
    all_clients = Client.objects.prefetch_related('projects')
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
                    pdf = render_invoice_pdf(build_invoice_data(inv, profile, lines=lines))
                    return _pdf_response(pdf, f'{inv.display_number}-preview.pdf', action == 'download')
                invoice = services.save_invoice(data, invoice)
                for w in services.column_overflow_warnings(data.lines):
                    messages.warning(request, w)
                messages.success(request, f'Invoice {invoice.invoice_number} saved.')
                return redirect('invoice_detail', pk=invoice.pk)
    initial = {}
    if data is not None:
        initial = {'client': data.client.pk, 'invoice_date': data.invoice_date, 'terms_days': data.terms_days,
                   'due_date': data.due_date, 'pdf_number_override': data.pdf_number_override,
                   'lines': [dict(project=l.project.pk if l.project else '', name=l.project_name, desc=l.description,
                                  start=l.from_date, end=l.to_date, days=l.days,
                                  unit=l.unit_override if l.unit_override is not None else '',
                                  amount=l.amount_override if l.amount_override is not None else '') for l in data.lines]}
    elif invoice is not None:
        initial = {'client': invoice.client_id, 'invoice_date': invoice.invoice_date, 'terms_days': invoice.terms_days,
                   'due_date': invoice.due_date, 'pdf_number_override': invoice.pdf_number_override,
                   'lines': [dict(project=l.project_id or '', name=l.project_name, desc=l.description, start=l.from_date,
                                  end=l.to_date, days=l.quantity_days, unit='' if l.project_id else l.unit_price,
                                  amount=l.amount if (l.amount_overridden or not l.project_id) else '')
                             for l in invoice.lines.all()]}
    else:
        from django.utils import timezone
        today = timezone.localdate()
        initial = {'client': request.GET.get('client', ''), 'invoice_date': today,
                   'terms_days': profile.payment_terms_days,
                   'due_date': billing.due_date_for(today, profile.payment_terms_days), 'lines': []}
    js_init = json.dumps({**initial, 'lines': initial.get('lines', [])}, default=str)
    return render(request, 'invoice_app/invoice_form.html', {
        'invoice': invoice, 'profile': profile, 'clients': all_clients,
        'next_number': invoice.invoice_number if invoice else profile.next_number(),
        'projects_json': json.dumps(_projects_json(all_clients)), 'init_json': js_init,
        'rounding': profile.amount_rounding, 'initial': initial})


def invoice_list(request):
    return render(request, 'invoice_app/invoice_list.html',
                  {'invoices': Invoice.objects.select_related('client')})


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    form = PdfOverrideForm(request.POST or None, instance=invoice)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'PDF number override saved (the saved invoice number and sequence are unchanged).')
        return redirect('invoice_detail', pk=pk)
    return render(request, 'invoice_app/invoice_detail.html', {
        'invoice': invoice, 'lines': invoice.lines.all(), 'form': form,
        'total_indian': billing.format_indian(invoice.total)})


def invoice_pdf(request, pk, manual_number=None):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    number = manual_number.strip() if manual_number else None
    pdf = render_invoice_pdf(build_invoice_data(invoice, BusinessProfile.current(), number=number))
    return _pdf_response(pdf, f'{number or invoice.display_number}.pdf', request.GET.get('download') == '1')
