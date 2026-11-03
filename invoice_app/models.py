"""Data model for QuantBud Invoice Studio.

Money is always ``Decimal``; see ``billing.py`` for the single rounding policy.
"""
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from . import gst
from .descriptions import validate_template_text

HEX_HELP = 'Hex colour, e.g. #0B5394'


class BusinessProfile(models.Model):
    """Singleton (pk=1) holding the issuer details, numbering and PDF colours."""

    ROUND_PAISE = 'paise'
    ROUND_RUPEE_DOWN = 'rupee_down'
    ROUNDING = [
        (ROUND_RUPEE_DOWN, 'Prorated amounts truncated to whole rupees (matches the QB020 sample)'),
        (ROUND_PAISE, 'Round to 2 decimals, ROUND_HALF_UP'),
    ]

    name = models.CharField(max_length=180, default='QUANTBUD LLP')
    address = models.TextField(default='57, Street No. 1, Sector-72\nGurugram, Haryana Pin-122101')
    phone = models.CharField('Contact number', max_length=60, default='6033430700', blank=True)
    email = models.EmailField(default='pk15pi@gmail.com', blank=True)
    gstin = models.CharField('GSTIN', max_length=40, default='06AABFQ2999K1ZQ', blank=True)
    bank_name = models.CharField(max_length=120, default='IDFC First Bank', blank=True)
    account_holder = models.CharField('Account holder name', max_length=180, default='QuantBud LLP', blank=True)
    account_number = models.CharField('Account number', max_length=80, default='10217638591', blank=True)
    bank_country = models.CharField(max_length=80, default='INDIA', blank=True)
    ifsc = models.CharField('IFSC', max_length=40, default='IDFB0021014', blank=True)
    signature_image = models.ImageField(
        upload_to='signatures/', blank=True,
        help_text='PNG with transparent background works best. If empty, the bundled default signature is used.')
    signature_text = models.CharField('Name below signature', max_length=180, default='QuantBud LLP', blank=True)
    footer_text = models.CharField(max_length=180, default='Thank you for your business!', blank=True)
    invoice_prefix = models.CharField(max_length=20, default='QB')
    next_invoice_number = models.PositiveIntegerField('Next sequence number', default=21)
    number_padding = models.PositiveSmallIntegerField('Digits in number', default=3)
    payment_terms_days = models.PositiveIntegerField('Default payment terms (days)', default=31)
    amount_rounding = models.CharField(max_length=12, choices=ROUNDING, default=ROUND_RUPEE_DOWN)
    # PDF colours (geometry never depends on these)
    primary_color = models.CharField('Primary navy (header, badge, total strip)', max_length=7, default='#0B5394', help_text=HEX_HELP)
    alternate_row_color = models.CharField('Alternate (light-blue) row', max_length=7, default='#CFE2F3', help_text=HEX_HELP)
    white_row_color = models.CharField('White row', max_length=7, default='#FFFFFF', help_text=HEX_HELP)
    border_color = models.CharField('Outer border', max_length=7, default='#0000FF', help_text=HEX_HELP)
    label_color = models.CharField('Labels (Bill To, dates)', max_length=7, default='#0000FF', help_text=HEX_HELP)

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def format_number(self, number):
        return f'{self.invoice_prefix}{number:0{self.number_padding}d}'

    def next_number(self):
        return self.format_number(self.next_invoice_number)

    def __str__(self):
        return self.name


TERMS_MONTH_DAYS = 'month_days'
TERMS_CUSTOM = 'custom'
TERMS_MODES = [
    (TERMS_MONTH_DAYS, '1 - Days in the invoice month (auto: Nov = 30, Feb = 28/29)'),
    (TERMS_CUSTOM, '2 - Custom text or number (e.g. 31, Net 15, Due on receipt)'),
]


class InvoiceFormatTemplate(models.Model):
    """A PDF design master. 'standard' = the original QuantBud layout (US Letter; Standard export vs GST
    Tax Invoice chosen automatically by tax treatment), byte-identical to the pre-format output. The other
    engines are built-in ReportLab layouts (invoice_app/pdf/formats.py). Engines only print saved invoice
    data; they never recompute amounts."""
    STANDARD, MODERN, CORPORATE, COMPACT, BOLD, TIMESHEET = (
        'standard', 'modern_minimal', 'corporate_classic', 'compact', 'bold_header', 'timesheet')
    ENGINES = [
        (STANDARD, 'QuantBud Standard (original layout, US Letter, auto Standard / Tax Invoice)'),
        (MODERN, 'Modern Minimal'),
        (CORPORATE, 'Corporate Classic'),
        (COMPACT, 'Compact One-Page'),
        (BOLD, 'Bold Header'),
        (TIMESHEET, 'Timesheet / Service Detail'),
    ]
    A4, LETTER = 'A4', 'LETTER'
    PAGE_SIZES = [(A4, 'A4 (210 x 297 mm)'), (LETTER, 'US Letter (8.5 x 11 in)')]
    PORTRAIT, LANDSCAPE = 'portrait', 'landscape'
    ORIENTATIONS = [(PORTRAIT, 'Portrait'), (LANDSCAPE, 'Landscape')]
    COLUMN_FIELDS = ('col_project_label', 'col_description_label', 'col_qty_label', 'col_unit_price_label',
                     'col_amount_label')
    SNAPSHOT_FIELDS = ('name', 'code', 'layout_engine', 'page_size', 'orientation', 'accent_color', 'show_logo',
                       'title_text') + COLUMN_FIELDS + (
                       'show_po_number', 'show_vendor_code', 'show_bank_details', 'show_signature',
                       'show_amount_in_words', 'notes_text', 'declaration_text')

    name = models.CharField('Format name', max_length=120, unique=True)
    code = models.SlugField(max_length=60, unique=True, help_text='Short unique id, e.g. modern-minimal.')
    layout_engine = models.CharField(max_length=24, choices=ENGINES, default=MODERN)
    page_size = models.CharField(max_length=8, choices=PAGE_SIZES, default=A4)
    orientation = models.CharField(max_length=10, choices=ORIENTATIONS, default=PORTRAIT)
    accent_color = models.CharField('Accent colour', max_length=7, default='#0B5394', help_text=HEX_HELP,
                                    validators=[RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Use a hex colour like #0B5394.')])
    show_logo = models.BooleanField('Show logo / monogram', default=True,
                                    help_text='Uses invoice_app/assets/logo.png when present, else a monogram.')
    title_text = models.CharField('Title override', max_length=40, blank=True,
                                  help_text='Blank = INVOICE (or TAX INVOICE for Domestic GST).')
    col_project_label = models.CharField('Column 1 label', max_length=40, blank=True,
                                         help_text='Blank = Consultant - Project Name')
    col_description_label = models.CharField('Column 2 label', max_length=40, blank=True,
                                             help_text='Blank = Memo/Description')
    col_qty_label = models.CharField('Column 3 label', max_length=20, blank=True, help_text='Blank = Qty')
    col_unit_price_label = models.CharField('Column 4 label', max_length=20, blank=True,
                                            help_text='Blank = Unit Price')
    col_amount_label = models.CharField('Column 5 label', max_length=20, blank=True, help_text='Blank = Amount')
    show_po_number = models.BooleanField('Show PO number', default=True)
    show_vendor_code = models.BooleanField('Show customer vendor code', default=True)
    show_bank_details = models.BooleanField('Show bank / payment details', default=True)
    show_signature = models.BooleanField('Show signature block', default=True)
    show_amount_in_words = models.BooleanField('Show amount in words', default=True,
                                               help_text='Always printed on Domestic GST tax invoices.')
    notes_text = models.TextField('Notes / terms text', blank=True)
    declaration_text = models.TextField('Declaration / footer text', blank=True)
    is_active = models.BooleanField('Active', default=True)
    is_builtin = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'invoice format template'

    def __str__(self):
        return self.name

    @property
    def is_standard(self):
        return self.layout_engine == self.STANDARD

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.layout_engine == self.STANDARD and (self.page_size != self.LETTER or
                                                     self.orientation != self.PORTRAIT):
            raise ValidationError({'page_size': 'The QuantBud Standard engine is fixed to US Letter portrait '
                                                '(it reproduces the original invoice exactly).'})
        if self.accent_color:
            self.accent_color = self.accent_color.upper()

    def settings_snapshot(self):
        snap = {f: getattr(self, f) for f in self.SNAPSHOT_FIELDS}
        snap['id'] = self.pk
        return snap


class Client(models.Model):
    MONTHLY_PRORATED = 'monthly_prorated'
    PER_DAY = 'per_day'
    MONTHLY_FIXED = 'monthly_fixed'
    BILLING_TYPES = [
        (MONTHLY_PRORATED, '1 - Monthly fixed amount (Unit Price = monthly / days in month)'),
        (PER_DAY, '2 - Per day amount (Unit Price fixed, Amount = Unit Price x Qty)'),
        (MONTHLY_FIXED, '3 - Monthly amount, Unit Price blank (Amount = monthly amount)'),
    ]
    name = models.CharField('Company / customer name', max_length=180)
    address = models.TextField(blank=True, help_text='One line per PDF line, e.g. street / city, state / country - zip')
    country = models.CharField(max_length=80, blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField('Contact number', max_length=60, blank=True)
    tax_id = models.CharField('Tax information', max_length=60, blank=True)
    billing_type = models.CharField('Payment term (billing type)', max_length=20, choices=BILLING_TYPES,
                                    default=MONTHLY_PRORATED,
                                    help_text='How every project row for this customer is priced. Separate from Terms (days until due).')
    terms_mode = models.CharField('Terms (days until due) type', max_length=12, choices=TERMS_MODES,
                                  default=TERMS_CUSTOM)
    terms_text = models.CharField('Terms (custom text or number)', max_length=40, blank=True,
                                  help_text='Only for type 2. Blank = business profile default days.')
    default_terms_days = models.PositiveIntegerField('Legacy terms days (unused; see Terms type/text)',
                                                     null=True, blank=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField('Active', default=True,
                                 help_text='Inactive customers are hidden from the Bill To list on new invoices.')
    # --- GST (Indian customers). Default 'export' = the historic no-tax behaviour. ---
    tax_treatment = models.CharField('Tax treatment', max_length=12, choices=gst.TREATMENTS, default=gst.EXPORT,
                                     help_text='Default for new invoices; can be changed per invoice.')
    gstin = models.CharField('Customer GSTIN', max_length=15, blank=True, validators=[gst.validate_gstin],
                             help_text='Optional. 15 characters; the state code is taken from its first 2 digits.')
    state_code = models.CharField('State code', max_length=2, blank=True, choices=gst.STATE_CHOICES,
                                  help_text='Auto-filled from the GSTIN. Needed for Domestic GST (default place of supply).')
    state_name = models.CharField('State name', max_length=80, blank=True, help_text='Auto-filled from the state code.')
    lut_number = models.CharField('LUT number', max_length=60, blank=True,
                                  help_text='Optional, printed on Export under LUT invoices.')
    vendor_code = models.CharField('Our vendor code (at this customer)', max_length=60, blank=True,
                                   help_text='Optional; printed by invoice formats that show it.')
    invoice_format = models.ForeignKey(InvoiceFormatTemplate, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='clients',
                                       help_text='Blank = default output (QuantBud Standard / Tax Invoice).')

    class Meta:
        ordering = ['name']

    def clean(self):
        from django.core.exceptions import ValidationError
        self.gstin = (self.gstin or '').strip().upper()
        if self.gstin and not gst.gstin_error(self.gstin):
            self.state_code = self.gstin[:2]
        if self.state_code and not self.state_name:
            self.state_name = gst.STATES.get(self.state_code, '')
        if self.tax_treatment == gst.DOMESTIC_GST and not self.state_code:
            raise ValidationError({'state_code': 'Domestic GST needs the customer state (enter the GSTIN or pick the state).'})

    def save(self, *args, **kwargs):
        self.gstin = (self.gstin or '').strip().upper()
        derived = gst.state_from_gstin(self.gstin) if self.gstin and not gst.gstin_error(self.gstin) else ''
        if derived:
            self.state_code = derived
        if self.state_code and not self.state_name:
            self.state_name = gst.STATES.get(self.state_code, '')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class InvoiceDescriptionTemplate(models.Model):
    """Reusable Memo/Description text for invoice lines (Description Template Master).

    Placeholders: see ``descriptions.ALLOWED_PLACEHOLDERS``. Never hard-delete one that is in use:
    projects (PROTECT) and saved invoice lines (PROTECT) block deletion; deactivate instead."""
    name = models.CharField('Template name', max_length=120, unique=True)
    template_text = models.CharField(
        max_length=240, validators=[validate_template_text],
        help_text='Placeholders: {start_date} {end_date} {month} {year} {project_name} {client_name}')
    is_active = models.BooleanField('Active', default=True,
                                    help_text='Inactive templates cannot be picked for projects (existing assignments keep working).')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name if self.is_active else f'{self.name} (inactive)'


class ProjectTemplate(models.Model):
    FIXED = 'fixed'
    VARIABLE = 'variable'
    PRICING = [(FIXED, 'Fixed monthly amount'), (VARIABLE, 'Variable (daily rate x days)')]
    CALENDAR = 'calendar'
    THIRTY = '30'
    BASIS = [(CALENDAR, 'Actual calendar days in month (28-31)'), (THIRTY, 'Fixed 30 days')]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField('Consultant - Project Name', max_length=180)
    DESC_TEMPLATE, DESC_CUSTOM, DESC_BLANK = 'template', 'custom', 'blank'
    DESC_TYPES = [(DESC_TEMPLATE, 'Fixed Template'), (DESC_CUSTOM, 'Custom Text'), (DESC_BLANK, 'Blank')]
    invoice_description_type = models.CharField(
        'Invoice Memo/Description type', max_length=10, choices=DESC_TYPES, default=DESC_CUSTOM,
        help_text='How new invoice lines for this project get their Memo/Description.')
    description_template = models.ForeignKey(
        'InvoiceDescriptionTemplate', null=True, blank=True, on_delete=models.PROTECT, related_name='projects',
        verbose_name='Description template')
    # Reused as the Custom Text (placeholders allowed). The legacy base text 'Support' was migrated to
    # 'Support - {start_date} - {end_date}', which renders exactly what older invoices got.
    description = models.CharField('Custom Memo/Description', max_length=240, blank=True,
                                   default='Support - {start_date} - {end_date}',
                                   validators=[validate_template_text],
                                   help_text='Used for Custom Text. Placeholders allowed, e.g. Support Services for {month} {year}')
    pricing_type = models.CharField('Legacy billing type (unused; customer billing type applies)', max_length=12, choices=PRICING, default=FIXED)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fixed_monthly_even_if_partial = models.BooleanField(
        'Charge full monthly amount regardless of days', default=False,
        help_text='Option C. Unticked = Option B (prorated by the divisor below).')
    proration_basis = models.CharField('Day calculation method (divisor)', max_length=12, choices=BASIS, default=CALENDAR)
    daily_rate = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text='Used when the customer billing type is 2 (Per day amount)')
    active = models.BooleanField('Active', default=True,
                                 help_text='Inactive projects are hidden from the invoice Consultant -Project Name list.')
    sac_code = models.CharField('SAC code', max_length=10, default=gst.DEFAULT_SAC, blank=True,
                                help_text='Services Accounting Code printed on GST tax invoices (998314 = IT consulting/support).')
    gst_rate = models.DecimalField('GST rate %', max_digits=5, decimal_places=2, default=gst.DEFAULT_RATE,
                                   help_text='Applied only on Domestic GST invoices.')

    class Meta:
        ordering = ['name']

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.invoice_description_type == self.DESC_TEMPLATE and not self.description_template_id:
            errors['description_template'] = 'Select a description template for Fixed Template.'
        if self.invoice_description_type == self.DESC_CUSTOM and not (self.description or '').strip():
            errors['description'] = 'Enter the custom description for Custom Text.'
        if errors:
            raise ValidationError(errors)

    @property
    def description_summary(self):
        if self.invoice_description_type == self.DESC_TEMPLATE:
            t = self.description_template
            return f'Template: {t.name if t else "-"}' + ('' if not t or t.is_active else ' (inactive)')
        if self.invoice_description_type == self.DESC_CUSTOM:
            return f'Custom: {self.description}'
        return 'Blank'

    @property
    def method_label(self):
        bt = self.client.billing_type if self.client_id else Client.MONTHLY_PRORATED
        if bt == Client.PER_DAY:
            return f'Per day: {self.daily_rate} x Qty'
        if bt == Client.MONTHLY_FIXED:
            return f'Monthly {self.monthly_amount}, Unit Price blank'
        return f'Monthly {self.monthly_amount} / days in month x Qty'

    def __str__(self):
        return f'{self.client.name} - {self.name}'


class Invoice(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=40, unique=True)
    pdf_number_override = models.CharField(
        'PDF invoice number override', max_length=40, blank=True,
        help_text='Printed on the PDF only. Does not change the saved number or the sequence.')
    invoice_date = models.DateField(default=timezone.localdate)
    terms_mode = models.CharField('Terms (days until due) type', max_length=12, choices=TERMS_MODES,
                                  default=TERMS_CUSTOM)
    terms_text = models.CharField('Terms', max_length=40, blank=True,
                                  help_text='Printed under Terms for type 2.')
    terms_days = models.PositiveIntegerField('Resolved terms days', null=True, blank=True, default=31,
                                             help_text='Days used for the Due Date; empty when Terms is non-numeric text.')
    due_date = models.DateField('Due Date', null=True, blank=True)
    billing_month = models.DateField(
        'Invoice for (month)', null=True, blank=True,
        help_text='First day of the month this invoice bills for. Empty on invoices saved before this field existed.')
    currency = models.CharField(max_length=8, default='INR')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # --- Tax (defaults: no tax, so grand_total == total for every existing invoice) ---
    TAX_NONE, TAX_CGST_SGST, TAX_IGST = 'none', 'cgst_sgst', 'igst'
    TAX_TYPES = [(TAX_NONE, 'None'), (TAX_CGST_SGST, 'CGST + SGST'), (TAX_IGST, 'IGST')]
    taxable_value = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                        help_text='Defaults to the line total (excluding GST).')
    tax_type = models.CharField(max_length=12, choices=TAX_TYPES, default=TAX_NONE)
    tax_rate = models.DecimalField('Tax rate %', max_digits=5, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=0,
                                      help_text='Taxable value + total tax. Amount the customer owes.')
    # --- Cancellation ---
    cancelled = models.BooleanField(default=False,
                                    help_text='Cancelled invoices are excluded from revenue and receivables.')
    cancelled_date = models.DateField(null=True, blank=True)
    # --- Lifecycle (Stage 4): draft -> finalized (posted to the ledger) -> cancelled (entry reversed) ---
    DRAFT, FINALIZED, CANCELLED = 'draft', 'finalized', 'cancelled'
    STATUSES = [(DRAFT, 'Draft'), (FINALIZED, 'Finalized'), (CANCELLED, 'Cancelled')]
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    # Fields that drive the ledger entry. On a FINALIZED invoice they are locked: corrections go through a
    # credit note (documented policy - see invoice_app/ledger.py).
    LOCKED_FIELDS = ('client_id', 'invoice_date', 'currency', 'taxable_value', 'tax_type', 'tax_rate',
                     'cgst_amount', 'sgst_amount', 'igst_amount', 'grand_total', 'tax_treatment', 'place_of_supply',
                     'format_template_id', 'format_snapshot')
    # --- GST treatment (see gst.py). The amounts above stay the single source of truth. ---
    tax_treatment = models.CharField('Tax treatment', max_length=12, choices=gst.TREATMENTS, default=gst.EXPORT)
    place_of_supply = models.CharField('Place of supply (state code)', max_length=2, blank=True,
                                       choices=gst.STATE_CHOICES,
                                       help_text='Domestic GST only. Defaults to the customer state.')
    # --- PDF format (Stage 6). Null = original default output. Snapshot frozen once not a draft. ---
    po_number = models.CharField('PO number', max_length=60, blank=True)
    format_template = models.ForeignKey(InvoiceFormatTemplate, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name='invoices')
    format_snapshot = models.JSONField(null=True, blank=True, editable=False,
                                       help_text='Format settings at save time; the PDF uses this copy.')

    @property
    def is_tax_invoice(self):
        return self.tax_treatment == gst.DOMESTIC_GST

    class Meta:
        ordering = ['-invoice_date', '-id']

    def apply_tax(self, rounding='rupee_down'):
        """Server-side recompute of all tax fields from taxable_value / tax_type / tax_rate."""
        from . import billing
        self.taxable_value = billing.q2(self.taxable_value)
        rate = billing.to_decimal(self.tax_rate)
        self.cgst_amount = self.sgst_amount = self.igst_amount = billing.q2(0)
        if self.tax_type == self.TAX_CGST_SGST and rate > 0:
            half = billing.round_amount(self.taxable_value * rate / 200, rounding)
            self.cgst_amount = self.sgst_amount = half
        elif self.tax_type == self.TAX_IGST and rate > 0:
            self.igst_amount = billing.round_amount(self.taxable_value * rate / 100, rounding)
        else:
            self.tax_type, self.tax_rate = self.TAX_NONE, billing.q2(0)
        self.total_tax = self.cgst_amount + self.sgst_amount + self.igst_amount
        self.grand_total = self.taxable_value + self.total_tax

    def save(self, *args, **kwargs):
        # Keep tax totals strictly consistent. Invoices created without tax input (older code paths,
        # scripts) get taxable value = line total and no tax, so grand total = total.
        if not self.taxable_value and self.total and not (self.cgst_amount or self.sgst_amount or self.igst_amount):
            self.taxable_value = self.total
        self.total_tax = (self.cgst_amount or 0) + (self.sgst_amount or 0) + (self.igst_amount or 0)
        self.grand_total = (self.taxable_value or 0) + self.total_tax
        if self.cancelled and not self.cancelled_date:
            self.cancelled_date = timezone.localdate()
        if not self.cancelled:
            self.cancelled_date = None
        if self.format_template_id and not self.format_snapshot and self.status == self.DRAFT:
            from .pdf.formats import snapshot_for
            self.format_snapshot = snapshot_for(self.format_template, self.client)
        self._guard_lifecycle()
        if self.cancelled:
            self.status = self.CANCELLED
        elif self.status == self.CANCELLED:
            self.status = self.FINALIZED if self.finalized_at else self.DRAFT
        super().save(*args, **kwargs)

    def _guard_lifecycle(self):
        from django.core.exceptions import ValidationError
        if not self.pk or getattr(self, '_lifecycle', False):
            return
        stored = Invoice.objects.filter(pk=self.pk).values(*self.LOCKED_FIELDS, 'status', 'finalized_at').first()
        if not stored:
            return
        if stored['status'] == self.FINALIZED:
            changed = [f.replace('_id', '') for f in self.LOCKED_FIELDS if stored[f] != getattr(self, f)]
            if changed:
                raise ValidationError(
                    f'Invoice {self.invoice_number} is finalized, so its customer, date and amounts cannot change '
                    f'({", ".join(changed)}). Issue a credit note for a reduction, or cancel it (with a reason) '
                    'and raise a new invoice.')
            if self.cancelled:
                raise ValidationError('A finalized invoice is cancelled with the "Cancel invoice" action, which '
                                      'needs a reason and reverses its ledger entry.')
        if stored['status'] == self.CANCELLED and stored['finalized_at'] and not self.cancelled:
            raise ValidationError('A cancelled finalized invoice cannot be reinstated; raise a new invoice.')

    @property
    def ledger_key(self):
        return f'invoice:{self.pk}'

    @property
    def ledger_entry(self):
        from accounting.postings import current
        return current(self.ledger_key) if self.pk else None

    @property
    def credited_total(self):
        from decimal import Decimal
        if not self.pk:
            return Decimal('0.00')
        return sum((c.total for c in self.credit_notes.all() if c.status == CreditNote.ACTIVE), Decimal('0.00'))

    # --- Receipts (derived from InvoicePayment; never stored) ---
    @property
    def amount_due(self):
        return self.grand_total

    def payment_summary(self):
        """OUTSTANDING POLICY: outstanding = grand total - amount received - TDS/bank charges written off
        - active credit notes."""
        from decimal import Decimal
        rec = tds = Decimal('0.00')
        for p in self.payments.all():
            rec += p.amount
            tds += p.deductions
        credited = self.credited_total
        settled = rec + tds
        due = self.amount_due
        out = due - settled - credited
        if settled + credited <= 0:
            status = 'Unpaid'
        elif out > 0:
            status = 'Partially Paid'
        elif out == 0:
            status = 'Paid'
        else:
            status = 'Overpaid'
        if self.cancelled:
            status = 'Cancelled'
        return {'received': rec, 'deductions': tds, 'settled': settled, 'credited': credited, 'outstanding': out,
                'status': status}

    @property
    def terms_display(self):
        """Exactly what the PDF prints under Terms."""
        if self.terms_mode == TERMS_MONTH_DAYS:
            return '' if self.terms_days is None else str(self.terms_days)
        return self.terms_text if self.terms_text else ('' if self.terms_days is None else str(self.terms_days))

    @property
    def billing_month_label(self):
        return self.billing_month.strftime('%B %Y') if self.billing_month else ''

    @property
    def display_number(self):
        return self.pdf_number_override or self.invoice_number

    def __str__(self):
        return self.invoice_number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    project = models.ForeignKey(ProjectTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    position = models.PositiveIntegerField(default=0)
    project_name = models.CharField(max_length=180)
    description = models.CharField(max_length=240, blank=True)
    description_template = models.ForeignKey(
        'InvoiceDescriptionTemplate', null=True, blank=True, on_delete=models.PROTECT, related_name='used_on_lines',
        help_text='Template configured on the project when this line was saved (audit/delete protection only; '
                  'the PDF prints the saved description).')
    quantity_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text='Blank for billing type 3')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_overridden = models.BooleanField(default=False)
    # GST snapshot taken from the project when the line is saved (later project edits never change it).
    sac_code = models.CharField('SAC code', max_length=10, blank=True)
    gst_rate = models.DecimalField('GST rate %', max_digits=5, decimal_places=2, default=0)

    class Meta:
        ordering = ['position', 'id']


class InvoicePayment(models.Model):
    """A customer receipt against an invoice. ``deductions`` = TDS / bank charges the customer
    withheld; it is written off against the invoice but is NOT cash received."""
    MODES = [('bank', 'Bank Transfer'), ('upi', 'UPI'), ('cheque', 'Cheque'), ('cash', 'Cash'),
             ('wire', 'Wire'), ('other', 'Other')]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments')
    receipt_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField('Amount received', max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=10, choices=MODES, default='bank')
    reference = models.CharField('Reference / UTR', max_length=80, blank=True)
    deductions = models.DecimalField('TDS / bank charges deducted', max_digits=14, decimal_places=2, default=0)
    bank_charges = models.DecimalField('of which bank charges', max_digits=14, decimal_places=2, default=0,
                                       help_text='Part of "TDS / bank charges" that is bank charges; the rest is TDS.')
    bank_account = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='invoice_receipts',
                                     help_text='Blank = the default bank account.')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-receipt_date', '-id']
        indexes = [models.Index(fields=['invoice']), models.Index(fields=['receipt_date'])]

    def __str__(self):
        return f'{self.invoice} {self.receipt_date} {self.amount}'

    @property
    def tds_amount(self):
        return (self.deductions or 0) - (self.bank_charges or 0)

    def save(self, *args, **kwargs):
        """Saving a receipt of a posted invoice posts it; an edit reverses the old entry and reposts."""
        from django.db import transaction
        from accounting import postings
        with transaction.atomic():
            super().save(*args, **kwargs)
            postings.post_receipt(self)

    def delete(self, *args, **kwargs):
        from django.db import transaction
        from accounting import postings
        with transaction.atomic():
            postings.unpost(postings.receipt_key(self), 'Receipt deleted')
            return super().delete(*args, **kwargs)


class CreditNote(models.Model):
    """Reduces a finalized invoice: reverses income and output GST proportionally and credits the customer."""
    ACTIVE, CANCELLED = 'active', 'cancelled'
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='credit_notes')
    number = models.CharField(max_length=50, unique=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    reason = models.CharField(max_length=300)
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2)
    cgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=[(ACTIVE, 'Active'), (CANCELLED, 'Cancelled')], default=ACTIVE)
    cancel_reason = models.CharField(max_length=300, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number

    @property
    def total_tax(self):
        return self.cgst_amount + self.sgst_amount + self.igst_amount

    def apply_proportional_tax(self):
        """Tax = invoice tax x (credit taxable / invoice taxable), per tax head, rounded to paise (q2)."""
        from accounting.money import q2
        inv = self.invoice
        ratio = (self.taxable_amount / inv.taxable_value) if inv.taxable_value else 0
        self.cgst_amount = q2(inv.cgst_amount * ratio)
        self.sgst_amount = q2(inv.sgst_amount * ratio)
        self.igst_amount = q2(inv.igst_amount * ratio)

    def save(self, *args, **kwargs):
        from accounting.money import q2
        if not self.number:
            n = CreditNote.objects.filter(invoice=self.invoice).count() + 1
            while CreditNote.objects.filter(number=f'CN-{self.invoice.invoice_number}-{n}').exists():
                n += 1
            self.number = f'CN-{self.invoice.invoice_number}-{n}'
        self.total = q2(self.taxable_amount + self.cgst_amount + self.sgst_amount + self.igst_amount)
        super().save(*args, **kwargs)
