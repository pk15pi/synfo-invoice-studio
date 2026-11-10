from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from employee_management.security import local_only

from . import services
from .forms import (
    AssetForm, AssignmentForm, LeaseForm, LeaseStatusForm, MaintenanceForm, MaintenanceUpdateForm, ReturnForm,
)
from .models import (
    Asset, AssetAssignment, AssetCategory, AssetEvent, AssetLease, AssetMaintenance,
)

FORM_PAGE = 'invoice_app/form_page.html'


def _form_page(request, form, title, back_url):
    return render(request, FORM_PAGE, {'form': form, 'title': title, 'back_url': back_url})


def _comma_list(value):
    return [part.strip() for part in (value or '').split(',') if part.strip()]


@local_only
def dashboard(request):
    return render(request, 'asset_management/dashboard.html', {
        'asset_count': Asset.objects.count(),
        'available_count': Asset.objects.filter(current_status=Asset.AVAILABLE).count(),
        'assigned_count': AssetAssignment.objects.filter(status=AssetAssignment.ACTIVE).count(),
        'company_count': Asset.objects.filter(current_status=Asset.COMPANY_STATUS).count(),
        'maintenance_count': AssetMaintenance.objects.filter(status__in=['reported', 'in_progress']).count(),
        'lease_count': AssetLease.objects.filter(status=AssetLease.ACTIVE).count(),
        'category_count': AssetCategory.objects.filter(is_active=True).count(),
        'recent_events': AssetEvent.objects.select_related(
            'asset', 'from_employee', 'to_employee',
        )[:8],
    })


@local_only
def register(request):
    assets = Asset.objects.select_related('category', 'current_employee', 'current_location')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        assets = assets.filter(
            Q(asset_code__icontains=search) | Q(name__icontains=search)
            | Q(serial_number__icontains=search) | Q(brand__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(Asset.STATUSES):
        assets = assets.filter(current_status=status)
    return render(request, 'asset_management/register.html', {
        'assets': assets, 'search': search, 'status': status, 'statuses': Asset.STATUSES,
    })


@local_only
def asset_new(request):
    form = AssetForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            asset = form.save(commit=False)
            asset.asset_code = services.allocate_asset_code(asset.category)
            asset.current_status = (
                Asset.COMPANY_STATUS if asset.usage_type == Asset.COMPANY_USE else Asset.AVAILABLE
            )
            asset.save()
            services.record_event(
                asset, 'created', new_status=asset.current_status, remarks=f'Registered {asset.name}.',
            )
        messages.success(request, f'{asset.asset_code} registered.')
        return redirect('assets:asset_detail', pk=asset.pk)
    return _form_page(request, form, 'Register an asset', reverse('assets:register'))


@local_only
def asset_detail(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related('category', 'current_employee', 'current_location'), pk=pk,
    )
    return render(request, 'asset_management/detail.html', {
        'asset': asset,
        'active_assignment': asset.assignments.select_related('employee').filter(
            status=AssetAssignment.ACTIVE,
        ).first(),
        'assignments': asset.assignments.select_related('employee')[:20],
        'leases': asset.leases.all()[:20],
        'maintenance_records': asset.maintenance_records.all()[:20],
        'events': asset.events.select_related('from_employee', 'to_employee')[:20],
    })


@local_only
def assignments(request):
    rows = AssetAssignment.objects.select_related('asset', 'employee')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(asset__asset_code__icontains=search) | Q(asset__name__icontains=search)
            | Q(employee__employee_id__icontains=search) | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(AssetAssignment.STATUSES):
        rows = rows.filter(status=status)
    return render(request, 'asset_management/assignments.html', {
        'assignments': rows, 'search': search, 'status': status, 'statuses': AssetAssignment.STATUSES,
    })


@local_only
def assignment_new(request):
    initial = {}
    asset_id = request.GET.get('asset', '')
    if asset_id.isdigit():
        initial['asset'] = Asset.objects.filter(pk=asset_id).first()
    form = AssignmentForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        assignment = form.save(commit=False)
        try:
            with transaction.atomic():
                services.assign_asset(
                    assignment,
                    acknowledgement_type=form.cleaned_data.get('acknowledgement_type') or '',
                    acknowledgement_name=form.cleaned_data.get('acknowledgement_name') or '',
                    confirmation_text=form.cleaned_data.get('confirmation_text') or '',
                    reference_id=services.next_handover_code(),
                )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(
                request, f'{assignment.asset.asset_code} assigned to {assignment.employee}.',
            )
            return redirect('assets:assignments')
    return _form_page(request, form, 'Assign an asset', reverse('assets:assignments'))


@local_only
def assignment_return(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('asset', 'employee'), pk=pk,
    )
    if assignment.status != AssetAssignment.ACTIVE:
        messages.info(request, 'This assignment has already been returned.')
        return redirect('assets:assignments')
    form = ReturnForm(request.POST or None, instance=assignment)
    if request.method == 'POST' and form.is_valid():
        try:
            services.return_assignment(
                assignment,
                actual_return_date=form.cleaned_data['actual_return_date'],
                return_condition=form.cleaned_data.get('return_condition') or '',
                accessories_returned=_comma_list(form.cleaned_data.get('returned_accessories')),
                return_notes=form.cleaned_data.get('return_notes') or '',
            )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(
                request, f'{assignment.asset.asset_code} returned by {assignment.employee}.',
            )
            return redirect('assets:assignments')
    return _form_page(
        request, form, f'Return {assignment.asset.asset_code}', reverse('assets:assignments'),
    )


@local_only
def leases(request):
    rows = AssetLease.objects.select_related('asset')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(asset__asset_code__icontains=search) | Q(asset__name__icontains=search)
            | Q(lessor_name__icontains=search) | Q(agreement_number__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(AssetLease.STATUSES):
        rows = rows.filter(status=status)
    return render(request, 'asset_management/leases.html', {
        'leases': rows, 'search': search, 'status': status, 'statuses': AssetLease.STATUSES,
    })


@local_only
def lease_new(request):
    initial = {}
    asset_id = request.GET.get('asset', '')
    if asset_id.isdigit():
        initial['asset'] = Asset.objects.filter(pk=asset_id).first()
    form = LeaseForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            lease = form.save(commit=False)
            services.start_lease(lease)
        messages.success(request, f'Lease with {lease.lessor_name} recorded.')
        return redirect('assets:leases')
    return _form_page(request, form, 'Record a lease', reverse('assets:leases'))


@local_only
def lease_update(request, pk):
    lease = get_object_or_404(AssetLease.objects.select_related('asset'), pk=pk)
    form = LeaseStatusForm(request.POST or None, initial={'status': lease.status})
    if request.method == 'POST' and form.is_valid():
        try:
            services.set_lease_status(
                lease, form.cleaned_data['status'], form.cleaned_data.get('remarks') or '',
            )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'Lease with {lease.lessor_name} updated.')
            return redirect('assets:leases')
    return _form_page(
        request, form, f'Update lease \u2013 {lease.asset.asset_code}', reverse('assets:leases'),
    )


@local_only
def maintenance(request):
    rows = AssetMaintenance.objects.select_related('asset')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(asset__asset_code__icontains=search) | Q(asset__name__icontains=search)
            | Q(service_provider__icontains=search) | Q(issue_description__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(AssetMaintenance.STATUSES):
        rows = rows.filter(status=status)
    return render(request, 'asset_management/maintenance.html', {
        'records': rows, 'search': search, 'status': status, 'statuses': AssetMaintenance.STATUSES,
    })


@local_only
def maintenance_new(request):
    initial = {}
    asset_id = request.GET.get('asset', '')
    if asset_id.isdigit():
        initial['asset'] = Asset.objects.filter(pk=asset_id).first()
    form = MaintenanceForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            record = form.save(commit=False)
            services.report_maintenance(record)
        messages.success(request, f'Maintenance reported for {record.asset.asset_code}.')
        return redirect('assets:maintenance')
    return _form_page(request, form, 'Report maintenance', reverse('assets:maintenance'))


@local_only
def maintenance_update(request, pk):
    record = get_object_or_404(AssetMaintenance.objects.select_related('asset'), pk=pk)
    form = MaintenanceUpdateForm(request.POST or None, instance=record)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            services.update_maintenance(form.save(commit=False))
        messages.success(request, f'Maintenance for {record.asset.asset_code} updated.')
        return redirect('assets:maintenance')
    return _form_page(
        request, form, f'Update maintenance \u2013 {record.asset.asset_code}', reverse('assets:maintenance'),
    )
