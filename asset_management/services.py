import os
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from document_management.models import Document, full_path, safe_rel_path, sha256_of
from document_management.services import MIME, MAX_BYTES
from employee_management.storage import private_storage
from employee_management.validators import sniff_upload

from .models import (
    Asset, AssetAcknowledgement, AssetAssignment, AssetEvent, AssetLease, AssetMaintenance, AssetSettings,
)

RECEIPT_SOURCE_TYPES = ('asset_receipt', 'asset_handover_receipt')


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


@transaction.atomic
def update_asset(form):
    """Save corrections only while the asset is with the company and not issued."""
    locked = _locked_asset(form.instance)
    if not locked.is_editable():
        raise ValidationError(
            'This asset can only be edited while it is with the company and not issued to an employee.'
        )
    previous_status = locked.current_status
    changed = list(form.changed_data)
    labels = [str(form.fields[name].label) for name in changed if name in form.fields]
    asset = form.save()
    if 'usage_type' in changed:
        target = Asset.COMPANY_STATUS if asset.usage_type == Asset.COMPANY_USE else Asset.AVAILABLE
        if asset.current_status != target:
            asset.current_status = target
            asset.save(update_fields=['current_status', 'updated_at'])
    note = 'Details updated: ' + ', '.join(labels) + '.' if labels else 'Asset details saved.'
    record_event(
        asset, 'updated', previous_status=previous_status, new_status=asset.current_status,
        remarks=note,
    )
    return asset


def receipts_for(assignment):
    return Document.objects.filter(
        source_type__in=RECEIPT_SOURCE_TYPES,
        content_type=ContentType.objects.get_for_model(AssetAssignment),
        object_id=str(assignment.pk),
    ).order_by('-uploaded_at', '-id')


@transaction.atomic
def upload_assignment_receipt(assignment, upload, *, note='', acknowledged_by=''):
    """Store the employee-signed receipt privately, index it and record the acknowledgement."""
    ext = sniff_upload(upload, MAX_BYTES, allowed=('pdf', 'jpg', 'png'))
    rel = f'asset_receipts/{uuid.uuid4().hex}.{ext}'
    upload.seek(0)
    rel = safe_rel_path(private_storage.save(rel, upload))
    now = timezone.now()
    doc = Document.objects.create(
        category='asset',
        title=f'Signed receipt \u2013 {assignment.asset.asset_code} \u2013 {assignment.employee.full_name}'[:200],
        content_type=ContentType.objects.get_for_model(AssetAssignment),
        object_id=str(assignment.pk),
        source_type='asset_receipt',
        source_id=uuid.uuid4().hex,
        file_path=rel,
        original_filename=os.path.basename(upload.name or '')[:255],
        mime_type=MIME[ext],
        sensitivity=Document.NORMAL,
        tags=f'asset,receipt,{assignment.asset.asset_code}'[:240],
        uploaded_at=now,
        size=os.path.getsize(full_path(rel)),
        sha256=sha256_of(rel),
        last_checked=now,
    )
    acknowledgement = assignment.acknowledgements.filter(document__isnull=True).order_by(
        '-acknowledged_at', '-id',
    ).first()
    if acknowledgement:
        acknowledgement.document = doc
        acknowledgement.save(update_fields=['document'])
    else:
        AssetAcknowledgement.objects.create(
            assignment=assignment, acknowledgement_type=AssetAcknowledgement.PHYSICAL,
            acknowledgement_name=(acknowledged_by or '').strip() or assignment.employee.full_name,
            acknowledged_at=now, document=doc, confirmation_text=(note or '').strip(),
        )
    return doc


def _locked_asset(asset):
    return Asset.objects.select_for_update().get(pk=asset.pk)


def _sync_custody(asset):
    """Refresh status and custodian from the units currently issued."""
    active = list(
        AssetAssignment.objects.filter(
            asset=asset, status=AssetAssignment.ACTIVE,
        ).select_related('employee')
    )
    available = max(asset.quantity - len(active), 0)
    fields = []
    if asset.current_status not in (Asset.UNDER_MAINTENANCE, Asset.LOST, Asset.DISPOSED):
        target = Asset.ASSIGNED if active and available == 0 else Asset.AVAILABLE
        if asset.current_status != target:
            asset.current_status = target
            fields.append('current_status')
    employees = {link.employee_id: link.employee for link in active}
    custodian = next(iter(employees.values())) if len(employees) == 1 else None
    if asset.current_employee_id != (custodian.pk if custodian else None):
        asset.current_employee = custodian
        fields.append('current_employee')
    if fields:
        fields.append('updated_at')
        asset.save(update_fields=fields)
    return asset


def _issue_assignment(assignment, *, reference_id, acknowledgement_type, acknowledgement_name,
                      confirmation_text, remarks):
    asset = _locked_asset(assignment.asset)
    if asset.current_status != Asset.AVAILABLE:
        raise ValidationError('Only an available asset can be assigned.')
    if asset.available_quantity < 1:
        if asset.is_serialized:
            raise ValidationError('This asset already has an active assignment.')
        raise ValidationError('All items of this asset are already assigned.')
    if acknowledgement_type == AssetAcknowledgement.DIGITAL and not (confirmation_text or '').strip():
        raise ValidationError('A digital acknowledgement needs the employee confirmation text.')

    previous_status = asset.current_status
    assignment.handover_number = reference_id or ''
    assignment.save()
    _sync_custody(asset)
    record_event(
        asset, 'assigned', to_employee=assignment.employee, from_location=asset.current_location,
        previous_status=previous_status, new_status=asset.current_status, reference_type='handover',
        reference_id=reference_id, remarks=remarks,
    )
    if acknowledgement_type:
        AssetAcknowledgement.objects.create(
            assignment=assignment, acknowledgement_type=acknowledgement_type,
            acknowledgement_name=(acknowledgement_name or '').strip() or assignment.employee.full_name,
            confirmation_text=(confirmation_text or '').strip(),
        )
    return assignment


@transaction.atomic
def assign_asset(assignment, *, acknowledgement_type='', acknowledgement_name='', confirmation_text='',
                 reference_id='', accessory_assets=None):
    """Issue the main asset and any selected register assets to the same employee."""
    _issue_assignment(
        assignment, reference_id=reference_id, acknowledgement_type=acknowledgement_type,
        acknowledgement_name=acknowledgement_name, confirmation_text=confirmation_text,
        remarks=f'Issued to {assignment.employee}.',
    )
    seen = {assignment.asset_id}
    for accessory in accessory_assets or ():
        if accessory.pk in seen:
            raise ValidationError('An asset cannot be both the main asset and an accessory.')
        seen.add(accessory.pk)
        accessory_assignment = AssetAssignment(
            asset=accessory, employee=assignment.employee,
            issue_date=assignment.issue_date,
            expected_return_date=assignment.expected_return_date,
            issue_condition=assignment.issue_condition,
            issue_notes=assignment.issue_notes,
            parent_assignment=assignment,
        )
        _issue_assignment(
            accessory_assignment, reference_id=next_handover_code(),
            acknowledgement_type=acknowledgement_type,
            acknowledgement_name=acknowledgement_name,
            confirmation_text=confirmation_text,
            remarks=(
                f'Issued as an accessory with {assignment.asset.asset_code} '
                f'({assignment.handover_number}).'
            ),
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
    _sync_custody(asset)
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
def transfer_asset(assignment, *, to_employee, transfer_date, issue_condition='', expected_return_date=None,
                   issue_notes='', acknowledgement_type='', acknowledgement_name='', confirmation_text=''):
    """
    Transfer an active assignment from one employee to another in a single atomic step.

    This closes the current assignment (returned) and immediately opens a new one for
    to_employee, recording a 'transferred' event rather than separate returned/assigned events.

    Returns the new AssetAssignment.
    """
    assignment = AssetAssignment.objects.select_for_update().select_related('asset', 'employee').get(
        pk=assignment.pk,
    )
    if assignment.status != AssetAssignment.ACTIVE:
        raise ValidationError('Only an active assignment can be transferred.')
    if assignment.employee_id == to_employee.pk:
        raise ValidationError('The asset is already assigned to this employee.')

    asset = _locked_asset(assignment.asset)
    from_employee = assignment.employee

    # Close the current assignment
    assignment.status = AssetAssignment.RETURNED
    assignment.actual_return_date = transfer_date
    assignment.return_notes = f'Transferred to {to_employee} on {transfer_date}.'
    assignment.save(update_fields=['status', 'actual_return_date', 'return_notes', 'updated_at'])

    # Open the new assignment
    new_assignment = AssetAssignment.objects.create(
        asset=asset,
        employee=to_employee,
        issue_date=transfer_date,
        expected_return_date=expected_return_date,
        issue_condition=issue_condition or assignment.issue_condition,
        issue_notes=(issue_notes or '').strip(),
        handover_number=next_handover_code(),
        status=AssetAssignment.ACTIVE,
    )

    # Sync custody — the asset stays assigned, just to a different person
    _sync_custody(asset)

    # Record a single 'transferred' event
    record_event(
        asset, 'transferred',
        from_employee=from_employee,
        to_employee=to_employee,
        previous_status=Asset.ASSIGNED,
        new_status=asset.current_status,
        reference_type='assignment',
        reference_id=str(new_assignment.pk),
        remarks=f'Transferred from {from_employee} to {to_employee} on {transfer_date}.',
    )

    if acknowledgement_type:
        AssetAcknowledgement.objects.create(
            assignment=new_assignment,
            acknowledgement_type=acknowledgement_type,
            acknowledgement_name=(acknowledgement_name or '').strip() or to_employee.full_name,
            confirmation_text=(confirmation_text or '').strip(),
        )

    return new_assignment


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
            active = AssetAssignment.objects.filter(
                asset=asset, status=AssetAssignment.ACTIVE,
            ).count()
            target = Asset.ASSIGNED if active >= asset.quantity else Asset.AVAILABLE
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


@transaction.atomic
def mark_lost(asset, *, loss_date, remarks=''):
    """Record an asset as lost. Any active assignments must be returned first."""
    asset = _locked_asset(asset)
    if asset.current_status == Asset.LOST:
        raise ValidationError('This asset is already marked as lost.')
    if asset.current_status == Asset.DISPOSED:
        raise ValidationError('A disposed asset cannot be marked as lost.')
    active = AssetAssignment.objects.filter(asset=asset, status=AssetAssignment.ACTIVE).count()
    if active:
        raise ValidationError(
            'Return all active assignments before marking this asset as lost.'
        )
    previous_status = asset.current_status
    asset.current_status = Asset.LOST
    asset.current_employee = None
    asset.save(update_fields=['current_status', 'current_employee', 'updated_at'])
    record_event(
        asset, 'lost',
        event_date=timezone.datetime.combine(loss_date, timezone.datetime.min.time()).replace(
            tzinfo=timezone.get_current_timezone()
        ),
        previous_status=previous_status,
        new_status=Asset.LOST,
        remarks=(remarks or '').strip() or f'Asset {asset.asset_code} marked as lost.',
    )
    return asset


@transaction.atomic
def dispose_asset(asset, *, disposal_date, disposal_method='', sale_proceeds=None, remarks=''):
    """
    Record an asset as disposed.

    disposal_method: one of 'sold', 'scrapped', 'donated', 'transferred', 'other'.
    sale_proceeds: Decimal or None (only relevant when method='sold').
    """
    VALID_METHODS = {'sold', 'scrapped', 'donated', 'transferred', 'other', ''}
    if disposal_method not in VALID_METHODS:
        raise ValidationError(f'Invalid disposal method "{disposal_method}".')
    asset = _locked_asset(asset)
    if asset.current_status == Asset.DISPOSED:
        raise ValidationError('This asset is already disposed.')
    active = AssetAssignment.objects.filter(asset=asset, status=AssetAssignment.ACTIVE).count()
    if active:
        raise ValidationError(
            'Return all active assignments before disposing of this asset.'
        )
    previous_status = asset.current_status
    asset.current_status = Asset.DISPOSED
    asset.current_employee = None
    asset.save(update_fields=['current_status', 'current_employee', 'updated_at'])
    note_parts = [f'Asset {asset.asset_code} disposed.']
    if disposal_method:
        note_parts.append(f'Method: {disposal_method}.')
    if sale_proceeds is not None:
        note_parts.append(f'Sale proceeds: {sale_proceeds}.')
    if (remarks or '').strip():
        note_parts.append(remarks.strip())
    record_event(
        asset, 'disposed',
        event_date=timezone.datetime.combine(disposal_date, timezone.datetime.min.time()).replace(
            tzinfo=timezone.get_current_timezone()
        ),
        previous_status=previous_status,
        new_status=Asset.DISPOSED,
        remarks=' '.join(note_parts),
    )
    return asset
