from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AssetCategory(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'asset categories'

    def __str__(self):
        return f'{self.name} ({self.code})'


class AssetLocation(models.Model):
    LOCATION_TYPES = [
        ('office', 'Office'), ('building', 'Building'), ('floor', 'Floor'),
        ('department', 'Department'), ('room', 'Room'), ('service_centre', 'Service centre'),
        ('vendor', 'Vendor'), ('other', 'Other'),
    ]

    name = models.CharField(max_length=150)
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPES, default='office', db_index=True)
    address = models.TextField(blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='children')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['parent', 'location_type'], name='asset_loc_parent_type_idx')]

    def __str__(self):
        return self.name

    def clean(self):
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({'parent': 'A location cannot be its own parent.'})
        ancestor = self.parent
        seen = set()
        while ancestor:
            if ancestor.pk in seen:
                raise ValidationError({'parent': 'The existing location hierarchy contains a cycle.'})
            seen.add(ancestor.pk)
            if self.pk and ancestor.pk == self.pk:
                raise ValidationError({'parent': 'A location cannot be nested beneath itself.'})
            ancestor = ancestor.parent


class Asset(models.Model):
    OWNED, LEASED, RENTED = 'owned', 'leased', 'rented'
    OWNERSHIP_TYPES = [(OWNED, 'Company owned'), (LEASED, 'Leased'), (RENTED, 'Rented')]
    EMPLOYEE_USE, COMPANY_USE = 'employee', 'company'
    USAGE_TYPES = [(EMPLOYEE_USE, 'Employee use'), (COMPANY_USE, 'Company use')]
    AVAILABLE, ASSIGNED, UNDER_MAINTENANCE, LOST, DISPOSED = (
        'available', 'assigned', 'under_maintenance', 'lost', 'disposed')
    COMPANY_STATUS = 'company_use'
    STATUSES = [
        (AVAILABLE, 'Available'), (ASSIGNED, 'Assigned'), (COMPANY_STATUS, 'Company use'),
        (UNDER_MAINTENANCE, 'Under maintenance'), (LOST, 'Lost'), (DISPOSED, 'Disposed'),
    ]
    EDITABLE_STATUSES = (AVAILABLE, COMPANY_STATUS)
    CONDITIONS = [('new', 'New'), ('good', 'Good'), ('fair', 'Fair'), ('poor', 'Poor'), ('damaged', 'Damaged')]

    asset_code = models.CharField(max_length=60, unique=True)
    name = models.CharField(max_length=180)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='assets')
    brand = models.CharField(max_length=100, blank=True)
    model_number = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=120, blank=True, db_index=True)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    ownership_type = models.CharField(max_length=10, choices=OWNERSHIP_TYPES, default=OWNED, db_index=True)
    usage_type = models.CharField(max_length=10, choices=USAGE_TYPES, default=COMPANY_USE, db_index=True)
    current_status = models.CharField(max_length=24, choices=STATUSES, default=AVAILABLE, db_index=True)
    condition = models.CharField(max_length=12, choices=CONDITIONS, default='good')
    current_employee = models.ForeignKey(
        'employee_management.Employee', null=True, blank=True, on_delete=models.PROTECT,
        related_name='assets_in_custody',
    )
    current_location = models.ForeignKey(
        AssetLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='assets',
    )
    purchase_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    vendor_name = models.CharField(max_length=180, blank=True)
    invoice_number = models.CharField(max_length=100, blank=True)
    warranty_expiry = models.DateField(null=True, blank=True)
    specifications = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['asset_code']
        indexes = [
            models.Index(fields=['category', 'current_status'], name='asset_cat_status_idx'),
            models.Index(fields=['current_employee', 'current_status'], name='asset_emp_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(purchase_price__isnull=True) | Q(purchase_price__gte=0),
                name='asset_purchase_price_nonnegative',
            ),
            models.CheckConstraint(condition=Q(quantity__gte=1), name='asset_quantity_positive'),
        ]

    def __str__(self):
        return f'{self.asset_code} - {self.name}'

    @property
    def is_serialized(self):
        return bool((self.serial_number or '').strip())

    @property
    def available_quantity(self):
        """Units that can still be issued; serialized assets are one item."""
        if self.current_status in (self.UNDER_MAINTENANCE, self.LOST, self.DISPOSED):
            return 0
        active = getattr(self, 'active_unit_count', None)
        if active is None:
            active = self.assignments.filter(status=AssetAssignment.ACTIVE).count()
        return max(self.quantity - active, 0)

    def clean(self):
        if self.is_serialized and self.quantity != 1:
            raise ValidationError({'quantity': 'An asset with a serial number is tracked as one item.'})

    def is_editable(self):
        """Details may be corrected only while the asset is with the company and not issued."""
        if self.current_status not in self.EDITABLE_STATUSES:
            return False
        return not self.assignments.filter(status='active').exists()


class AssetLease(models.Model):
    ACTIVE, EXPIRED, TERMINATED, RETURNED = 'active', 'expired', 'terminated', 'returned'
    STATUSES = [(ACTIVE, 'Active'), (EXPIRED, 'Expired'), (TERMINATED, 'Terminated'), (RETURNED, 'Returned')]
    BILLING_FREQUENCIES = [
        ('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('annually', 'Annually'), ('other', 'Other'),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='leases')
    lessor_name = models.CharField(max_length=180)
    agreement_number = models.CharField(max_length=100, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    monthly_rent = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    security_deposit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'),
                                          validators=[MinValueValidator(Decimal('0.00'))])
    billing_frequency = models.CharField(max_length=12, choices=BILLING_FREQUENCIES, default='monthly')
    purchase_option = models.BooleanField(default=False)
    return_required = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=ACTIVE, db_index=True)
    agreement_document = models.ForeignKey(
        'document_management.Document', null=True, blank=True, on_delete=models.PROTECT,
        related_name='asset_leases',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-id']
        indexes = [models.Index(fields=['asset', 'status'], name='asset_lease_status_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(monthly_rent__gte=0), name='asset_lease_rent_nonnegative'),
            models.CheckConstraint(condition=Q(security_deposit__gte=0), name='asset_lease_deposit_nonnegative'),
            models.CheckConstraint(condition=Q(end_date__gte=models.F('start_date')), name='asset_lease_dates_valid'),
        ]

    def __str__(self):
        return f'{self.asset.asset_code} lease from {self.lessor_name} ({self.start_date})'


class AssetAssignment(models.Model):
    ACTIVE, RETURNED = 'active', 'returned'
    STATUSES = [(ACTIVE, 'Active'), (RETURNED, 'Returned')]

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='assignments')
    employee = models.ForeignKey(
        'employee_management.Employee', on_delete=models.PROTECT, related_name='asset_assignments',
    )
    issue_date = models.DateField(default=timezone.localdate)
    expected_return_date = models.DateField(null=True, blank=True)
    actual_return_date = models.DateField(null=True, blank=True)
    issue_condition = models.CharField(max_length=12, choices=Asset.CONDITIONS, default='good')
    return_condition = models.CharField(max_length=12, choices=Asset.CONDITIONS, blank=True)
    accessories_issued = models.JSONField(default=list, blank=True)
    accessories_returned = models.JSONField(default=list, blank=True)
    issue_notes = models.TextField(blank=True)
    return_notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE, db_index=True)
    parent_assignment = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='accessory_assignments',
    )
    handover_number = models.CharField(max_length=40, blank=True)
    receipt_emailed_at = models.DateTimeField(null=True, blank=True)
    receipt_email_to = models.EmailField(blank=True)
    receipt_email_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-issue_date', '-id']
        indexes = [models.Index(fields=['employee', 'status'], name='asset_assign_emp_status_idx')]
        constraints = [
            models.CheckConstraint(
                condition=Q(expected_return_date__isnull=True) | Q(expected_return_date__gte=models.F('issue_date')),
                name='asset_expected_return_after_issue',
            ),
            models.CheckConstraint(
                condition=Q(actual_return_date__isnull=True) | Q(actual_return_date__gte=models.F('issue_date')),
                name='asset_actual_return_after_issue',
            ),
        ]

    def __str__(self):
        return f'{self.asset.asset_code} assigned to {self.employee}'

    def accessories_handed_over(self):
        """Manual accessory text plus assets issued from the register with this handover."""
        items = [str(item) for item in self.accessories_issued or []]
        for link in self.accessory_assignments.select_related('asset'):
            items.append(f'{link.asset.asset_code} \u2013 {link.asset.name}')
        return items


class AssetAcknowledgement(models.Model):
    PHYSICAL, DIGITAL = 'physical', 'digital'
    TYPES = [(PHYSICAL, 'Physical'), (DIGITAL, 'Digital')]

    assignment = models.ForeignKey(AssetAssignment, on_delete=models.PROTECT, related_name='acknowledgements')
    acknowledgement_type = models.CharField(max_length=10, choices=TYPES)
    acknowledgement_name = models.CharField(max_length=180)
    acknowledged_at = models.DateTimeField(default=timezone.now)
    document = models.ForeignKey(
        'document_management.Document', null=True, blank=True, on_delete=models.PROTECT,
        related_name='asset_acknowledgements',
    )
    confirmation_text = models.TextField(blank=True)
    confirmation_version = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-acknowledged_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=Q(acknowledgement_type='physical')
                | (Q(acknowledgement_type='digital') & ~Q(confirmation_text='') & ~Q(confirmation_text=' ')),
                name='asset_digital_ack_has_confirmation',
            ),
        ]

    def clean(self):
        if self.acknowledgement_type == self.DIGITAL and not self.confirmation_text.strip():
            raise ValidationError({'confirmation_text': 'An explicit employee confirmation is required.'})


class AssetEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Asset events are historical records and cannot be updated.')

    def delete(self):
        raise ValidationError('Asset events are historical records and cannot be deleted.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Asset events are historical records and cannot be updated.')


class AssetEvent(models.Model):
    EVENT_TYPES = [
        ('created', 'Created'), ('updated', 'Details updated'),
        ('assigned', 'Assigned'), ('returned', 'Returned'),
        ('transferred', 'Transferred'), ('status_change', 'Status change'),
        ('maintenance', 'Maintenance'), ('lease', 'Lease'), ('lost', 'Lost'),
        ('disposed', 'Disposed'), ('other', 'Other'),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='events')
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES, db_index=True)
    event_date = models.DateTimeField(default=timezone.now, db_index=True)
    from_employee = models.ForeignKey(
        'employee_management.Employee', null=True, blank=True, on_delete=models.PROTECT,
        related_name='asset_events_from',
    )
    to_employee = models.ForeignKey(
        'employee_management.Employee', null=True, blank=True, on_delete=models.PROTECT,
        related_name='asset_events_to',
    )
    from_location = models.ForeignKey(
        AssetLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='asset_events_from',
    )
    to_location = models.ForeignKey(
        AssetLocation, null=True, blank=True, on_delete=models.PROTECT, related_name='asset_events_to',
    )
    previous_status = models.CharField(max_length=24, blank=True)
    new_status = models.CharField(max_length=24, blank=True)
    remarks = models.TextField(blank=True)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AssetEventQuerySet.as_manager()

    class Meta:
        ordering = ['-event_date', '-id']
        indexes = [models.Index(fields=['asset', 'event_date'], name='asset_event_date_idx')]

    def save(self, *args, **kwargs):
        if self.pk and AssetEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError('Asset events are historical records and cannot be edited.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Asset events are historical records and cannot be deleted.')

    def __str__(self):
        return f'{self.asset.asset_code}: {self.get_event_type_display()} ({self.event_date})'


class AssetMaintenance(models.Model):
    STATUSES = [
        ('reported', 'Reported'), ('in_progress', 'In progress'),
        ('completed', 'Completed'), ('cancelled', 'Cancelled'),
    ]

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='maintenance_records')
    service_date = models.DateField()
    service_provider = models.CharField(max_length=180, blank=True)
    issue_description = models.TextField()
    work_performed = models.TextField(blank=True)
    repair_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    warranty_claim = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUSES, default='reported', db_index=True)
    completion_date = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-service_date', '-id']
        indexes = [models.Index(fields=['asset', 'status'], name='asset_maint_status_idx')]
        constraints = [
            models.CheckConstraint(
                condition=Q(repair_cost__isnull=True) | Q(repair_cost__gte=0),
                name='asset_maint_cost_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(completion_date__isnull=True) | Q(completion_date__gte=models.F('service_date')),
                name='asset_maint_completion_after_service',
            ),
        ]

    def __str__(self):
        return f'{self.asset.asset_code} maintenance ({self.service_date})'


class AssetSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    asset_prefix = models.CharField(max_length=20, default='QB')
    next_asset_sequence = models.PositiveIntegerField(default=1)
    handover_prefix = models.CharField(max_length=20, default='QB-AH')
    next_handover_sequence = models.PositiveIntegerField(default=1)
    default_currency = models.CharField(max_length=3, default='INR')

    class Meta:
        verbose_name = 'asset settings'
        verbose_name_plural = 'asset settings'

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'Asset settings'
