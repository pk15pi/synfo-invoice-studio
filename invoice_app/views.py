from datetime import date, timedelta
from calendar import month_name
from decimal import Decimal
import json

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST
from employee_management.security import local_only

from . import billing, descriptions, gst, ledger, services
from .forms import (BusinessProfileForm, ClientForm, CreditNoteForm, InvoiceDescriptionTemplateForm, InvoiceEmailTemplateForm, InvoiceFormatTemplateForm, ManpowerDeploymentForm, PaymentForm, PdfOverrideForm,
                    ProjectTemplateForm, RecruitmentAgreementForm, RecruitmentCandidateForm,
                    RecruitmentPlacementForm, RecruitmentRequisitionForm)
from .models import (BusinessProfile, Client, CreditNote, Invoice, InvoiceDescriptionTemplate, InvoiceEmailTemplate, InvoiceFormatTemplate,
                     InvoicePayment, ManpowerBillingRecord, ManpowerDeployment, ManpowerRateRevision, ProjectTemplate,
                     RecruitmentAgreement, RecruitmentCandidate, RecruitmentPlacement, RecruitmentRequisition)
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data
from .pdf.formats import render_for_invoice
from reporting import periods as reporting_periods


def recruitment_dashboard(request):
    placements = RecruitmentPlacement.objects.select_related(
        'client', 'candidate', 'requisition', 'agreement', 'invoice').order_by('-created_at')
    client_id = request.GET.get('client', '')
    status = request.GET.get('status', '')
    candidate_id = request.GET.get('candidate', '')
    invoice_status = request.GET.get('invoice_status', '')
    designation = (request.GET.get('designation') or '').strip()
    joining_from, joining_to = services._date(request.GET.get('joining_from')), services._date(request.GET.get('joining_to'))
    recruitment_from, recruitment_to = services._date(request.GET.get('recruitment_from')), services._date(request.GET.get('recruitment_to'))
    q = (request.GET.get('q') or '').strip()
    if client_id.isdigit():
        placements = placements.filter(client_id=client_id)
    if candidate_id.isdigit():
        placements = placements.filter(candidate_id=candidate_id)
    if designation:
        placements = placements.filter(designation=designation)
    if joining_from:
        placements = placements.filter(joining_date__gte=joining_from)
    if joining_to:
        placements = placements.filter(joining_date__lte=joining_to)
    if recruitment_from:
        placements = placements.filter(fee_approved_on__gte=recruitment_from)
    if recruitment_to:
        placements = placements.filter(fee_approved_on__lte=recruitment_to)
    if status == 'pending':
        placements = placements.filter(fee_approved=False)
    elif status == 'approved':
        placements = placements.filter(fee_approved=True)
    elif status == 'invoiced':
        placements = placements.filter(invoice__status=Invoice.FINALIZED)
    if q:
        from django.db.models import Q
        placements = placements.filter(Q(candidate__candidate_name__icontains=q) |
                                       Q(designation__icontains=q) |
                                       Q(client__name__icontains=q) |
                                       Q(requisition__title__icontains=q))
    rows = list(placements[:500])
    if status == 'eligible':
        rows = [row for row in rows if not row.fee_approved and row.can_approve_fee]
    for row in rows:
        row.payment_summary = row.invoice.payment_summary() if row.invoice_id else None
    if invoice_status == 'unbilled':
        rows = [row for row in rows if row.fee_approved and not row.invoice_id]
    elif invoice_status == 'draft':
        rows = [row for row in rows if row.invoice_id and row.invoice.status == Invoice.DRAFT]
    elif invoice_status == 'invoiced':
        rows = [row for row in rows if row.invoice_id and row.invoice.status == Invoice.FINALIZED]
    elif invoice_status == 'paid':
        rows = [row for row in rows if row.payment_summary and row.payment_summary['status'] == 'Paid']
    elif invoice_status == 'outstanding':
        rows = [row for row in rows if row.payment_summary and row.payment_summary['outstanding'] > 0]
    all_placements = RecruitmentPlacement.objects.select_related('invoice').filter(invoice__status=Invoice.FINALIZED)
    invoices = {p.invoice_id: p.invoice for p in all_placements if p.invoice_id}.values()
    outstanding = sum((p.payment_summary()['outstanding'] for p in invoices), Decimal('0.00'))
    today = timezone.localdate()
    return render(request, 'invoice_app/recruitment_dashboard.html', {
        'placements': rows,
        'clients': Client.objects.order_by('name'),
        'candidates': RecruitmentCandidate.objects.order_by('candidate_name'),
        'agreements': RecruitmentAgreement.objects.select_related('client').order_by('client__name', '-effective_start')[:200],
        'selected_client': client_id, 'selected_candidate': candidate_id,
        'selected_status': status, 'selected_invoice_status': invoice_status, 'q': q,
        'pending_count': RecruitmentPlacement.objects.filter(fee_approved=False).count(),
        'eligible_uninvoiced_count': sum(1 for p in RecruitmentPlacement.objects.select_related(
            'agreement', 'client', 'candidate').filter(fee_approved=False) if p.can_approve_fee),
        'invoiced_count': RecruitmentPlacement.objects.filter(invoice__status=Invoice.FINALIZED).count(),
        'outstanding_total': outstanding,
        'designations': RecruitmentPlacement.objects.order_by('designation').values_list('designation', flat=True).distinct(),
        'selected_designation': designation, 'joining_from': joining_from, 'joining_to': joining_to,
        'recruitment_from': recruitment_from, 'recruitment_to': recruitment_to,
        'guarantees_expiring': RecruitmentPlacement.objects.filter(guarantee_end__gte=today,
            guarantee_end__lte=today + timedelta(days=30)).count(),
    })


def recruitment_candidate_edit(request, pk=None):
    candidate = get_object_or_404(RecruitmentCandidate, pk=pk) if pk else None
    form = RecruitmentCandidateForm(request.POST or None, instance=candidate)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, 'Candidate record saved. Personal contact and identity details remain in secure HR storage.')
        return redirect('recruitment_dashboard')
    return render(request, 'invoice_app/recruitment_candidate_form.html', {'form': form, 'candidate': candidate})


def recruitment_requisition_edit(request, pk=None):
    requisition = get_object_or_404(RecruitmentRequisition, pk=pk) if pk else None
    form = RecruitmentRequisitionForm(request.POST or None, instance=requisition)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Recruitment requisition saved.')
        return redirect('recruitment_dashboard')
    return render(request, 'invoice_app/recruitment_requisition_form.html', {'form': form, 'requisition': requisition})


def recruitment_agreement_edit(request, pk=None):
    agreement = get_object_or_404(RecruitmentAgreement, pk=pk) if pk else None
    if agreement and agreement.placements.filter(fee_approved=True).exists() and request.method == 'POST':
        messages.error(request, 'This agreement has approved placement fee snapshots. Add a new agreement version to preserve its terms.')
        return redirect('recruitment_agreement_add')
    form = RecruitmentAgreementForm(request.POST or None, instance=agreement)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Recruitment agreement saved. Existing approved fee snapshots are unchanged.')
        return redirect('recruitment_dashboard')
    return render(request, 'invoice_app/recruitment_agreement_form.html', {'form': form, 'agreement': agreement})


def recruitment_placement_edit(request, pk=None):
    placement = get_object_or_404(RecruitmentPlacement.objects.select_related(
        'client', 'candidate', 'agreement', 'invoice'), pk=pk) if pk else None
    if request.method == 'POST' and placement and placement.fee_approved:
        messages.error(request, 'The approved placement fee and its CTC/agreement snapshot are locked. Use the existing invoice credit-note workflow after billing.')
        return redirect('recruitment_dashboard')
    form = RecruitmentPlacementForm(request.POST or None, instance=placement,
                                    initial={'client': request.GET.get('client', ''),
                                             'designation': placement.candidate.default_designation if placement and placement.candidate_id else ''})
    action = request.POST.get('action', 'save') if request.method == 'POST' else ''
    if request.method == 'POST' and form.is_valid():
        candidate, client, requisition = (form.cleaned_data.get('candidate'), form.cleaned_data.get('client'),
                                           form.cleaned_data.get('requisition'))
        duplicate = RecruitmentPlacement.objects.filter(client=client, candidate=candidate,
            requisition=requisition).exclude(pk=placement.pk if placement else 0).first()
        if duplicate:
            form.add_error('candidate', f'This candidate already has a placement record for this client and requisition (#{duplicate.pk}).')
        else:
            obj = form.save(commit=False)
            try:
                with transaction.atomic():
                    obj.full_clean()
                    obj.save()
                    if action == 'approve':
                        from .recruitment_billing import approve_fee
                        amount = form.cleaned_data.get('fee_override_amount')
                        obj = approve_fee(obj, override_amount=amount,
                                          override_reason=form.cleaned_data.get('fee_override_reason') or '')
            except ValidationError as exc:
                for error in getattr(exc, 'error_dict', {}).values():
                    for item in error:
                        form.add_error(None, item.message)
                if not getattr(exc, 'error_dict', None):
                    form.add_error(None, exc.messages[0])
            else:
                messages.success(request, 'Placement saved; fee approved and frozen.' if action == 'approve'
                                 else 'Placement details saved. Fee remains pending explicit approval.')
                return redirect('recruitment_placement_edit', pk=obj.pk)
    fee_preview = None
    trigger_date = None
    can_approve = False
    if form.is_bound and form.is_valid():
        obj = form.save(commit=False)
        try:
            from .recruitment_billing import calculate_fee
            fee_preview = calculate_fee(obj)[0]
            trigger_date = obj.trigger_date
            can_approve = obj.can_approve_fee
        except (ValidationError, AttributeError, TypeError):
            pass
    elif placement:
        fee_preview, trigger_date, can_approve = placement.fee_amount, placement.trigger_date, placement.can_approve_fee
    if placement and placement.fee_approved:
        for field in form.fields.values():
            field.disabled = True
    return render(request, 'invoice_app/recruitment_placement_form.html', {
        'form': form, 'placement': placement, 'fee_preview': fee_preview,
        'trigger_date': trigger_date, 'can_approve': can_approve,
        'agreement_options_json': json.dumps([{'id': a.pk, 'client': a.client_id,
            'label': f'{a.agreement_reference or "Agreement"} · {a.get_billing_method_display()} · {a.effective_start:%d-%m-%Y}'}
            for a in RecruitmentAgreement.objects.exclude(status='draft').select_related('client')]),
        'requisition_options_json': json.dumps([{'id': r.pk, 'client': r.client_id,
            'label': f'{r.title}{" · " + r.reference if r.reference else ""}'}
            for r in RecruitmentRequisition.objects.select_related('client')]),
        'candidate_options_json': json.dumps([{'id': c.pk, 'designation': c.default_designation}
            for c in RecruitmentCandidate.objects.all()])})


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
        'client': client, 'projects': client.projects.all(), 'documents': client.documents.all(),
        'invoices': Invoice.objects.filter(client=client)})


def client_edit(request, pk=None):
    client = get_object_or_404(Client, pk=pk) if pk else None
    form = ClientForm(request.POST or None, request.FILES or None, instance=client)
    if request.method == 'POST' and form.is_valid():
        client = form.save()
        form.save_documents(client)
        messages.success(request, 'Customer saved.')
        return redirect('client_detail', pk=client.pk)
    return render(request, 'invoice_app/form_page.html', {
        'form': form, 'title': 'Edit customer' if client else 'Add customer',
        'back': 'clients', 'customer_form': True})



@local_only
def client_document(request, pk):
    """Serve a customer NDA/MCA file from private storage (never via a public URL)."""
    from employee_management.security import private_file_response
    from .models import ClientDocument
    doc = get_object_or_404(ClientDocument, pk=pk)
    ext = doc.file.name.rsplit('.', 1)[-1]
    name = get_valid_filename(f'{doc.client.name}-{doc.kind}.{ext}')
    return private_file_response(doc.file, doc.content_type or 'application/octet-stream', name)


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


def manpower_deployments(request):
    qs = ManpowerDeployment.objects.select_related('client', 'project', 'employee').prefetch_related('rate_revisions')
    client_id = request.GET.get('client', '')
    status = request.GET.get('status', '')
    method = request.GET.get('method', '')
    q = (request.GET.get('q') or '').strip()
    if client_id.isdigit():
        qs = qs.filter(client_id=client_id)
    if status in dict(ManpowerDeployment.STATUSES):
        qs = qs.filter(status=status)
    if method in dict(ManpowerDeployment.BILLING_METHODS):
        qs = qs.filter(billing_method=method)
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(designation__icontains=q) | Q(resource_name__icontains=q) |
                       Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q) |
                       Q(client__name__icontains=q) | Q(contract_reference__icontains=q))
    period = services.previous_month(timezone.localdate())
    if request.GET.get('period_mode'):
        selected_period = reporting_periods.resolve(request.GET)
    else:
        selected_period = reporting_periods.resolve({
            'period_mode': 'financial',
            'fy': str(period.year if period.month >= 4 else period.year - 1),
            'period_type': 'monthly',
            'period_value': str(period.month),
        })
    report_period = selected_period.start
    billing_records = ManpowerBillingRecord.objects.select_related('deployment__client', 'deployment__project',
                                                                  'invoice').filter(
        invoice__billing_month__range=(selected_period.start, selected_period.end))
    if client_id.isdigit():
        billing_records = billing_records.filter(deployment__client_id=client_id)
    return render(request, 'invoice_app/manpower_deployments.html', {
        'rows': qs, 'clients': Client.objects.order_by('name'), 'selected_client': client_id,
        'selected_status': status, 'selected_method': method, 'q': q,
        'statuses': ManpowerDeployment.STATUSES, 'methods': ManpowerDeployment.BILLING_METHODS,
        'active_count': ManpowerDeployment.objects.filter(status='active').count(),
        'recurring_count': ManpowerDeployment.objects.filter(status='active', recurring=True).count(),
        'period': period,
        'report_period': report_period, 'selected_period': selected_period, 'billing_records': billing_records,
        'period_billing_total': sum((r.amount for r in billing_records), Decimal('0.00')),
        'report_years': range(timezone.localdate().year - 5, timezone.localdate().year + 2),
        'month_options': [(f'{i:02d}', month_name[i]) for i in range(1, 13)],
        'selected_billing_month': report_period.strftime('%m'),
        'standard_filter_default_type': 'monthly',
        'standard_filter_default_fy': selected_period.start.year if selected_period.start.month >= 4 else selected_period.start.year - 1,
        'standard_filter_default_value': str(selected_period.start.month),
    })


def manpower_deployment_edit(request, pk=None):
    obj = get_object_or_404(ManpowerDeployment.objects.select_related('client').prefetch_related('rate_revisions'), pk=pk) if pk else None
    form = ManpowerDeploymentForm(request.POST or None, instance=obj,
                                  initial={'rate_effective_from': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        values = form.cleaned_data
        effective_from = values['rate_effective_from']
        old = (obj.billing_method, obj.billing_rate, obj.proration_method) if obj else None
        new = (values['billing_method'], values['billing_rate'], values['proration_method'])
        latest = obj.rate_revisions.order_by('-effective_from', '-id').first() if obj else None
        changed_rate = old is None or old != new or (latest and (latest.billing_method, latest.rate, latest.proration_method) != new)
        if changed_rate and latest and effective_from <= latest.effective_from:
            form.add_error('rate_effective_from', 'A rate revision must take effect after the latest saved revision; previous rates are preserved.')
        elif changed_rate and latest and not (values.get('rate_note') or '').strip():
            form.add_error('rate_note', 'Explain why this commercial rate is changing.')
        if not form.errors:
            try:
                with transaction.atomic():
                    deployment = form.save()
                    if changed_rate:
                        ManpowerRateRevision.objects.create(
                            deployment=deployment, billing_method=deployment.billing_method,
                            rate=deployment.billing_rate, proration_method=deployment.proration_method,
                            effective_from=effective_from, note=values.get('rate_note', '').strip())
            except IntegrityError:
                form.add_error('rate_effective_from', 'A rate revision already exists for this effective date.')
            else:
                messages.success(request, 'Manpower deployment and commercial terms saved.')
                return redirect('manpower_deployments')
    history = obj.rate_revisions.all() if obj else []
    return render(request, 'invoice_app/manpower_deployment_form.html', {
        'form': form, 'deployment': obj, 'history': history,
        'title': 'Edit manpower deployment' if obj else 'Add manpower deployment'})


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


def _projects_json(clients_qs, keep_project_ids=(), keep_placement_ids=()):
    """Only active projects are offered; inactive ones already on the invoice being edited are kept."""
    data = {}
    for c in clients_qs:
        deployments = []
        for d in c.manpower_deployments.all():
            revisions = [{'method': r.billing_method, 'rate': str(r.rate), 'proration': r.proration_method,
                          'effective': r.effective_from.isoformat()} for r in d.rate_revisions.all()]
            deployments.append({'id': d.pk, 'name': d.resource_name or (d.employee.full_name if d.employee_id else ''),
                                'designation': d.designation, 'method': d.billing_method, 'rate': str(d.billing_rate),
                                'proration': d.proration_method, 'start': d.start_date.isoformat(),
                                'end': d.end_date.isoformat() if d.end_date else '', 'status': d.status,
                                'recurring': d.recurring, 'rates': revisions})
        placements = []
        for placement in c.recruitment_placements.select_related('candidate', 'agreement', 'invoice'):
            if not placement.fee_approved or (placement.invoice_id and placement.pk not in keep_placement_ids):
                continue
            placements.append({'id': placement.pk,
                               'name': placement.candidate_name_snapshot or placement.candidate.candidate_name,
                               'designation': placement.designation_snapshot or placement.designation,
                               'fee': str(placement.fee_amount), 'agreement': placement.agreement.agreement_reference,
                               'invoice': placement.invoice_id or ''})
        data[c.pk] = {'last_pdf_ref_no': c.last_pdf_ref_no,
            'terms_mode': c.terms_mode, 'terms_text': c.terms_text, 'billing': c.billing_type, 'billing_label': c.get_billing_type_display(),
            'treatment': c.tax_treatment, 'state_code': c.state_code, 'gstin': c.gstin, 'lut': c.lut_number, 'projects': [{
                'id': p.pk, 'name': p.name, 'description': p.description, 'desc_type': p.invoice_description_type, 'type': p.pricing_type,
                'monthly': str(p.monthly_amount), 'daily': str(p.daily_rate), 'basis': p.proration_basis,
                'full': p.fixed_monthly_even_if_partial, 'label': p.method_label,
                'sac': p.sac_code or gst.DEFAULT_SAC, 'gst_rate': str(p.gst_rate)} for p in c.projects.all() if p.active or p.pk in keep_project_ids],
            'deployments': deployments, 'placements': placements}
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
    all_clients = Client.objects.filter(Q(active=True) | Q(pk=invoice.client_id if invoice else 0)).prefetch_related(
        'projects', 'manpower_deployments__employee', 'manpower_deployments__rate_revisions',
        'recruitment_placements__candidate', 'recruitment_placements__agreement', 'recruitment_placements__invoice')
    keep_ids = set(invoice.lines.exclude(project=None).values_list('project_id', flat=True)) if invoice else set()
    keep_placement_ids = set(invoice.lines.exclude(recruitment_placement=None).values_list(
        'recruitment_placement_id', flat=True)) if invoice else set()
    data = None
    if request.method == 'POST':
        client = Client.objects.filter(pk=request.POST.get('client') or 0).first()
        if client is None:
            messages.error(request, 'Select a customer.')
        else:
            data = services.parse_invoice_post(request.POST, client, profile, invoice=invoice)
            for e in data.errors:
                messages.error(request, e)
            action = request.POST.get('action', 'save')
            missing_manual_number = action == 'preview_manual' and not data.pdf_number_override
            if missing_manual_number:
                messages.error(request, 'Enter a manual PDF number to preview with that number.')
            if not data.errors and not missing_manual_number:
                if action in ('preview', 'preview_manual', 'download'):
                    number = invoice.invoice_number if invoice else profile.next_number()
                    inv, lines = services.unsaved_invoice(data, number)
                    manual_number = data.pdf_number_override if action == 'preview_manual' else None
                    pdf = render_for_invoice(inv, profile, lines=lines, number=manual_number)
                    display_number = manual_number or inv.invoice_number
                    return _pdf_response(pdf, f'{display_number}-preview.pdf', action == 'download')
                try:
                    invoice = services.save_invoice(data, invoice)
                except (ValidationError, IntegrityError) as e:
                    if isinstance(e, IntegrityError):
                        messages.error(request, 'A deployment for this billing period was invoiced by another open request. Refresh and review the period before saving.')
                        invoice = Invoice.objects.get(pk=invoice.pk) if invoice else None
                        return redirect('invoice_edit', pk=invoice.pk) if invoice else redirect('new_invoice')
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
                   'service_type': data.service_type, 'resource_display_format': data.resource_display_format,
                   'billing_period_start': data.billing_period_start, 'billing_period_end': data.billing_period_end,
                   'supplementary': data.supplementary,
                   'lines': [dict(project=l.project.pk if l.project else '', name=l.project_name, desc=l.description,
                                  start=l.from_date, end=l.to_date, days=l.days,
                                  unit=l.unit_override if l.unit_override is not None else '',
                                  amount=l.amount_override if l.amount_override is not None else '',
                                  deployment=l.deployment.pk if l.deployment else '', method=l.billing_method,
                                  placement=l.placement.pk if l.placement else '', remarks=l.line_remarks) for l in data.lines]}
    elif invoice is not None:
        initial = {'client': invoice.client_id, 'invoice_date': invoice.invoice_date, 'terms_mode': invoice.terms_mode,
                   'terms_text': invoice.terms_display,
                   'due_date': invoice.due_date, 'pdf_number_override': invoice.pdf_number_override,
                   **_ym_parts(invoice.billing_month),
                   'tax_treatment': invoice.tax_treatment, 'place_of_supply': invoice.place_of_supply,
                   'po_number': invoice.po_number,
                   'format_template': (str(invoice.format_template_id) if invoice.format_template_id and
                                       invoice.format_template_id != invoice.client.invoice_format_id else ''),
                   'service_type': invoice.service_type,
                   'resource_display_format': invoice.resource_display_format,
                   'billing_period_start': invoice.billing_period_start, 'billing_period_end': invoice.billing_period_end,
                   'supplementary': invoice.supplementary,
                   'lines': [dict(project=l.project_id or '', name=l.project_name, desc=l.description, start=l.from_date,
                                  end=l.to_date, days=l.quantity_days, unit='' if (l.project_id or l.unit_price is None) else l.unit_price,
                                  amount=l.amount if (l.amount_overridden or (not l.project_id and not l.recruitment_placement_id)) else '',
                                  deployment=l.manpower_deployment_id or '', method=l.billing_method_snapshot,
                                  placement=l.recruitment_placement_id or '',
                                  remarks='')
                             for l in invoice.lines.all()]}
    else:
        from django.utils import timezone
        today = timezone.localdate()
        initial = {'client': request.GET.get('client', ''), 'invoice_date': today,
                   'terms_mode': 'custom', 'terms_text': str(profile.payment_terms_days),
                   'profile_terms': str(profile.payment_terms_days),
                   'due_date': billing.due_date_for(today, profile.payment_terms_days),
                   'service_type': request.GET.get('service_type', Invoice.PROJECT_MANPOWER),
                   'resource_display_format': profile.manpower_resource_display_default,
                   **_ym_parts(services.previous_month(today)), 'lines': []}
        requested_month = None
        try:
            if request.GET.get('billing_year') and request.GET.get('billing_mon'):
                requested_month = services.parse_month(f"{request.GET['billing_year']}-{request.GET['billing_mon']}")
                initial.update(_ym_parts(requested_month))
        except ValueError:
            requested_month = None
        if initial['service_type'] == Invoice.GENERAL_MANPOWER and str(initial['client']).isdigit():
            selected_deployments = ManpowerDeployment.objects.select_related('employee').filter(
                client_id=initial['client'], status='active')
            requested_id = request.GET.get('deployment')
            if requested_id and requested_id.isdigit():
                selected_deployments = selected_deployments.filter(pk=requested_id)
            elif request.GET.get('recurring') == '1':
                selected_deployments = selected_deployments.filter(recurring=True)
            else:
                selected_deployments = selected_deployments.none()
            presets = []
            selected_deployments = list(selected_deployments)
            for d in selected_deployments:
                if requested_month:
                    import calendar
                    month_end = date(requested_month.year, requested_month.month,
                                     calendar.monthrange(requested_month.year, requested_month.month)[1])
                    if d.start_date > month_end or (d.end_date and d.end_date < requested_month):
                        continue
                    start, end = max(d.start_date, requested_month), min(d.end_date or month_end, month_end)
                else:
                    start, end = d.start_date, d.end_date
                presets.append({'deployment': d.pk, 'name': d.designation,
                                'start': start.isoformat() if start else '', 'end': end.isoformat() if end else '',
                                'days': '1' if d.billing_method in (ManpowerDeployment.MONTHLY, ManpowerDeployment.FIXED) else '',
                                'unit': str(d.billing_rate), 'method': d.billing_method,
                                'proration': d.proration_method})
            initial['lines'] = presets
            preset_ids = {row['deployment'] for row in presets}
            po_values = {d.po_number for d in selected_deployments if d.pk in preset_ids and d.po_number}
            if len(po_values) == 1:
                initial['po_number'] = po_values.pop()
        elif initial['service_type'] == Invoice.RECRUITMENT_PLACEMENT and str(initial['client']).isdigit():
            requested_id = request.GET.get('placement')
            selected_placements = RecruitmentPlacement.objects.select_related('candidate', 'agreement').filter(
                client_id=initial['client'], fee_approved=True, invoice__isnull=True)
            if requested_id and requested_id.isdigit():
                selected_placements = selected_placements.filter(pk=requested_id)
            else:
                selected_placements = selected_placements.none()
            selected_placements = list(selected_placements)
            initial['lines'] = [{'placement': p.pk, 'name': p.candidate_name_snapshot or p.candidate.candidate_name,
                                 'days': '1', 'description': ''} for p in selected_placements]
            display_values = {p.invoice_display_format for p in selected_placements}
            if len(display_values) == 1:
                initial['resource_display_format'] = display_values.pop()
            if len(selected_placements) == 1 and selected_placements[0].agreement.payment_terms:
                initial['terms_mode'] = 'custom'
                initial['terms_text'] = selected_placements[0].agreement.payment_terms
    js_init = json.dumps({**initial, 'profile_terms': str(profile.payment_terms_days), 'lines': initial.get('lines', [])}, default=str)
    from attendance.models import AttendanceSettings, Holiday
    attendance_schedule = AttendanceSettings.objects.filter(pk=1).first() or AttendanceSettings()
    work_calendar = {'offDays': sorted(attendance_schedule.off_weekdays),
                     'offSaturdays': sorted(attendance_schedule.off_saturday_numbers),
                     'holidays': [d.isoformat() for d in Holiday.objects.filter(optional=False).values_list('date', flat=True)]}
    return render(request, 'invoice_app/invoice_form.html', {
        'invoice': invoice, 'profile': profile, 'clients': all_clients,
        'next_number': invoice.invoice_number if invoice else profile.next_number(),
        'projects_json': json.dumps(_projects_json(all_clients, keep_ids, keep_placement_ids)), 'init_json': js_init,
        'rounding': profile.amount_rounding, 'initial': initial,
        'year_options': _year_options(initial.get('billing_year')),
        'mon_options': [(f'{i:02d}', n) for i, n in enumerate(MONTH_NAMES, 1)],
        'dup_names': _dup_names(all_clients), 'tax_types': Invoice.TAX_TYPES,
        'service_types': Invoice.SERVICE_TYPES, 'resource_displays': Invoice.RESOURCE_DISPLAY,
        'formats': InvoiceFormatTemplate.objects.filter(
            Q(is_active=True) | Q(pk=invoice.format_template_id if invoice and invoice.format_template_id else 0)),
        'format_locked': bool(invoice and invoice.status != Invoice.DRAFT),
        'client_formats_json': json.dumps({str(c.pk): c.invoice_format.name if c.invoice_format_id else ''
                                           for c in all_clients.select_related('invoice_format')}),
        'work_calendar_json': json.dumps(work_calendar),
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
    period = (request.GET.get('period') or '').strip()
    year = (request.GET.get('year') or '').strip()
    month = (request.GET.get('month') or '').strip()
    financial_year = (request.GET.get('financial_year') or '').strip()
    financial_year_options = reporting_periods.fy_choices(back=4, forward=0)
    today = timezone.localdate()
    current_month = today.replace(day=1)
    qs = Invoice.objects.all()
    filter_error = ''
    selected_standard_period = None

    def month_start(offset):
        index = current_month.year * 12 + current_month.month - 1 + offset
        return date(index // 12, index % 12 + 1, 1)

    if request.GET.get('period_mode'):
        selected_standard_period = reporting_periods.resolve(request.GET)
        qs = qs.filter(invoice_date__range=(selected_standard_period.start, selected_standard_period.end))
    elif period in ('last3', 'previous', 'last6'):
        start = month_start({'last3': -2, 'previous': -1, 'last6': -5}[period])
        end = month_start(0 if period == 'previous' else 1)
        qs = qs.filter(invoice_date__gte=start, invoice_date__lt=end)
    elif period == 'financial_year':
        allowed_financial_years = {str(start_year) for start_year, _ in financial_year_options}
        if financial_year not in allowed_financial_years:
            filter_error = 'Choose a financial year from the available options.'
            qs = qs.none()
        else:
            start_year = int(financial_year)
            qs = qs.filter(
                invoice_date__gte=date(start_year, 4, 1),
                invoice_date__lt=date(start_year + 1, 4, 1),
            )
    elif period == 'year_month':
        try:
            selected_year = int(year)
            if not 1 <= selected_year <= 9999:
                raise ValueError
            qs = qs.filter(invoice_date__year=selected_year)
            if month:
                selected_month = int(month)
                if not 1 <= selected_month <= 12:
                    raise ValueError
                qs = qs.filter(invoice_date__month=selected_month)
        except (TypeError, ValueError):
            filter_error = 'Choose a valid year and month.'
            qs = qs.none()
    elif period not in ('', 'all'):
        filter_error = 'Choose a valid invoice date filter.'
        qs = qs.none()

    years = {year_date.year for year_date in Invoice.objects.dates('invoice_date', 'year')}
    years.add(today.year)
    if year.isdigit():
        years.add(int(year))
    selected_month_label = (
        month_name[int(month)] if month.isdigit() and 1 <= int(month) <= 12 else ''
    )
    financial_year_label = (
        reporting_periods.fy_label(int(financial_year))
        if financial_year.isdigit() and financial_year in {
            str(start_year) for start_year, _ in financial_year_options
        } else ''
    )
    return render(request, 'invoice_app/invoice_list.html', {
        'invoices': _with_pay(qs.order_by('-invoice_date', '-id')),
        'period': period or 'all',
        'year': year,
        'month': month,
        'year_options': sorted(years, reverse=True),
        'month_options': [(f'{i:02d}', month_name[i]) for i in range(1, 13)],
        'financial_year_options': financial_year_options,
        'financial_year': financial_year,
        'financial_year_label': financial_year_label,
        'selected_month_label': selected_month_label,
        'filter_error': filter_error,
        'selected_standard_period': selected_standard_period,
        'empty_text': 'No invoices match the selected date filter.' if period not in ('', 'all')
        else 'No invoices yet.',
    })


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    form = PdfOverrideForm(request.POST or None, instance=invoice)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved_invoice = form.save()
            if saved_invoice.pdf_number_override:
                Client.objects.filter(pk=saved_invoice.client_id).update(
                    last_pdf_ref_no=saved_invoice.pdf_number_override,
                )
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
    selected_period = reporting_periods.resolve(g) if g.get('period_mode') else None
    if selected_period:
        qs = qs.filter(receipt_date__range=(selected_period.start, selected_period.end))
    elif f['from']:
        qs = qs.filter(receipt_date__gte=f['from'])
    if f['to'] and not selected_period:
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
        'selected_period': selected_period,
        'tot_amount': sum((r.amount for r in rows), 0), 'tot_ded': sum((r.deductions for r in rows), 0)})


def invoice_pdf(request, pk, manual_number=None):
    invoice = get_object_or_404(Invoice.objects.select_related('client'), pk=pk)
    template, suffix = None, ''
    if request.GET.get('format'):
        # Ad-hoc "download in another format": uses the template's current settings; nothing is saved.
        template = get_object_or_404(InvoiceFormatTemplate, pk=request.GET['format'] if
                                     request.GET['format'].isdigit() else 0, is_active=True)
        suffix = f'-{template.code}'
    pdf = render_for_invoice(invoice, BusinessProfile.current(), template=template)
    return _pdf_response(
        pdf, f'{invoice.invoice_number}{suffix}.pdf', request.GET.get('download') == '1',
    )


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


def email_templates(request):
    """Shared master list of email subject/body templates (invoice + salary)."""
    from django.db.models import Count
    from .invoice_email import PLACEHOLDERS
    from .models import InvoiceEmailDefaults
    qs = InvoiceEmailTemplate.objects.annotate(
        n_subj=Count('clients_using_as_subject', distinct=True),
        n_body=Count('clients_using_as_body', distinct=True),
    )
    kind = (request.GET.get('kind') or 'all').strip().lower()
    if kind in (InvoiceEmailTemplate.SUBJECT, InvoiceEmailTemplate.BODY):
        qs = qs.filter(kind=kind)
    else:
        kind = 'all'
    category = (request.GET.get('category') or 'all').strip().lower()
    if category in (
        InvoiceEmailTemplate.COMMON,
        InvoiceEmailTemplate.CUSTOMER,
        InvoiceEmailTemplate.SALARY,
    ):
        qs = qs.filter(category=category)
    else:
        category = 'all'
    qs = qs.order_by('category', 'kind', 'name', 'id')
    defaults = InvoiceEmailDefaults.current()
    return render(request, 'invoice_app/email_templates.html', {
        'templates': qs,
        'kind': kind,
        'category': category,
        'placeholders': PLACEHOLDERS,
        'salary_subject_id': defaults.salary_subject_template_id,
        'salary_body_id': defaults.salary_body_template_id,
    })


def email_template_edit(request, pk=None):
    obj = get_object_or_404(InvoiceEmailTemplate, pk=pk) if pk else None
    form = InvoiceEmailTemplateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Email template saved.')
        return redirect('email_templates')
    return render(request, 'invoice_app/form_page.html', {
        'form': form,
        'title': 'Edit email template' if obj else 'Add email template',
        'back': 'email_templates',
    })


@require_POST
def email_template_delete(request, pk):
    from django.db.models import Q
    from .models import InvoiceEmailDefaults
    t = get_object_or_404(InvoiceEmailTemplate, pk=pk)
    defaults = InvoiceEmailDefaults.current()
    if (
        defaults.salary_subject_template_id == t.pk
        or defaults.salary_body_template_id == t.pk
    ):
        messages.error(
            request,
            '"{}" is the salary-slip default and cannot be deleted. '
            'Choose another salary default first.'.format(t.name),
        )
        return redirect('email_templates')
    in_use = Client.objects.filter(
        Q(invoice_email_subject_template=t) | Q(invoice_email_body_template=t)
    ).count()
    if in_use:
        messages.error(
            request,
            '"{}" is assigned to {} customer(s) and cannot be deleted. '
            "Clear or change those customers' invoice email template first.".format(t.name, in_use),
        )
        return redirect('email_templates')
    changed = False
    if defaults.subject_template_id == t.pk:
        defaults.subject_template = None
        changed = True
    if defaults.body_template_id == t.pk:
        defaults.body_template = None
        changed = True
    if changed:
        defaults.save()
    name = t.name
    t.delete()
    messages.success(request, f'Email template "{name}" deleted.')
    return redirect('email_templates')


@require_POST
def email_template_set_salary_default(request, pk):
    from .models import InvoiceEmailDefaults
    t = get_object_or_404(InvoiceEmailTemplate, pk=pk)
    if t.category not in (InvoiceEmailTemplate.COMMON, InvoiceEmailTemplate.SALARY):
        messages.error(
            request,
            'Only Common or Salary templates can be the salary-slip default.',
        )
        return redirect('email_templates')
    defaults = InvoiceEmailDefaults.current()
    if t.kind == InvoiceEmailTemplate.SUBJECT:
        defaults.salary_subject_template = t
    else:
        defaults.salary_body_template = t
    defaults.save()
    messages.success(
        request,
        f'"{t.name}" is now the salary-slip {t.get_kind_display().lower()} default.',
    )
    return redirect('email_templates')


# Back-compat aliases used by any leftover imports
invoice_email_templates = email_templates
invoice_email_template_edit = email_template_edit
invoice_email_template_delete = email_template_delete



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


def geo_states(request):
    """JSON options for the State dropdown of one country."""
    from django.http import JsonResponse
    from .models import State
    cid = request.GET.get('country') or ''
    rows = list(State.objects.filter(country_id=cid).values_list('pk', 'name')) if cid.isdigit() else []
    return JsonResponse({'options': rows})


def geo_districts(request):
    """JSON options for the District dropdown of one state."""
    from django.http import JsonResponse
    from .models import District
    sid = request.GET.get('state') or ''
    rows = list(District.objects.filter(state_id=sid).values_list('pk', 'name')) if sid.isdigit() else []
    return JsonResponse({'options': rows})


def business_logo(request):
    """Serve the header logo from the business profile (public display image for this local app)."""
    import mimetypes
    from django.http import FileResponse, Http404
    profile = BusinessProfile.objects.filter(pk=1).first()
    f = profile.business_logo if profile else None
    if not f or not f.storage.exists(f.name):
        raise Http404
    ext = f.name.rsplit('.', 1)[-1].lower()
    ctype = {'svg': 'image/svg+xml', 'webp': 'image/webp', 'png': 'image/png'}.get(ext) or \
        mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
    resp = FileResponse(f.open('rb'), content_type=ctype)
    resp['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'max-age=86400'
    return resp
