from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import (
    Asset, AssetAcknowledgement, AssetAssignment, AssetEvent, AssetLease, AssetMaintenance, AssetSettings,
)


def _allocate_sequence(field_name, prefix_field, category_code=''):
    AssetSettings.objects.get_or_create(pk=1)
    with transaction.atomic():
        changed = AssetSettings.objects.filter(pk=1).update(**{field_name: F(field_name) + 1})
        if changed != 1:
            raise RuntimeError('Asset settings are unavailable; could not allocate a number.')
        settings = AssetSettings.objects.get(pk=1)
        sequence = getattr(settings, field_name) - 1
        number = f'{sequence:03d}'
        prefix = getattr(settings, prefix_field)
        if category_code:
            return f'{prefix}-{category_code}-{number}'
        return f'{prefix}-{number}'


def next_asset_code(category):
    return _allocate_sequence('next_asset_sequence', 'asset_prefix', category.code)


def next_handover_code():
    return _allocate_sequence('next_handover_sequence', 'handover_prefix')


def allocate_asset_code(category):
    """Return a generated asset code that is not already in use."""
    for _ in range(1000):
        code = next_asset_code(category)
        if not Asset.objects.filter(asset_code=code).exists():
            return code
    raise RuntimeError('Could not allocate a unique asset code.')


def record_event(asset, event_type, **fields):
    return AssetEvent.objects.create(asset=asset, event_type=event_type, **fields)


def _locked_asset(asset):
    return Asset.objects.select_for_update().get(pk=asset.pk)


@transaction.atomic
def assign_asset(assignment, *, acknowledgement_type='', acknowledgement_name='', confirmation_text='',
                 reference_id=''):
    asset = _locked_asset(assignment.asset)
    if asset.current_status != Asset.AVAILABLE:
        raise ValidationError('Only an available asset can be assigned.')
    if AssetAssignment.objects.filter(asset=asset, status=AssetAssignment.ACTIVE).exists():
        raise ValidationError('This asset already has an active assignment.')
    if acknowledgement_type == AssetAcknowledgement.DIGITAL and not (confirmation_text or '').strip():
        raise ValidationError('A digital acknowledgement needs the employee confirmation text.')

    previous_status = asset.current_status
    assignment.save()
    asset.current_status = Asset.ASSIGNED
    asset.current_employee = assignment.employee
    asset.save(update_fields=['current_status', 'current_employee', 'updated_at'])
    record_event(
        asset, 'assigned', to_employee=assignment.employee, from_location=asset.current_location,
        previous_status=previous_status, new_status=asset.current_status, reference_type='handover',
        reference_id=reference_id, remarks=f'Issued to {assignment.employee}.',
    )
    if acknowledgement_type:
        AssetAcknowledgement.objects.create(
            assignment=assignment, acknowledgement_type=acknowledgement_type,
            acknowledgement_name=(acknowledgement_name or '').strip() or assignment.employee.full_name,
            confirmation_text=(confirmation_text or '').strip(),
        )
    return assignment


@transaction.atomic
def return_assignment(assignment, *, actual_return_date, return_condition='', accessories_returned=None,
                      return_notes=''):
    assignment = AssetAssignment.objects.select_for_update().get(pk=assignment.pk)
    if assignment.status != AssetAssignment.ACTIVE:
        raise ValidationError('This assignment has already been returned.')
    asset = _locked_asset(assignment.asset)
    previous_status = asset.current_status
    assignment.status = AssetAssignment.RETURNED
    assignment.actual_return_date = actual_return_date
    assignment.return_condition = return_condition
    assignment.accessories_returned = accessories_returned or []
    assignment.return_notes = return_notes
    assignment.save()
    asset.current_employee = None
    if asset.current_status not in (Asset.LOST, Asset.DISPOSED):
        asset.current_status = Asset.AVAILABLE
        asset.save(update_fields=['current_status', 'current_employee', 'updated_at'])
    else:
        asset.save(update_fields=['current_employee', 'updated_at'])
    record_event(
        asset, 'returned', from_employee=assignment.employee, previous_status=previous_status,
        new_status=asset.current_status, reference_type='assignment', reference_id=str(assignment.pk),
        remarks=f'Returned by {assignment.employee}.',
    )
    return assignment


@transaction.atomic
def start_lease(lease):
    asset = _locked_asset(lease.asset)
    lease.save()
    if asset.ownership_type != Asset.LEASED:
        asset.ownership_type = Asset.LEASED
        asset.save(update_fields=['ownership_type', 'updated_at'])
    record_event(
        asset, 'lease', previous_status=asset.current_status, new_status=asset.current_status,
        remarks=(
            f'Lease with {lease.lessor_name} from {lease.start_date:%d-%m-%Y} to {lease.end_date:%d-%m-%Y}.'
        ),
    )
    return lease


@transaction.atomic
def set_lease_status(lease, status, remarks=''):
    if status not in dict(AssetLease.STATUSES):
        raise ValidationError('Choose a valid lease status.')
    lease = AssetLease.objects.select_for_update().select_related('asset').get(pk=lease.pk)
    lease.status = status
    lease.save(update_fields=['status', 'updated_at'])
    note = f'Lease status changed to {lease.get_status_display()}.'
    if (remarks or '').strip():
        note = f'{note} {remarks.strip()}'
    record_event(
        lease.asset, 'lease', previous_status=lease.asset.current_status,
        new_status=lease.asset.current_status, reference_type='lease', reference_id=str(lease.pk),
        remarks=note,
    )
    return lease


@transaction.atomic
def report_maintenance(maintenance):
    asset = _locked_asset(maintenance.asset)
    maintenance.save()
    previous_status = asset.current_status
    if asset.current_status not in (Asset.LOST, Asset.DISPOSED):
        asset.current_status = Asset.UNDER_MAINTENANCE
        asset.save(update_fields=['current_status', 'updated_at'])
    record_event(
        asset, 'maintenance', previous_status=previous_status, new_status=asset.current_status,
        reference_type='maintenance', reference_id=str(maintenance.pk),
        remarks=f'Maintenance reported: {maintenance.issue_description}',
    )
    return maintenance


@transaction.atomic
def update_maintenance(maintenance):
    asset = _locked_asset(maintenance.asset)
    previous_status = asset.current_status
    maintenance.save()
    target = None
    if maintenance.status in ('completed', 'cancelled'):
        other_open = AssetMaintenance.objects.filter(
            asset=asset, status__in=['reported', 'in_progress'],
        ).exclude(pk=maintenance.pk).exists()
        if other_open:
            target = Asset.UNDER_MAINTENANCE
        elif asset.current_status not in (Asset.LOST, Asset.DISPOSED):
            target = (
                Asset.ASSIGNED
                if AssetAssignment.objects.filter(asset=asset, status=AssetAssignment.ACTIVE).exists()
                else Asset.AVAILABLE
            )
    elif asset.current_status not in (Asset.LOST, Asset.DISPOSED, Asset.UNDER_MAINTENANCE):
        target = Asset.UNDER_MAINTENANCE
    if target and asset.current_status != target:
        asset.current_status = target
        asset.save(update_fields=['current_status', 'updated_at'])
    record_event(
        asset, 'maintenance', previous_status=previous_status, new_status=asset.current_status,
        reference_type='maintenance', reference_id=str(maintenance.pk),
        remarks=f'Maintenance marked {maintenance.get_status_display()}.',
    )
    return maintenance
