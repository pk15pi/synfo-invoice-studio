from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from employee_management.security import local_only

from . import receipt_email, services
from .forms import (
    AssetCategoryForm, AssetForm, AssetLocationForm, AssetReceiptForm, AssignmentForm, ConsumableIssueForm,
    ConsumableItemForm, ConsumableStockInForm, DisposalForm, LeaseForm, LeaseStatusForm, LostForm,
    MaintenanceForm, MaintenanceUpdateForm, ReturnForm, TransferForm,
)
from .models import (
    Asset, AssetAssignment, AssetCategory, AssetEvent, AssetLease, AssetLocation, AssetMaintenance,
    ConsumableItem, ConsumableMovement,
)
from .receipt_pdf import receipt_number

FORM_PAGE = 'invoice_app/form_page.html'


def _form_page(request, form, title, back_url):
    return render(request, FORM_PAGE, {'form': form, 'title': title, 'back_url': back_url})


def _comma_list(value):
    return [part.strip() for part in (value or '').split(',') if part.strip()]


@local_only
def dashboard(request):
    return render(request, 'asset_management/dashboard.html', {
        'asset_count': Asset.objects.count(),
        'fixed_count': Asset.objects.filter(category__item_class=AssetCategory.FIXED).count(),
        'bulk_count': Asset.objects.filter(category__item_class=AssetCategory.BULK).count(),
        'available_count': Asset.objects.filter(current_status=Asset.AVAILABLE).count(),
        'assigned_count': AssetAssignment.objects.filter(status=AssetAssignment.ACTIVE).count(),
        'company_count': Asset.objects.filter(current_status=Asset.COMPANY_STATUS).count(),
        'maintenance_count': AssetMaintenance.objects.filter(status__in=['reported', 'in_progress']).count(),
        'lease_count': AssetLease.objects.filter(status=AssetLease.ACTIVE).count(),
        'category_count': AssetCategory.objects.filter(is_active=True).count(),
        'consumable_count': ConsumableItem.objects.filter(is_active=True).count(),
        'low_stock_count': ConsumableItem.objects.filter(
            is_active=True, reorder_level__gt=0, current_stock__lte=F('reorder_level'),
        ).count(),
        'recent_events': AssetEvent.objects.select_related(
            'asset', 'from_employee', 'to_employee',
        )[:8],
    })


@local_only
def register(request):
    assets = Asset.objects.select_related(
        'category', 'current_employee', 'current_location',
    ).annotate(
        active_unit_count=Count(
            'assignments', filter=Q(assignments__status=AssetAssignment.ACTIVE),
        ),
    )
    search = request.GET.get('q', '').strip()[:100]
    if search:
        assets = assets.filter(
            Q(asset_code__icontains=search) | Q(name__icontains=search)
            | Q(serial_number__icontains=search) | Q(brand__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(Asset.STATUSES):
        assets = assets.filter(current_status=status)
    item_class = request.GET.get('item_class', '')
    if item_class in (AssetCategory.FIXED, AssetCategory.BULK):
        assets = assets.filter(category__item_class=item_class)
    category_id = request.GET.get('category', '')
    if category_id.isdigit():
        assets = assets.filter(category_id=int(category_id))
    return render(request, 'asset_management/register.html', {
        'assets': assets, 'search': search, 'status': status, 'statuses': Asset.STATUSES,
        'item_class': item_class,
        'item_classes': [(AssetCategory.FIXED, 'Fixed / capital assets'), (AssetCategory.BULK, 'Bulk returnable')],
        'category_id': category_id,
        'categories': AssetCategory.objects.filter(is_active=True).exclude(item_class=AssetCategory.CONSUMABLE),
    })


@local_only
def locations(request):
    rows = AssetLocation.objects.select_related('parent')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(name__icontains=search) | Q(address__icontains=search) | Q(parent__name__icontains=search)
        )
    location_type = request.GET.get('type', '')
    if location_type in dict(AssetLocation.LOCATION_TYPES):
        rows = rows.filter(location_type=location_type)
    return render(request, 'asset_management/locations.html', {
        'locations': rows, 'search': search, 'location_type': location_type,
        'location_types': AssetLocation.LOCATION_TYPES,
    })


@local_only
def location_new(request):
    form = AssetLocationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        location = form.save()
        messages.success(request, f'Location {location.name} added.')
        return redirect('assets:locations')
    return _form_page(request, form, 'Add a location', reverse('assets:locations'))


@local_only
def location_edit(request, pk):
    location = get_object_or_404(AssetLocation, pk=pk)
    form = AssetLocationForm(request.POST or None, instance=location)
    if request.method == 'POST' and form.is_valid():
        location = form.save()
        messages.success(request, f'Location {location.name} updated.')
        return redirect('assets:locations')
    return _form_page(
        request, form, f'Edit {location.name}', reverse('assets:locations'),
    )


@local_only
def asset_new(request):
    form = AssetForm(request.POST or None, item_class=request.GET.get('item_class', ''))
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
        Asset.objects.select_related('category', 'current_employee', 'current_location').annotate(
            active_unit_count=Count(
                'assignments', filter=Q(assignments__status=AssetAssignment.ACTIVE),
            ),
        ),
        pk=pk,
    )
    active_assignments = list(
        asset.assignments.select_related('employee', 'parent_assignment__asset').filter(
            status=AssetAssignment.ACTIVE,
        )
    )
    active_assignment = active_assignments[0] if active_assignments else None
    latest_assignment = active_assignment or asset.assignments.select_related('employee').first()
    return render(request, 'asset_management/detail.html', {
        'asset': asset,
        'active_assignment': active_assignment,
        'active_assignments': active_assignments,
        'latest_assignment': latest_assignment,
        'accessory_assignments': (
            AssetAssignment.objects.filter(parent_assignment__in=active_assignments)
            .select_related('asset')
            if active_assignments else []
        ),
        'receipts': services.receipts_for(latest_assignment) if latest_assignment else [],
        'assignments': asset.assignments.select_related('employee')[:20],
        'leases': asset.leases.all()[:20],
        'maintenance_records': asset.maintenance_records.all()[:20],
        'events': asset.events.select_related('from_employee', 'to_employee')[:20],
    })


def _edit_locked_message(asset):
    active = asset.assignments.filter(status=AssetAssignment.ACTIVE).select_related('employee').first()
    if active:
        return (
            f'{asset.asset_code} is issued to {active.employee}. '
            'Return it to the company before editing.'
        )
    return (
        f'{asset.asset_code} is {asset.get_current_status_display().lower()}; '
        'it can be edited only while it is with the company.'
    )


@local_only
def asset_edit(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related('category', 'current_employee', 'current_location'), pk=pk,
    )
    if not asset.is_editable():
        messages.error(request, _edit_locked_message(asset))
        return redirect('assets:asset_detail', pk=asset.pk)
    form = AssetForm(request.POST or None, instance=asset)
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_asset(form)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'{asset.asset_code} updated.')
            return redirect('assets:asset_detail', pk=asset.pk)
    return _form_page(
        request, form, f'Edit {asset.asset_code}', reverse('assets:asset_detail', args=(asset.pk,)),
    )


@local_only
def assignments(request):
    rows = AssetAssignment.objects.select_related('asset', 'employee', 'parent_assignment__asset')
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
    form = AssignmentForm(request.POST or None, request.FILES or None, initial=initial)
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
                    accessory_assets=form.cleaned_data.get('accessory_assets') or (),
                )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            accessory_count = len(form.cleaned_data.get('accessory_assets') or ())
            extra = f' with {accessory_count} accessory asset(s).' if accessory_count else '.'
            messages.success(
                request, f'{assignment.asset.asset_code} assigned to {assignment.employee}{extra}',
            )
            receipt = form.cleaned_data.get('receipt')
            if receipt:
                try:
                    services.upload_assignment_receipt(assignment, receipt)
                except ValidationError as exc:
                    messages.warning(
                        request,
                        f'Assignment saved, but the receipt was not uploaded: {" ".join(exc.messages)}',
                    )
            return redirect(f"{reverse('assets:assignment_receipt', args=(assignment.pk,))}?confirm=1")
    return _form_page(request, form, 'Assign an asset', reverse('assets:assignments'))


@local_only
def assignment_receipt(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('asset', 'employee'), pk=pk,
    )
    upload_form = AssetReceiptForm(request.POST or None, request.FILES or None)
    confirm = request.GET.get('confirm') == '1'
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'send':
            sent, error = receipt_email.send_handover_receipt(assignment)
            if sent:
                messages.success(
                    request, f'Handover receipt emailed to {assignment.receipt_email_to}.',
                )
                return redirect('assets:assignments')
            messages.error(request, f'The receipt could not be emailed: {error}')
            confirm = True
        elif action == 'skip':
            messages.info(
                request, 'Receipt email skipped. Use the Receipt link on the assignment to send it later.',
            )
            return redirect('assets:assignments')
        elif upload_form.is_valid():
            try:
                services.upload_assignment_receipt(
                    assignment, upload_form.cleaned_data['file'],
                    note=upload_form.cleaned_data.get('note') or '',
                )
            except ValidationError as exc:
                upload_form.add_error('file', exc.messages)
            else:
                messages.success(request, 'Receipt uploaded, stored privately and indexed in Documents.')
                return redirect('assets:assignment_receipt', pk=assignment.pk)
    return render(request, 'asset_management/assignment_receipt.html', {
        'assignment': assignment,
        'upload_form': upload_form,
        'receipts': services.receipts_for(assignment),
        'acknowledgements': assignment.acknowledgements.select_related('document'),
        'recipient': receipt_email.employee_email(assignment.employee),
        'confirm': confirm,
        'receipt_number': receipt_number(assignment),
    })


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
def assignment_transfer(request, pk):
    assignment = get_object_or_404(
        AssetAssignment.objects.select_related('asset', 'employee'), pk=pk,
    )
    if assignment.status != AssetAssignment.ACTIVE:
        messages.info(request, 'Only an active assignment can be transferred.')
        return redirect('assets:assignments')
    form = TransferForm(request.POST or None, assignment=assignment)
    if request.method == 'POST' and form.is_valid():
        try:
            new_assignment = services.transfer_asset(
                assignment,
                to_employee=form.cleaned_data['to_employee'],
                transfer_date=form.cleaned_data['transfer_date'],
                expected_return_date=form.cleaned_data.get('expected_return_date'),
                issue_condition=form.cleaned_data.get('issue_condition') or '',
                issue_notes=form.cleaned_data.get('issue_notes') or '',
                acknowledgement_type=form.cleaned_data.get('acknowledgement_type') or '',
                acknowledgement_name=form.cleaned_data.get('acknowledgement_name') or '',
                confirmation_text=form.cleaned_data.get('confirmation_text') or '',
            )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(
                request, f'{assignment.asset.asset_code} transferred to {new_assignment.employee}.',
            )
            return redirect(f"{reverse('assets:assignment_receipt', args=(new_assignment.pk,))}?confirm=1")
    return _form_page(
        request, form, f'Transfer {assignment.asset.asset_code}', reverse('assets:assignments'),
    )


@local_only
def asset_mark_lost(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if asset.current_status == Asset.LOST:
        messages.info(request, f'{asset.asset_code} is already marked as lost.')
        return redirect('assets:asset_detail', pk=asset.pk)
    if asset.current_status == Asset.DISPOSED:
        messages.error(request, 'A disposed asset cannot be marked as lost.')
        return redirect('assets:asset_detail', pk=asset.pk)
    if asset.assignments.filter(status=AssetAssignment.ACTIVE).exists():
        messages.error(request, 'Return all active assignments before marking this asset as lost.')
        return redirect('assets:asset_detail', pk=asset.pk)
    form = LostForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.mark_lost(
                asset,
                loss_date=form.cleaned_data['loss_date'],
                remarks=form.cleaned_data.get('remarks') or '',
            )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'{asset.asset_code} marked as lost.')
            return redirect('assets:asset_detail', pk=asset.pk)
    return _form_page(
        request, form, f'Mark {asset.asset_code} as lost', reverse('assets:asset_detail', args=(asset.pk,)),
    )


@local_only
def asset_dispose(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if asset.current_status == Asset.DISPOSED:
        messages.info(request, f'{asset.asset_code} is already disposed.')
        return redirect('assets:asset_detail', pk=asset.pk)
    if asset.assignments.filter(status=AssetAssignment.ACTIVE).exists():
        messages.error(request, 'Return all active assignments before disposing of this asset.')
        return redirect('assets:asset_detail', pk=asset.pk)
    form = DisposalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.dispose_asset(
                asset,
                disposal_date=form.cleaned_data['disposal_date'],
                disposal_method=form.cleaned_data.get('disposal_method') or '',
                sale_proceeds=form.cleaned_data.get('sale_proceeds'),
                remarks=form.cleaned_data.get('remarks') or '',
            )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'{asset.asset_code} marked as disposed.')
            return redirect('assets:asset_detail', pk=asset.pk)
    return _form_page(
        request, form, f'Dispose {asset.asset_code}', reverse('assets:asset_detail', args=(asset.pk,)),
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


@local_only
def categories(request):
    rows = AssetCategory.objects.all()
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(name__icontains=search) | Q(code__icontains=search) | Q(description__icontains=search)
        )
    item_class = request.GET.get('item_class', '')
    if item_class in dict(AssetCategory.ITEM_CLASSES):
        rows = rows.filter(item_class=item_class)
    return render(request, 'asset_management/categories.html', {
        'categories': rows, 'search': search, 'item_class': item_class,
        'item_classes': AssetCategory.ITEM_CLASSES,
    })


@local_only
def category_new(request):
    form = AssetCategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        category = form.save()
        messages.success(request, f'Category {category.name} added.')
        return redirect('assets:categories')
    return _form_page(request, form, 'Add a category', reverse('assets:categories'))


@local_only
def category_edit(request, pk):
    category = get_object_or_404(AssetCategory, pk=pk)
    form = AssetCategoryForm(request.POST or None, instance=category)
    if request.method == 'POST' and form.is_valid():
        category = form.save()
        messages.success(request, f'Category {category.name} updated.')
        return redirect('assets:categories')
    return _form_page(request, form, f'Edit {category.name}', reverse('assets:categories'))


@local_only
def consumables(request):
    rows = ConsumableItem.objects.select_related('category', 'location')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        rows = rows.filter(
            Q(item_code__icontains=search) | Q(name__icontains=search)
            | Q(vendor_name__icontains=search) | Q(category__name__icontains=search)
        )
    is_active = request.GET.get('active', '')
    if is_active == '1':
        rows = rows.filter(is_active=True)
    elif is_active == '0':
        rows = rows.filter(is_active=False)
    return render(request, 'asset_management/consumables.html', {
        'consumables': rows, 'search': search, 'active': is_active,
    })


@local_only
def consumable_detail(request, pk):
    item = get_object_or_404(
        ConsumableItem.objects.select_related('category', 'location'), pk=pk,
    )
    movements = item.movements.all()[:50]
    return render(request, 'asset_management/consumable_detail.html', {
        'item': item, 'movements': movements,
    })


@local_only
def consumable_new(request):
    form = ConsumableItemForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                item = services.create_consumable(form.save(commit=False))
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'{item.item_code} added.')
            return redirect('assets:consumable_detail', pk=item.pk)
    return _form_page(request, form, 'Add a consumable', reverse('assets:consumables'))


@local_only
def consumable_edit(request, pk):
    item = get_object_or_404(ConsumableItem, pk=pk)
    form = ConsumableItemForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        messages.success(request, f'{item.item_code} updated.')
        return redirect('assets:consumable_detail', pk=item.pk)
    return _form_page(
        request, form, f'Edit {item.item_code}', reverse('assets:consumable_detail', args=(item.pk,)),
    )


@local_only
def consumable_restock(request, pk):
    item = get_object_or_404(ConsumableItem, pk=pk)
    form = ConsumableStockInForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                services.record_consumable_stock_in(
                    item, quantity=form.cleaned_data['quantity'],
                    movement_date=form.cleaned_data['movement_date'],
                    unit_cost=form.cleaned_data.get('unit_cost'),
                    location=form.cleaned_data.get('location'),
                    reference=form.cleaned_data.get('reference') or '',
                    remarks=form.cleaned_data.get('remarks') or '',
                )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(request, f'Stock increased for {item.item_code}.')
            return redirect('assets:consumable_detail', pk=item.pk)
    return _form_page(
        request, form, f'Restock {item.item_code}', reverse('assets:consumable_detail', args=(item.pk,)),
    )


@local_only
def consumable_issue(request, pk):
    item = get_object_or_404(ConsumableItem, pk=pk)
    form = ConsumableIssueForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                services.issue_consumable(
                    item, quantity=form.cleaned_data['quantity'],
                    movement_date=form.cleaned_data['movement_date'],
                    employee=form.cleaned_data.get('employee'),
                    location=form.cleaned_data.get('location'),
                    reference=form.cleaned_data.get('reference') or '',
                    remarks=form.cleaned_data.get('remarks') or '',
                )
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        else:
            messages.success(
                request, f'{form.cleaned_data["quantity"]} x {item.name} issued.',
            )
            return redirect('assets:consumable_detail', pk=item.pk)
    return _form_page(
        request, form, f'Issue {item.item_code}', reverse('assets:consumable_detail', args=(item.pk,)),
    )
