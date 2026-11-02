"""Data model for QuantBud Invoice Studio.

Money is always ``Decimal``; see ``billing.py`` for the single rounding policy.
"""
from django.db import models
from django.utils import timezone

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


class Client(models.Model):
    name = models.CharField('Company / customer name', max_length=180)
    address = models.TextField(blank=True, help_text='One line per PDF line, e.g. street / city, state / country - zip')
    country = models.CharField(max_length=80, blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField('Contact number', max_length=60, blank=True)
    tax_id = models.CharField('Tax information', max_length=60, blank=True)
    default_terms_days = models.PositiveIntegerField('Default payment terms (days)', null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ProjectTemplate(models.Model):
    FIXED = 'fixed'
    VARIABLE = 'variable'
    PRICING = [(FIXED, 'Fixed monthly amount'), (VARIABLE, 'Variable (daily rate x days)')]
    CALENDAR = 'calendar'
    THIRTY = '30'
    BASIS = [(CALENDAR, 'Actual calendar days in month (28-31)'), (THIRTY, 'Fixed 30 days')]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField('Consultant - Project Name', max_length=180)
    description = models.CharField('Default Memo/Description', max_length=240, blank=True, default='Support')
    pricing_type = models.CharField('Billing type', max_length=12, choices=PRICING, default=FIXED)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fixed_monthly_even_if_partial = models.BooleanField(
        'Charge full monthly amount regardless of days', default=False,
        help_text='Option C. Unticked = Option B (prorated by the divisor below).')
    proration_basis = models.CharField('Day calculation method (divisor)', max_length=12, choices=BASIS, default=CALENDAR)
    daily_rate = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text='Used for Variable billing')

    class Meta:
        ordering = ['name']

    @property
    def method_label(self):
        if self.pricing_type == self.VARIABLE:
            return 'A: daily rate x days'
        if self.fixed_monthly_even_if_partial:
            return 'C: full monthly amount'
        return 'B: monthly / %s x days' % ('30' if self.proration_basis == self.THIRTY else 'calendar days')

    def __str__(self):
        return f'{self.client.name} - {self.name}'


class Invoice(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=40, unique=True)
    pdf_number_override = models.CharField(
        'PDF invoice number override', max_length=40, blank=True,
        help_text='Printed on the PDF only. Does not change the saved number or the sequence.')
    invoice_date = models.DateField(default=timezone.localdate)
    terms_days = models.PositiveIntegerField(default=31)
    due_date = models.DateField()
    currency = models.CharField(max_length=8, default='INR')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ['-invoice_date', '-id']

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
    quantity_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ['position', 'id']
