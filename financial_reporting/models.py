import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from employee_management.storage import private_storage
from employee_management.validators import sniff_upload


PERIOD_TYPES = [
    ('month', 'Monthly'),
    ('quarter', 'Quarterly'),
    ('half_year', 'Half-yearly'),
    ('annual', 'Annual'),
    ('financial_year', 'Financial year'),
    ('custom', 'Custom date range'),
]
OUTPUT_FORMATS = [('pdf', 'PDF'), ('xlsx', 'Excel'), ('csv', 'CSV'), ('zip', 'ZIP')]


def report_file_upload_to(instance, filename):
    extension = instance.output_format if instance.output_format in {'pdf', 'xlsx', 'csv', 'zip'} else 'bin'
    return f'financial_reporting/reports/{uuid.uuid4().hex}.{extension}'


def package_upload_to(instance, filename):
    return f'financial_reporting/packages/{uuid.uuid4().hex}.zip'


def compliance_upload_to(instance, filename):
    return f'financial_reporting/compliance/{uuid.uuid4().hex}.pdf'


def validate_acknowledgement_pdf(upload):
    sniff_upload(upload, 10 * 1024 * 1024, allowed=('pdf',))


class FinancialPeriod(models.Model):
    """A persisted report range; distinct from accounting.FinancialPeriod's monthly close control."""

    name = models.CharField(max_length=120)
    period_type = models.CharField(max_length=16, choices=PERIOD_TYPES, db_index=True)
    financial_year = models.ForeignKey(
        'accounting.FinancialYear', null=True, blank=True, on_delete=models.PROTECT,
        related_name='reporting_periods',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date', '-end_date']
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F('start_date')),
                name='fin_report_period_dates_valid',
            ),
            models.UniqueConstraint(
                fields=['period_type', 'start_date', 'end_date'],
                name='fin_report_period_unique_range',
            ),
        ]
        indexes = [models.Index(fields=['start_date', 'end_date'], name='fin_report_period_range_idx')]

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
        if self.financial_year_id and self.start_date and self.end_date:
            fy = self.financial_year
            if self.start_date < fy.start_date or self.end_date > fy.end_date:
                raise ValidationError({'financial_year': 'The report range must be within its financial year.'})

    def __str__(self):
        return self.name


class ReportCategory(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    active_status = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'report categories'

    def __str__(self):
        return self.name


class ReportDefinition(models.Model):
    report_code = models.SlugField(max_length=60, unique=True)
    report_name = models.CharField(max_length=160)
    category = models.ForeignKey(ReportCategory, on_delete=models.PROTECT, related_name='reports')
    description = models.TextField(blank=True)
    source_application = models.CharField(max_length=60)
    source_model_or_service = models.CharField(max_length=160, blank=True)
    supported_period_types = models.JSONField(default=list)
    supported_formats = models.JSONField(default=list)
    applicable_financial_year = models.ForeignKey(
        'accounting.FinancialYear', null=True, blank=True, on_delete=models.PROTECT,
        related_name='report_definitions',
    )
    applicability_rules = models.JSONField(default=dict, blank=True)
    active_status = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category__display_order', 'report_name']
        indexes = [
            models.Index(fields=['category', 'active_status'], name='fin_report_cat_active_idx'),
            models.Index(fields=['source_application', 'active_status'], name='fin_report_source_idx'),
        ]

    def __str__(self):
        return f'{self.report_name} ({self.report_code})'

    def clean(self):
        errors = {}
        if not isinstance(self.supported_period_types, list) or any(
            item not in dict(PERIOD_TYPES) for item in self.supported_period_types
        ):
            errors['supported_period_types'] = 'Choose supported period types from the configured list.'
        if not isinstance(self.supported_formats, list) or any(
            item not in dict(OUTPUT_FORMATS) for item in self.supported_formats
        ):
            errors['supported_formats'] = 'Choose supported formats from the configured list.'
        if not isinstance(self.applicability_rules, dict):
            errors['applicability_rules'] = 'Applicability rules must be a JSON object.'
        if errors:
            raise ValidationError(errors)


class ReportRun(models.Model):
    PENDING, RUNNING, SUCCEEDED, FAILED, INCOMPLETE = (
        'pending', 'running', 'succeeded', 'failed', 'incomplete',
    )
    STATUSES = [
        (PENDING, 'Pending'), (RUNNING, 'Running'), (SUCCEEDED, 'Succeeded'),
        (FAILED, 'Failed'), (INCOMPLETE, 'Incomplete'),
    ]

    report_definition = models.ForeignKey(ReportDefinition, on_delete=models.PROTECT, related_name='runs')
    financial_period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='report_runs')
    from_date = models.DateField()
    to_date = models.DateField()
    filters = models.JSONField(default=dict, blank=True)
    output_format = models.CharField(max_length=8, choices=OUTPUT_FORMATS, blank=True)
    generated_file = models.FileField(
        storage=private_storage, upload_to=report_file_upload_to, blank=True,
    )
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    record_count = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING, db_index=True)
    error_details = models.TextField(blank=True)
    source_data_as_of = models.DateTimeField(null=True, blank=True)
    source_snapshot_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-generated_at', '-id']
        indexes = [
            models.Index(fields=['report_definition', 'generated_at'], name='fin_run_report_date_idx'),
            models.Index(fields=['status', 'generated_at'], name='fin_run_status_date_idx'),
        ]
        constraints = [models.CheckConstraint(
            condition=Q(to_date__gte=models.F('from_date')),
            name='fin_report_run_dates_valid',
        )]

    def __str__(self):
        return f'{self.report_definition.report_code} {self.from_date} to {self.to_date}'


class ExportPackage(models.Model):
    MONTHLY, QUARTERLY, HALF_YEARLY, ANNUAL, INCOME_TAX, GST, TDS, COMPLETE, CUSTOM = (
        'monthly_accounts', 'quarterly_tax', 'half_yearly_review', 'annual_audit',
        'income_tax', 'gst_reconciliation', 'tds_compliance', 'complete_ca', 'custom',
    )
    PACKAGE_TYPES = [
        (MONTHLY, 'Monthly Accounts Package'), (QUARTERLY, 'Quarterly Tax Package'),
        (HALF_YEARLY, 'Half-Yearly Financial Review'), (ANNUAL, 'Annual Audit Package'),
        (INCOME_TAX, 'Income Tax Package'), (GST, 'GST Reconciliation Package'),
        (TDS, 'TDS Compliance Package'), (COMPLETE, 'Complete CA Package'),
        (CUSTOM, 'Custom Export Package'),
    ]
    PENDING, RUNNING, SUCCEEDED, FAILED, INCOMPLETE = (
        'pending', 'running', 'succeeded', 'failed', 'incomplete',
    )
    STATUSES = [
        (PENDING, 'Pending'), (RUNNING, 'Running'), (SUCCEEDED, 'Succeeded'),
        (FAILED, 'Failed'), (INCOMPLETE, 'Incomplete'),
    ]

    package_number = models.CharField(max_length=60, unique=True)
    package_type = models.CharField(max_length=24, choices=PACKAGE_TYPES, db_index=True)
    financial_period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='packages')
    from_date = models.DateField()
    to_date = models.DateField()
    zip_file = models.FileField(
        storage=private_storage, upload_to=package_upload_to, blank=True,
    )
    manifest = models.JSONField(default=dict, blank=True)
    generation_status = models.CharField(max_length=12, choices=STATUSES, default=PENDING, db_index=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['generation_status', 'created_at'], name='fin_package_status_idx')]
        constraints = [models.CheckConstraint(
            condition=Q(to_date__gte=models.F('from_date')),
            name='fin_package_dates_valid',
        )]

    def __str__(self):
        return self.package_number


class ExportPackageItem(models.Model):
    REPORT, SOURCE_DOCUMENT = 'report', 'source_document'
    ITEM_TYPES = [(REPORT, 'Generated report'), (SOURCE_DOCUMENT, 'Supporting document')]

    package = models.ForeignKey(ExportPackage, on_delete=models.CASCADE, related_name='items')
    item_type = models.CharField(max_length=20, choices=ITEM_TYPES)
    report_run = models.ForeignKey(
        ReportRun, null=True, blank=True, on_delete=models.PROTECT, related_name='package_items',
    )
    source_document = models.ForeignKey(
        'document_management.Document', null=True, blank=True, on_delete=models.PROTECT,
        related_name='financial_export_items',
    )
    file_name = models.CharField(max_length=255)
    record_count = models.PositiveBigIntegerField(default=0)
    file_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    included = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['file_name', 'id']
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(item_type='report', report_run__isnull=False, source_document__isnull=True)
                    | Q(item_type='source_document', report_run__isnull=True, source_document__isnull=False)
                ),
                name='fin_package_item_one_source',
            ),
            models.UniqueConstraint(fields=['package', 'file_name'], name='fin_package_item_name_uniq'),
        ]

    def __str__(self):
        return self.file_name


class ReconciliationRun(models.Model):
    PENDING, RUNNING, COMPLETED, FAILED = 'pending', 'running', 'completed', 'failed'
    STATUSES = [(PENDING, 'Pending'), (RUNNING, 'Running'), (COMPLETED, 'Completed'), (FAILED, 'Failed')]

    reconciliation_type = models.CharField(max_length=60, db_index=True)
    financial_period = models.ForeignKey(
        FinancialPeriod, on_delete=models.PROTECT, related_name='reconciliations',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING, db_index=True)
    total_records = models.PositiveBigIntegerField(default=0)
    matched_records = models.PositiveBigIntegerField(default=0)
    unmatched_records = models.PositiveBigIntegerField(default=0)
    difference_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    generated_at = models.DateTimeField(default=timezone.now, db_index=True)
    error_details = models.TextField(blank=True)

    class Meta:
        ordering = ['-generated_at', '-id']
        indexes = [models.Index(fields=['reconciliation_type', 'generated_at'], name='fin_recon_type_date_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=models.F('start_date')),
                                   name='fin_recon_dates_valid'),
            models.CheckConstraint(condition=Q(matched_records__lte=models.F('total_records')),
                                   name='fin_recon_matched_lte_total'),
            models.CheckConstraint(condition=Q(unmatched_records__lte=models.F('total_records')),
                                   name='fin_recon_unmatched_lte_total'),
            models.CheckConstraint(
                condition=Q(matched_records__lte=models.F('total_records') - models.F('unmatched_records')),
                name='fin_recon_counts_not_overlapping',
            ),
            models.CheckConstraint(
                condition=~Q(status='completed') | Q(
                    matched_records=models.F('total_records') - models.F('unmatched_records'),
                ),
                name='fin_recon_completed_counts_complete',
            ),
        ]

    def __str__(self):
        return f'{self.reconciliation_type} {self.start_date} to {self.end_date}'


class ReconciliationIssue(models.Model):
    OPEN, RESOLVED, ACCEPTED = 'open', 'resolved', 'accepted'
    RESOLUTION_STATUSES = [(OPEN, 'Open'), (RESOLVED, 'Resolved'), (ACCEPTED, 'Accepted with explanation')]

    reconciliation_run = models.ForeignKey(ReconciliationRun, on_delete=models.PROTECT, related_name='issues')
    source_application = models.CharField(max_length=60, blank=True)
    source_reference = models.CharField(max_length=160)
    expected_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    actual_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    issue_category = models.CharField(max_length=60, db_index=True)
    resolution_status = models.CharField(max_length=12, choices=RESOLUTION_STATUSES, default=OPEN, db_index=True)
    resolution_remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['resolution_status', 'issue_category', 'id']
        indexes = [models.Index(fields=['reconciliation_run', 'resolution_status'],
                                name='fin_issue_run_status_idx')]

    def __str__(self):
        return f'{self.issue_category}: {self.source_reference}'


class AuditChecklist(models.Model):
    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=60, blank=True, db_index=True)
    description = models.TextField(blank=True)
    applicability_rules = models.JSONField(default=dict, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    active_status = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name


class AuditChecklistResult(models.Model):
    PASSED, WARNING, FAILED, PENDING, NOT_APPLICABLE = (
        'passed', 'warning', 'failed', 'pending', 'not_applicable',
    )
    STATUSES = [
        (PASSED, 'Passed'), (WARNING, 'Warning'), (FAILED, 'Failed'),
        (PENDING, 'Pending'), (NOT_APPLICABLE, 'Not applicable'),
    ]

    checklist = models.ForeignKey(AuditChecklist, on_delete=models.PROTECT, related_name='results')
    financial_period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='checklist_results')
    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING, db_index=True)
    summary = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    resolution_remarks = models.TextField(blank=True)
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-checked_at', 'checklist__display_order']
        indexes = [models.Index(fields=['financial_period', 'status'], name='fin_check_period_status_idx')]

    def __str__(self):
        return f'{self.checklist.name}: {self.get_status_display()}'


class ComplianceTemplate(models.Model):
    template_code = models.SlugField(max_length=60)
    template_name = models.CharField(max_length=160)
    compliance_type = models.CharField(max_length=60, db_index=True)
    applicable_financial_year = models.ForeignKey(
        'accounting.FinancialYear', null=True, blank=True, on_delete=models.PROTECT,
        related_name='compliance_templates',
    )
    legal_framework = models.CharField(max_length=120, blank=True)
    tax_regime = models.CharField(max_length=80, blank=True)
    form_version = models.CharField(max_length=40)
    configuration = models.JSONField(default=dict, blank=True)
    active_status = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['compliance_type', 'template_code', '-form_version']
        constraints = [models.UniqueConstraint(
            fields=['template_code', 'applicable_financial_year', 'tax_regime', 'form_version'],
            name='fin_compliance_template_version_uniq',
            condition=Q(applicable_financial_year__isnull=False),
        ), models.UniqueConstraint(
            fields=['template_code', 'tax_regime', 'form_version'],
            name='fin_compliance_template_global_version_uniq',
            condition=Q(applicable_financial_year__isnull=True),
        )]

    def __str__(self):
        return f'{self.template_name} ({self.form_version})'


class ComplianceSubmission(models.Model):
    DRAFT, IN_REVIEW, REVIEWED, FILED, NOT_FILED = 'draft', 'in_review', 'reviewed', 'filed', 'not_filed'
    STATUSES = [
        (DRAFT, 'Draft working paper'), (IN_REVIEW, 'Under CA review'), (REVIEWED, 'Reviewed'),
        (FILED, 'Filed externally'), (NOT_FILED, 'Not filed'),
    ]

    compliance_template = models.ForeignKey(
        ComplianceTemplate, on_delete=models.PROTECT, related_name='submissions',
    )
    reporting_period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='submissions')
    filing_date = models.DateField(null=True, blank=True)
    acknowledgement_number = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=DRAFT, db_index=True)
    uploaded_acknowledgement = models.FileField(
        storage=private_storage, upload_to=compliance_upload_to,
        validators=[validate_acknowledgement_pdf], blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-reporting_period__start_date', '-created_at']
        indexes = [models.Index(fields=['status', 'filing_date'], name='fin_submission_status_idx')]

    def __str__(self):
        return f'{self.compliance_template.template_name} - {self.reporting_period}'


class ExportManifest(models.Model):
    package = models.ForeignKey(ExportPackage, on_delete=models.PROTECT, related_name='manifests')
    version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField(default=dict)
    sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-version', '-created_at']
        constraints = [models.UniqueConstraint(fields=['package', 'version'], name='fin_manifest_version_uniq')]

    def __str__(self):
        return f'{self.package.package_number} manifest v{self.version}'
