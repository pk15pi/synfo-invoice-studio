import os
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from document_management.models import Document
from employee_management.storage import private_storage
from employee_management.validators import sniff_upload


DOCUMENT_STATUSES = [
    ('draft', 'Draft'),
    ('pending_approval', 'Pending Approval'),
    ('approved', 'Approved'),
    ('issued', 'Issued'),
    ('sent', 'Sent'),
    ('acknowledged', 'Acknowledged'),
    ('signed', 'Signed'),
    ('superseded', 'Superseded'),
    ('cancelled', 'Cancelled'),
    ('expired', 'Expired'),
]


def company_logo_upload_to(instance, filename):
    ext = os.path.splitext(filename or '')[1].lower().lstrip('.')
    if ext not in {'png', 'jpg', 'jpeg'}:
        ext = 'bin'
    return f'document_generation/company/{uuid.uuid4().hex}.{ext}'


def validate_company_logo(upload):
    sniff_upload(upload, 5 * 1024 * 1024, allowed=('png', 'jpg'))


def default_template_output_formats():
    return ['docx', 'pdf']


class CompanyDocumentProfile(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    legal_entity_name = models.CharField(max_length=180, default='Quantbud LLP')
    brand_name = models.CharField(max_length=180, default='Synfowebs Technology')
    llpin = models.CharField('LLPIN', max_length=30, blank=True)
    registered_office_address = models.TextField(blank=True)
    business_address = models.TextField(blank=True)
    gstin = models.CharField(max_length=20, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    official_email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=40, blank=True)
    website = models.URLField(blank=True)
    logo = models.FileField(
        storage=private_storage,
        upload_to=company_logo_upload_to,
        validators=[validate_company_logo],
        blank=True,
    )
    partner_details = models.JSONField(default=list, blank=True)
    document_footer = models.TextField(blank=True)
    jurisdiction = models.CharField(max_length=160, blank=True)
    default_language = models.CharField(max_length=40, default='English')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'company document profile'

    @classmethod
    def current(cls):
        profile, _ = cls.objects.get_or_create(pk=1)
        return profile

    def clean(self):
        if not isinstance(self.partner_details, list):
            raise ValidationError({'partner_details': 'Partner details must be a JSON list.'})

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('The company document profile cannot be deleted.')

    def __str__(self):
        return self.legal_entity_name

    @property
    def contracting_party_display(self):
        if self.brand_name:
            return f'{self.legal_entity_name}, operating under the brand name {self.brand_name}'
        return self.legal_entity_name


class CompanyDocumentSignatory(models.Model):
    profile = models.ForeignKey(
        CompanyDocumentProfile, on_delete=models.PROTECT, related_name='signatories',
    )
    name = models.CharField(max_length=160)
    designation = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    is_authorized = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        indexes = [models.Index(fields=['is_active', 'is_authorized'], name='dgen_sign_active_idx')]

    def clean(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError({'valid_to': 'End date cannot be before start date.'})

    def __str__(self):
        return self.name


class DocumentCategory(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name_plural = 'document categories'

    def __str__(self):
        return self.name


class DocumentType(models.Model):
    YEAR_CALENDAR, YEAR_FINANCIAL, YEAR_NONE = 'calendar', 'financial', 'none'
    YEAR_CONVENTIONS = [
        (YEAR_CALENDAR, 'Calendar year'),
        (YEAR_FINANCIAL, 'Indian financial year (April to March)'),
        (YEAR_NONE, 'No year in document number'),
    ]
    RESET_NEVER, RESET_CALENDAR, RESET_FINANCIAL = 'never', 'calendar_year', 'financial_year'
    RESET_FREQUENCIES = [
        (RESET_NEVER, 'Never'),
        (RESET_CALENDAR, 'Calendar year'),
        (RESET_FINANCIAL, 'Financial year'),
    ]

    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=160)
    categories = models.ManyToManyField(DocumentCategory, related_name='document_types')
    description = models.TextField(blank=True)
    number_prefix = models.CharField(max_length=40)
    number_padding = models.PositiveSmallIntegerField(default=3, validators=[MinValueValidator(1)])
    year_convention = models.CharField(max_length=12, choices=YEAR_CONVENTIONS, default=YEAR_CALENDAR)
    reset_frequency = models.CharField(max_length=16, choices=RESET_FREQUENCIES, default=RESET_CALENDAR)
    approval_required = models.BooleanField(default=True)
    approval_workflow = models.JSONField(default=list, blank=True)
    default_output_formats = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['is_active', 'name'], name='dgen_type_active_idx')]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(number_padding__gte=1), name='dgen_type_padding_positive',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(reset_frequency='never')
                    | (
                        models.Q(reset_frequency='calendar_year', year_convention='calendar')
                        | models.Q(reset_frequency='financial_year', year_convention='financial')
                    )
                ),
                name='dgen_type_reset_year_match',
            ),
        ]

    def clean(self):
        if self.reset_frequency == self.RESET_CALENDAR and self.year_convention != self.YEAR_CALENDAR:
            raise ValidationError({'year_convention': 'Calendar-year resets require calendar-year numbering.'})
        if self.reset_frequency == self.RESET_FINANCIAL and self.year_convention != self.YEAR_FINANCIAL:
            raise ValidationError({'year_convention': 'Financial-year resets require financial-year numbering.'})
        if self.reset_frequency != self.RESET_NEVER and self.year_convention == self.YEAR_NONE:
            raise ValidationError({
                'year_convention': 'A resetting sequence must include its calendar or financial year.'
            })
        if not isinstance(self.approval_workflow, list):
            raise ValidationError({'approval_workflow': 'Approval workflow must be a JSON list.'})
        if (not isinstance(self.default_output_formats, list)
                or any(item not in {'docx', 'pdf'} for item in self.default_output_formats)):
            raise ValidationError({'default_output_formats': 'Choose Word, PDF, or both output formats.'})

    def __str__(self):
        return self.name


class DocumentTemplate(models.Model):
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name='templates',
    )
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True, db_index=True)
    active_version = models.ForeignKey(
        'DocumentTemplateVersion', null=True, blank=True, on_delete=models.PROTECT,
        related_name='active_for_templates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document_type__name', 'name']
        constraints = [
            models.UniqueConstraint(fields=['document_type', 'name'], name='dgen_template_name_uniq'),
        ]

    def clean(self):
        if self.active_version_id:
            if self.active_version.template_id != self.pk:
                raise ValidationError({'active_version': 'The active version must belong to this template.'})
            if self.active_version.status != DocumentTemplateVersion.APPROVED:
                raise ValidationError({'active_version': 'Only an approved version can be active.'})

    def __str__(self):
        return f'{self.document_type.name}: {self.name}'


class DocumentTemplateVersion(models.Model):
    DRAFT, REVIEW, APPROVED, RETIRED = 'draft', 'review', 'approved', 'retired'
    STATUSES = [
        (DRAFT, 'Draft'),
        (REVIEW, 'Pending Review'),
        (APPROVED, 'Approved'),
        (RETIRED, 'Retired'),
    ]

    template = models.ForeignKey(
        DocumentTemplate, on_delete=models.PROTECT, related_name='versions',
    )
    version_number = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    header_text = models.TextField(blank=True)
    body_template = models.TextField(blank=True)
    footer_text = models.TextField(blank=True)
    output_formats = models.JSONField(default=default_template_output_formats, blank=True)
    signature_configuration = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_document_template_versions',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='created_document_template_versions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['template', '-version_number']
        constraints = [
            models.UniqueConstraint(
                fields=['template', 'version_number'], name='dgen_template_version_uniq',
            ),
            models.CheckConstraint(
                condition=models.Q(version_number__gte=1), name='dgen_template_version_positive',
            ),
        ]

    def clean(self):
        if (not isinstance(self.output_formats, list)
                or not self.output_formats
                or any(item not in {'docx', 'pdf'} for item in self.output_formats)):
            raise ValidationError({'output_formats': 'Choose Word, PDF, or both output formats.'})
        if not isinstance(self.signature_configuration, dict):
            raise ValidationError({'signature_configuration': 'Signature configuration must be a JSON object.'})
        parties = self.signature_configuration.get('parties')
        if parties is not None and (
            not isinstance(parties, list)
            or any(role not in {'issuer', 'counterparty', 'witness'} for role in parties)
            or not parties
        ):
            raise ValidationError({
                'signature_configuration': 'Signature parties must be issuer, counterparty, or witness.',
            })
        if self.status == self.APPROVED and (not self.approved_by_id or not self.approved_at):
            raise ValidationError('An approved template version requires an approver and approval time.')

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and (previous.status == self.APPROVED or previous.approved_at):
                immutable_fields = (
                    'template_id', 'version_number', 'header_text', 'body_template',
                    'footer_text', 'output_formats', 'signature_configuration',
                    'approved_by_id', 'approved_at',
                )
                if any(getattr(previous, field) != getattr(self, field) for field in immutable_fields):
                    raise ValidationError('Approved template versions are immutable; create a new version.')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.template} v{self.version_number}'


class DocumentClause(models.Model):
    DRAFT, REVIEW, APPROVED, RETIRED = 'draft', 'review', 'approved', 'retired'
    APPROVAL_STATUSES = [
        (DRAFT, 'Draft'),
        (REVIEW, 'Pending Review'),
        (APPROVED, 'Approved'),
        (RETIRED, 'Retired'),
    ]

    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=160)
    category = models.CharField(max_length=60, blank=True, db_index=True)
    clause_text = models.TextField()
    version = models.PositiveIntegerField(default=1)
    review_date = models.DateField(null=True, blank=True)
    admin_notes = models.TextField(blank=True)
    applicable_document_types = models.ManyToManyField(DocumentType, blank=True, related_name='clauses')
    approval_status = models.CharField(max_length=10, choices=APPROVAL_STATUSES, default=DRAFT, db_index=True)
    effective_date = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_document_clauses',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name', '-version']
        constraints = [
            models.UniqueConstraint(fields=['code', 'version'], name='dgen_clause_version_uniq'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='dgen_clause_version_positive'),
        ]

    def clean(self):
        if self.approval_status == self.APPROVED and (not self.approved_by_id or not self.approved_at):
            raise ValidationError('An approved clause requires an approver and approval time.')

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and (previous.approval_status == self.APPROVED or previous.approved_at):
                immutable_fields = (
                    'code', 'name', 'category', 'clause_text', 'version', 'effective_date',
                    'review_date', 'admin_notes', 'approved_by_id', 'approved_at',
                )
                if any(getattr(previous, field) != getattr(self, field) for field in immutable_fields):
                    raise ValidationError('Approved clauses are immutable; create a new version.')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} v{self.version}'


class DocumentFieldDefinition(models.Model):
    SOURCE_MODULES = [
        ('manual', 'Manual entry'),
        ('employee', 'Employee'),
        ('client', 'Client'),
        ('vendor', 'Vendor'),
        ('asset', 'Asset'),
        ('company', 'Company profile'),
    ]
    VALUE_TYPES = [
        ('text', 'Text'),
        ('date', 'Date'),
        ('decimal', 'Decimal'),
        ('integer', 'Integer'),
        ('boolean', 'Yes/No'),
        ('email', 'Email'),
        ('multiline', 'Multi-line text'),
        ('select', 'Dropdown'),
        ('currency', 'Currency'),
    ]

    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name='field_definitions',
    )
    field_key = models.SlugField(max_length=60)
    label = models.CharField(max_length=120)
    value_type = models.CharField(max_length=12, choices=VALUE_TYPES, default='text')
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    default_value = models.TextField(blank=True)
    placeholder = models.CharField(max_length=160, blank=True)
    help_text = models.TextField(blank=True)
    choices = models.JSONField(default=list, blank=True)
    validation_rules = models.JSONField(default=dict, blank=True)
    source_module = models.CharField(max_length=12, choices=SOURCE_MODULES, default='manual')
    source_field = models.CharField(max_length=60, blank=True)
    is_auto_populated = models.BooleanField(default=False)
    visibility_condition = models.JSONField(default=dict, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['document_type', 'display_order', 'label']
        constraints = [
            models.UniqueConstraint(
                fields=['document_type', 'field_key'], name='dgen_field_key_uniq',
            ),
        ]

    def clean(self):
        if not isinstance(self.validation_rules, dict):
            raise ValidationError({'validation_rules': 'Validation rules must be a JSON object.'})
        allowed_rules = {'min', 'max', 'min_length', 'max_length'}
        unsupported_rules = set(self.validation_rules) - allowed_rules
        if unsupported_rules:
            raise ValidationError({
                'validation_rules': 'Unsupported validation rules: ' + ', '.join(sorted(unsupported_rules)),
            })
        try:
            for key in ('min', 'max'):
                if key in self.validation_rules:
                    Decimal(str(self.validation_rules[key]))
            for key in ('min_length', 'max_length'):
                if key in self.validation_rules and int(self.validation_rules[key]) < 0:
                    raise ValueError
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValidationError({'validation_rules': 'Validation bounds must be valid non-negative values.'}) from error
        if not isinstance(self.choices, list) or any(
            not isinstance(choice, str) for choice in self.choices
        ):
            raise ValidationError({'choices': 'Dropdown choices must be a list of strings.'})
        if not isinstance(self.visibility_condition, dict):
            raise ValidationError({'visibility_condition': 'Visibility condition must be a JSON object.'})
        if self.value_type == 'select' and not self.choices:
            raise ValidationError({'choices': 'Dropdown fields require at least one choice.'})
        if self.source_module == 'manual' and self.source_field:
            raise ValidationError({'source_field': 'Manual fields cannot have a source field.'})
        if self.source_module != 'manual' and self.source_field:
            from .field_resolver import FIELD_ADAPTERS
            if self.source_field not in FIELD_ADAPTERS.get(self.source_module, {}):
                raise ValidationError({'source_field': 'Choose a supported source field for this module.'})
        if self.is_auto_populated and (self.source_module == 'manual' or not self.source_field):
            raise ValidationError({'is_auto_populated': 'Auto-populated fields need a configured source module and field.'})
        if self.visibility_condition:
            if set(self.visibility_condition) - {'field', 'operator', 'value'}:
                raise ValidationError({'visibility_condition': 'Visibility conditions support only field, operator, and value.'})
            field_key = self.visibility_condition.get('field')
            operators = {'truthy', 'present', 'empty', 'equals', 'not_equals'}
            if self.visibility_condition.get('operator', 'truthy') not in operators:
                raise ValidationError({'visibility_condition': 'Choose a supported visibility operator.'})
            if not field_key or not self.document_type.field_definitions.exclude(pk=self.pk).filter(
                field_key=field_key, is_active=True,
            ).exists():
                raise ValidationError({'visibility_condition': 'Choose another active field as the condition.'})

    def __str__(self):
        return f'{self.document_type.name}: {self.label}'


class DocumentNumberSequence(models.Model):
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name='number_sequences',
    )
    period_key = models.CharField(max_length=20, default='all')
    next_value = models.PositiveBigIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['document_type', 'period_key'], name='dgen_sequence_period_uniq',
            ),
            models.CheckConstraint(
                condition=models.Q(next_value__gte=1), name='dgen_sequence_next_positive',
            ),
        ]

    def __str__(self):
        return f'{self.document_type.code} / {self.period_key}'


class GeneratedDocumentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Use document lifecycle services instead of bulk-updating document records.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Use document lifecycle services instead of bulk-updating document records.')

    def delete(self):
        if self.exclude(status='draft').exists():
            raise ValidationError('Only draft documents may be deleted.')
        return super().delete()


class GeneratedDocument(models.Model):
    IMMUTABLE_STATUSES = {
        'issued', 'sent', 'acknowledged', 'signed', 'superseded', 'cancelled', 'expired',
    }
    SNAPSHOT_FIELDS = (
        'document_number', 'document_type_id', 'template_version_id', 'version_number',
        'rendered_content', 'content_snapshot', 'issue_date', 'issued_at',
        'expiry_date', 'file_document_id', 'authorised_signatory_id', 'signatory_snapshot',
        'created_by_id', 'approved_by_id', 'issued_by_id', 'approved_at',
        'effective_date', 'word_file_document_id', 'pdf_file_document_id', 'supersedes_id',
    )

    document_number = models.CharField(max_length=100, unique=True)
    document_type = models.ForeignKey(
        DocumentType, on_delete=models.PROTECT, related_name='generated_documents',
    )
    template_version = models.ForeignKey(
        DocumentTemplateVersion, null=True, blank=True, on_delete=models.PROTECT,
        related_name='generated_documents',
    )
    version_number = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=DOCUMENT_STATUSES, default='draft', db_index=True)
    rendered_content = models.TextField(blank=True)
    content_snapshot = models.JSONField(default=dict, blank=True)
    file_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.PROTECT,
        related_name='generated_documents',
    )
    word_file_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.PROTECT,
        related_name='generated_word_documents',
    )
    pdf_file_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.PROTECT,
        related_name='generated_pdf_documents',
    )
    authorised_signatory = models.ForeignKey(
        CompanyDocumentSignatory, null=True, blank=True, on_delete=models.PROTECT,
        related_name='documents',
    )
    signatory_snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='created_generated_documents',
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_generated_documents',
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='issued_generated_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    supersedes = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT,
        related_name='superseding_documents',
    )

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='dgen_doc_status_created_idx'),
            models.Index(fields=['document_type', 'created_at'], name='dgen_doc_type_created_idx'),
            models.Index(fields=['expiry_date', 'status'], name='dgen_doc_expiry_status_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(expiry_date__isnull=True)
                    | models.Q(issue_date__isnull=True)
                    | models.Q(expiry_date__gte=models.F('issue_date'))
                ),
                name='dgen_doc_expiry_after_issue',
            ),
            models.CheckConstraint(
                condition=models.Q(version_number__gte=1), name='dgen_doc_version_positive',
            ),
            models.UniqueConstraint(
                fields=['supersedes', 'version_number'],
                condition=models.Q(supersedes__isnull=False),
                name='dgen_doc_revision_uniq',
            ),
        ]

    objects = GeneratedDocumentQuerySet.as_manager()

    def clean(self):
        if self.status == 'cancelled' and not self.cancellation_reason.strip():
            raise ValidationError({'cancellation_reason': 'A cancellation reason is required.'})
        if not isinstance(self.content_snapshot, dict):
            raise ValidationError({'content_snapshot': 'The content snapshot must be a JSON object.'})
        if not isinstance(self.signatory_snapshot, dict):
            raise ValidationError({'signatory_snapshot': 'The signatory snapshot must be a JSON object.'})
        if self.template_version_id and self.document_type_id:
            if self.template_version.template.document_type_id != self.document_type_id:
                raise ValidationError({'template_version': 'The template version must match the document type.'})

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and previous.status in self.IMMUTABLE_STATUSES:
                changed = [
                    field for field in self.SNAPSHOT_FIELDS
                    if getattr(previous, field) != getattr(self, field)
                ]
                if previous.status == 'cancelled' and previous.cancellation_reason != self.cancellation_reason:
                    changed.append('cancellation_reason')
                if changed:
                    raise ValidationError(
                        'Issued document snapshots are immutable; create a superseding document instead.'
                    )
        if self.status == 'cancelled' and not self.cancellation_reason.strip():
            raise ValidationError({'cancellation_reason': 'A cancellation reason is required.'})
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != 'draft':
            raise ValidationError('Only draft documents may be deleted.')
        super().delete(*args, **kwargs)

    def __str__(self):
        return self.document_number


class DocumentParty(models.Model):
    ROLES = [
        ('issuer', 'Issuer'),
        ('client', 'Client'),
        ('freelancer', 'Freelancer'),
        ('consultant', 'Consultant'),
        ('candidate', 'Candidate'),
        ('employee', 'Employee'),
        ('asset', 'Asset'),
        ('vendor', 'Vendor'),
        ('witness', 'Witness'),
        ('other', 'Other'),
    ]

    generated_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name='parties',
    )
    role = models.CharField(max_length=16, choices=ROLES, db_index=True)
    content_type = models.ForeignKey(
        'contenttypes.ContentType', null=True, blank=True, on_delete=models.SET_NULL,
    )
    object_id = models.CharField(max_length=40, blank=True)
    linked_record = GenericForeignKey('content_type', 'object_id')
    display_name = models.CharField(max_length=180)
    contact_snapshot = models.JSONField(default=dict, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'id']
        indexes = [models.Index(fields=['content_type', 'object_id'], name='dgen_party_object_idx')]

    def clean(self):
        if not isinstance(self.contact_snapshot, dict):
            raise ValidationError({'contact_snapshot': 'Party snapshot must be a JSON object.'})
        if self.object_id and not self.content_type_id:
            raise ValidationError({'content_type': 'Select the linked record type.'})

    def save(self, *args, **kwargs):
        if self.generated_document_id and self.generated_document.status in GeneratedDocument.IMMUTABLE_STATUSES:
            if self.pk:
                previous = type(self).objects.filter(pk=self.pk).first()
                if previous and any(
                    getattr(previous, field.attname) != getattr(self, field.attname)
                    for field in self._meta.concrete_fields if not field.primary_key
                ):
                    raise ValidationError('Parties on issued documents are immutable.')
            else:
                raise ValidationError('Parties cannot be added to an issued document.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.generated_document.status in GeneratedDocument.IMMUTABLE_STATUSES:
            raise ValidationError('Parties on issued documents cannot be deleted.')
        super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.display_name} ({self.get_role_display()})'


class DocumentTemplateClause(models.Model):
    template_version = models.ForeignKey(
        DocumentTemplateVersion, on_delete=models.PROTECT, related_name='template_clauses',
    )
    clause = models.ForeignKey(
        DocumentClause, on_delete=models.PROTECT, related_name='template_assignments',
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    is_mandatory = models.BooleanField(default=False)
    text_override = models.TextField(blank=True)

    class Meta:
        ordering = ['display_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['template_version', 'clause'], name='dgen_template_clause_uniq',
            ),
        ]

    def save(self, *args, **kwargs):
        version = self.template_version
        if version.status == DocumentTemplateVersion.APPROVED or version.approved_at:
            if self.pk:
                previous = type(self).objects.filter(pk=self.pk).first()
                if previous and any(
                    getattr(previous, field.attname) != getattr(self, field.attname)
                    for field in self._meta.concrete_fields if not field.primary_key
                ):
                    raise ValidationError('Clauses on approved template versions are immutable.')
            else:
                raise ValidationError('Clauses cannot be added to an approved template version.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        version = self.template_version
        if version.status == DocumentTemplateVersion.APPROVED or version.approved_at:
            raise ValidationError('Clauses on approved template versions cannot be deleted.')
        super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.template_version} / {self.clause}'


class DocumentApproval(models.Model):
    PENDING, APPROVED, REJECTED, WITHDRAWN = 'pending', 'approved', 'rejected', 'withdrawn'
    STATUSES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
        (WITHDRAWN, 'Withdrawn'),
    ]

    generated_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name='approvals',
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='document_approvals',
    )
    approver_name = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    comments = models.TextField(blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-requested_at', '-id']

    def __str__(self):
        return f'{self.generated_document} - {self.get_status_display()}'


class DocumentAcknowledgement(models.Model):
    PHYSICAL, DIGITAL = 'physical', 'digital'
    TYPES = [(PHYSICAL, 'Physical'), (DIGITAL, 'Digital')]

    generated_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name='acknowledgements',
    )
    party = models.ForeignKey(
        DocumentParty, null=True, blank=True, on_delete=models.PROTECT,
        related_name='acknowledgements',
    )
    acknowledgement_type = models.CharField(max_length=10, choices=TYPES)
    acknowledging_name = models.CharField(max_length=160, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    document_version = models.PositiveIntegerField(default=1)
    confirmation_text = models.TextField(blank=True)
    confirmation_version = models.CharField(max_length=40, blank=True)
    signed_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.PROTECT,
        related_name='document_acknowledgements',
    )
    received_by = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(acknowledgement_type='digital')
                    | (
                        models.Q(acknowledged_at__isnull=False)
                        & ~models.Q(acknowledging_name='')
                        & ~models.Q(confirmation_text='')
                    )
                ),
                name='dgen_digital_ack_complete',
            ),
            models.CheckConstraint(
                condition=models.Q(document_version__gte=1), name='dgen_ack_version_positive',
            ),
        ]

    def clean(self):
        if self.acknowledgement_type == self.DIGITAL and (
            not self.acknowledged_at or not self.acknowledging_name.strip() or not self.confirmation_text.strip()
        ):
            raise ValidationError(
                'Digital acknowledgement requires an explicit name, confirmation, and timestamp.'
            )
        if self.acknowledgement_type == self.PHYSICAL and self.signed_document_id and not self.received_by.strip():
            raise ValidationError({'received_by': 'Record who received the signed physical document.'})

    def __str__(self):
        return f'{self.generated_document} - {self.get_acknowledgement_type_display()}'


class DocumentSignature(models.Model):
    MANUAL, ESIGN = 'manual', 'esign'
    METHODS = [(MANUAL, 'Manual / uploaded evidence'), (ESIGN, 'External e-signature service')]
    PENDING, SIGNED, DECLINED, VOID = 'pending', 'signed', 'declined', 'void'
    STATUSES = [(PENDING, 'Pending'), (SIGNED, 'Signed'), (DECLINED, 'Declined'), (VOID, 'Void')]

    generated_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name='signatures',
    )
    party = models.ForeignKey(
        DocumentParty, null=True, blank=True, on_delete=models.PROTECT, related_name='signatures',
    )
    signer_name = models.CharField(max_length=160)
    method = models.CharField(max_length=8, choices=METHODS, default=MANUAL)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.PROTECT,
        related_name='document_signatures',
    )
    provider_reference = models.CharField(max_length=160, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def clean(self):
        if not isinstance(self.metadata, dict):
            raise ValidationError({'metadata': 'Signature metadata must be a JSON object.'})

    def __str__(self):
        return f'{self.generated_document} - {self.signer_name}'


class DocumentActivityLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Document activity history is append-only.')

    def delete(self):
        raise ValidationError('Document activity history is append-only.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Document activity history is append-only.')


class DocumentActivityLog(models.Model):
    generated_document = models.ForeignKey(
        GeneratedDocument, on_delete=models.PROTECT, related_name='activity',
    )
    action = models.CharField(max_length=40, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='document_activity',
    )
    actor_label = models.CharField(max_length=160, default='Local operator (no login)')
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = DocumentActivityLogQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-id']

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Document activity history is append-only.')
        if not self._state.adding:
            raise ValidationError('Document activity history is append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Document activity history is append-only.')

    def __str__(self):
        return f'{self.generated_document} - {self.action}'
