from decimal import Decimal
from django.db import models
from django.utils import timezone

class BusinessProfile(models.Model):
    name = models.CharField(max_length=180, default='QUANTBUD LLP')
    address = models.TextField(default='57, Street No. 1, Sector-72\nGurugram, Haryana Pin-122101')
    phone = models.CharField(max_length=60, default='6033430700', blank=True)
    email = models.EmailField(default='pk15pi@gmail.com', blank=True)
    gstin = models.CharField(max_length=40, default='06AABFQ2999K1ZQ', blank=True)
    bank_name = models.CharField(max_length=120, default='IDFC First Bank', blank=True)
    account_holder = models.CharField(max_length=180, default='QuantBud LLP', blank=True)
    account_number = models.CharField(max_length=80, default='10217638591', blank=True)
    bank_country = models.CharField(max_length=80, default='INDIA', blank=True)
    ifsc = models.CharField(max_length=40, default='IDFB0021014', blank=True)
    invoice_prefix = models.CharField(max_length=20, default='QB')
    next_invoice_number = models.PositiveIntegerField(default=21)
    payment_terms_days = models.PositiveIntegerField(default=31)
    primary_color = models.CharField(max_length=7, default='#0B5394')
    alternate_row_color = models.CharField(max_length=7, default='#CFE2F3')
    signature_text = models.CharField(max_length=180, default='QuantBud LLP', blank=True)
    footer_text = models.CharField(max_length=180, default='Thank you for your business!', blank=True)

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def next_number(self):
        return f'{self.invoice_prefix}{self.next_invoice_number:03d}'

    def __str__(self): return self.name

class Client(models.Model):
    name = models.CharField(max_length=180)
    address = models.TextField(blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    tax_id = models.CharField(max_length=60, blank=True)
    default_terms_days = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    def __str__(self): return self.name

class ProjectTemplate(models.Model):
    FIXED = 'fixed'
    VARIABLE = 'variable'
    PRICING = [(FIXED, 'Fixed monthly amount'), (VARIABLE, 'Variable / daily rate')]
    CALENDAR = 'calendar'
    THIRTY = '30'
    BASIS = [(CALENDAR, 'Actual calendar days in month'), (THIRTY, 'Always use 30 days')]
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='projects')
    name = models.CharField(max_length=180)
    description = models.CharField(max_length=240, blank=True, default='Support')
    pricing_type = models.CharField(max_length=12, choices=PRICING, default=FIXED)
    monthly_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    daily_rate = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    proration_basis = models.CharField(max_length=12, choices=BASIS, default=THIRTY)
    fixed_monthly_even_if_partial = models.BooleanField(default=False)
    def __str__(self): return f'{self.client.name} – {self.name}'

class Invoice(models.Model):
    client = models.ForeignKey(Client, on_delete=models.PROTECT)
    invoice_number = models.CharField(max_length=40, unique=True)
    invoice_date = models.DateField(default=timezone.localdate)
    terms_days = models.PositiveIntegerField(default=31)
    due_date = models.DateField()
    currency = models.CharField(max_length=8, default='INR')
    created_at = models.DateTimeField(auto_now_add=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    def __str__(self): return self.invoice_number

class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    project_name = models.CharField(max_length=180)
    description = models.CharField(max_length=240, blank=True)
    quantity_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
