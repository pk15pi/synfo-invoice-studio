"""Expense Management & Employee Reimbursement.

History rules
* Company expenses and claims are never hard-deleted; they are cancelled with a reason.
* Vendor payments (CompanyExpensePayment) and employee reimbursements (EmployeeReimbursement)
  are separate, append-only transaction tables; they are only created via services.py
  inside transaction.atomic.
* Attachments live in the private store (settings.EMPLOYEE_PRIVATE_ROOT/expenses/...) and
  are served only by local-only controlled views.
"""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils import timezone

from employee_management.storage import private_storage
from employee_management.validators import mask

ZERO = Decimal('0.00')
MONEY = dict(max_digits=14, decimal_places=2, default=ZERO)
RATE = dict(max_digits=12, decimal_places=4, default=Decimal('1'))
Q2 = Decimal('0.01')

CURRENCIES = [('INR', 'INR (₹)'), ('USD', 'USD ($)'), ('EUR', 'EUR (€)'), ('GBP', 'GBP (£)'), ('AED', 'AED'),
              ('SGD', 'SGD'), ('AUD', 'AUD'), ('CAD', 'CAD'), ('JPY', 'JPY'), ('OTHER', 'Other')]

PAYMENT_METHODS = [('bank_transfer', 'Bank Transfer'), ('upi', 'UPI'), ('credit_card', 'Credit Card'),
                   ('debit_card', 'Debit Card'), ('cash', 'Cash'), ('other', 'Other')]


def q2(v):
    return (v or ZERO).quantize(Q2)


class ExpenseSettings(models.Model):
    """Singleton (pk=1): ID prefixes, sequence counters and attachment limit."""
    expense_prefix = models.CharField(max_length=10, default='EXP')
    claim_prefix = models.CharField(max_length=10, default='CLM')
    vendor_prefix = models.CharField(max_length=10, default='VND')
    number_padding = models.PositiveSmallIntegerField(default=4)
    next_expense_number = models.PositiveIntegerField(default=1)
    next_claim_number = models.PositiveIntegerField(default=1)
    next_vendor_number = models.PositiveIntegerField(default=1)
    max_attachment_mb = models.PositiveSmallIntegerField('Max attachment size (MB)', default=10)
    default_currency = models.CharField(max_length=5, choices=CURRENCIES, default='INR')

    class Meta:
        verbose_name = 'expense settings'

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def next_code(cls, kind, model, field):
        """Allocate the next unique code (e.g. EXP-0001) atomically; skips codes already used."""
        with transaction.atomic():
            cls.load()
            counter = f'next_{kind}_number'
            while True:
                cls.objects.filter(pk=1).update(**{counter: F(counter) + 1})
                s = cls.objects.get(pk=1)
                n = getattr(s, counter) - 1
                code = f'{getattr(s, kind + "_prefix")}-{n:0{s.number_padding}d}'
                if not model.objects.filter(**{field: code}).exists():
                    return code


# --------------------------------------------------------------------------- categories
class ExpenseCategory(models.Model):
    COMPANY, CLAIM, BOTH = 'company', 'claim', 'both'
    TYPES = [(COMPANY, 'Company Expense'), (CLAIM, 'Employee Claim'), (BOTH, 'Both')]
    name = models.CharField(max_length=120, unique=True)
    category_type = models.CharField(max_length=10, choices=TYPES, default=BOTH, db_index=True)
    description = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'expense categories'

    def __str__(self):
        return self.name

    @property
    def in_use(self):
        return self.company_expenses.exists() or self.claims.exists()

    @classmethod
    def for_company(cls):
        return cls.objects.filter(active=True, category_type__in=[cls.COMPANY, cls.BOTH])

    @classmethod
    def for_claims(cls):
        return cls.objects.filter(active=True, category_type__in=[cls.CLAIM, cls.BOTH])


class ExpenseSubcategory(models.Model):
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='subcategories')
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category__name', 'name']
        constraints = [models.UniqueConstraint(fields=['category', 'name'], name='exp_subcat_unique')]
        verbose_name_plural = 'expense subcategories'

    def __str__(self):
        return f'{self.category.name} / {self.name}'

    @property
    def in_use(self):
        return self.company_expenses.exists() or self.claims.exists()


# --------------------------------------------------------------------------- vendors
class Vendor(models.Model):
    TYPES = [('hosting', 'Hosting provider'), ('software', 'Software vendor'), ('internet', 'Internet provider'),
             ('office', 'Office supplier'), ('travel', 'Travel agency'), ('consultant', 'Consultant'),
             ('professional', 'Professional services'), ('utility', 'Utility'), ('landlord', 'Landlord'),
             ('other', 'Other')]
    ACTIVE, INACTIVE = 'active', 'inactive'
    vendor_code = models.CharField('Vendor ID', max_length=20, unique=True, editable=False)
    name = models.CharField('Vendor name', max_length=180, db_index=True)
    vendor_type = models.CharField(max_length=15, choices=TYPES, default='other')
    contact_person = models.CharField(max_length=120, blank=True)
    mobile = models.CharField('Mobile number', max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=80, default='India', blank=True)
    gstin = models.CharField('GSTIN', max_length=15, blank=True, db_index=True)
    pan = models.CharField('PAN', max_length=10, blank=True)
    website = models.URLField(blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=34, blank=True)
    ifsc = models.CharField('IFSC', max_length=11, blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=[(ACTIVE, 'Active'), (INACTIVE, 'Inactive')], default=ACTIVE,
                              db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def account_masked(self):
        return mask(self.bank_account_number)

    def save(self, *a, **kw):
        if not self.vendor_code:
            self.vendor_code = ExpenseSettings.next_code('vendor', Vendor, 'vendor_code')
        self.gstin = (self.gstin or '').upper().strip()
        self.pan = (self.pan or '').upper().strip()
        self.ifsc = (self.ifsc or '').upper().strip()
        super().save(*a, **kw)


# --------------------------------------------------------------------------- company expenses
class CompanyExpense(models.Model):
    UNPAID, PARTIAL, PAID, CANCELLED = 'unpaid', 'partial', 'paid', 'cancelled'
    STATUSES = [(UNPAID, 'Unpaid'), (PARTIAL, 'Partially Paid'), (PAID, 'Paid'), (CANCELLED, 'Cancelled')]
    BREAKUP, DIRECT = 'breakup', 'direct'
    MODES = [(BREAKUP, 'Calculate from tax breakup'), (DIRECT, 'Enter final invoice amount directly')]

    expense_id = models.CharField('Expense ID', max_length=20, unique=True, editable=False)
    expense_date = models.DateField(db_index=True)
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.PROTECT, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='company_expenses')
    subcategory = models.ForeignKey(ExpenseSubcategory, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='company_expenses')
    description = models.CharField(max_length=300)
    invoice_number = models.CharField('Invoice / bill number', max_length=60, blank=True, db_index=True)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField('Payment due date', null=True, blank=True)
    amount_mode = models.CharField('Amount entry', max_length=8, choices=MODES, default=BREAKUP)
    amount_before_tax = models.DecimalField(**MONEY)
    gst_amount = models.DecimalField('GST amount', **MONEY)
    cgst_amount = models.DecimalField('CGST', **MONEY)
    sgst_amount = models.DecimalField('SGST', **MONEY)
    igst_amount = models.DecimalField('IGST', **MONEY)
    other_taxes = models.DecimalField(**MONEY)
    discount = models.DecimalField(**MONEY)
    total_amount = models.DecimalField('Total invoice amount', **MONEY)
    currency = models.CharField(max_length=5, choices=CURRENCIES, default='INR')
    exchange_rate = models.DecimalField('Exchange rate to INR', help_text='1 for INR. Used to show INR totals in reports.',
                                        **RATE)
    payment_status = models.CharField(max_length=10, choices=STATUSES, default=UNPAID, db_index=True)
    client = models.ForeignKey('invoice_app.Client', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='company_expenses')
    project = models.ForeignKey('invoice_app.ProjectTemplate', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='company_expenses')
    department = models.ForeignKey('employee_management.Department', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='company_expenses')
    employee = models.ForeignKey('employee_management.Employee', null=True, blank=True, on_delete=models.PROTECT,
                                 related_name='company_expenses')
    remarks = models.TextField(blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-expense_date', '-id']
        indexes = [models.Index(fields=['category', 'expense_date'], name='exp_cat_date_idx'),
                   models.Index(fields=['vendor', 'expense_date'], name='exp_vendor_date_idx')]

    def __str__(self):
        return f'{self.expense_id} - {self.description[:40]}'

    @property
    def is_cancelled(self):
        return self.payment_status == self.CANCELLED

    def compute_total(self):
        return q2(self.amount_before_tax + self.gst_amount + self.other_taxes - self.discount)

    def clean(self):
        errors = {}
        for f in ('amount_before_tax', 'gst_amount', 'cgst_amount', 'sgst_amount', 'igst_amount', 'other_taxes',
                  'discount', 'total_amount'):
            if (getattr(self, f) or ZERO) < 0:
                errors[f] = 'Amounts cannot be negative.'
        split = (self.cgst_amount or ZERO) + (self.sgst_amount or ZERO) + (self.igst_amount or ZERO)
        if split and not self.gst_amount:
            self.gst_amount = q2(split)
        if split and q2(split) != q2(self.gst_amount):
            errors['gst_amount'] = f'CGST + SGST + IGST ({q2(split)}) must equal the GST amount ({q2(self.gst_amount)}).'
        if self.amount_mode == self.BREAKUP:
            if not errors:
                self.total_amount = self.compute_total()
        if not errors and (self.total_amount or ZERO) <= 0:
            errors['total_amount'] = 'Total invoice amount must be greater than zero.'
        if self.subcategory_id and self.category_id and self.subcategory.category_id != self.category_id:
            errors['subcategory'] = 'Subcategory does not belong to the selected category.'
        if self.project_id:
            if self.client_id and self.project.client_id != self.client_id:
                errors['project'] = 'Project belongs to a different client.'
            elif not self.client_id:
                self.client_id = self.project.client_id
        if (self.exchange_rate or 0) <= 0:
            errors['exchange_rate'] = 'Exchange rate must be positive.'
        if self.currency == 'INR':
            self.exchange_rate = Decimal('1')
        if self.pk and not errors:
            paid = self.paid_amount
            if q2(self.total_amount) < paid:
                errors['total_amount'] = f'Total cannot be less than the amount already paid ({paid}).'
        if errors:
            raise ValidationError(errors)

    def save(self, *a, **kw):
        if not self.expense_id:
            self.expense_id = ExpenseSettings.next_code('expense', CompanyExpense, 'expense_id')
        if self.amount_mode == self.BREAKUP:
            self.total_amount = self.compute_total()
        with transaction.atomic():
            super().save(*a, **kw)
            from .ledger import post_expense
            post_expense(self)

    @property
    def paid_amount(self):
        if not self.pk:
            return ZERO
        return q2(self.payments.aggregate(s=Sum(F('amount') + F('tds_amount')))['s'])

    @property
    def outstanding_amount(self):
        if self.is_cancelled:
            return ZERO
        return q2(self.total_amount - self.paid_amount)

    @property
    def total_inr(self):
        return q2(self.total_amount * self.exchange_rate)

    def derive_status(self):
        if self.is_cancelled:
            return self.CANCELLED
        paid = self.paid_amount
        if paid <= 0:
            return self.UNPAID
        return self.PAID if paid >= q2(self.total_amount) else self.PARTIAL

    def refresh_status(self):
        new = self.derive_status()
        if new != self.payment_status:
            CompanyExpense.objects.filter(pk=self.pk).update(payment_status=new, updated_at=timezone.now())
            self.payment_status = new
        return new


class CompanyExpensePayment(models.Model):
    expense = models.ForeignKey(CompanyExpense, on_delete=models.PROTECT, related_name='payments')
    payment_date = models.DateField(db_index=True)
    method = models.CharField('Payment method', max_length=15, choices=PAYMENT_METHODS)
    reference = models.CharField('Transaction reference number', max_length=80, blank=True, db_index=True)
    bank_account = models.CharField('Bank account used', max_length=120, blank=True)
    bank = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                             related_name='expense_payments', verbose_name='Paid from (ledger)')
    amount = models.DecimalField('Paid amount', max_digits=14, decimal_places=2)
    tds_amount = models.DecimalField('TDS deducted', **MONEY)
    remarks = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['payment_date', 'id']

    def __str__(self):
        return f'{self.expense.expense_id} payment {self.amount} on {self.payment_date}'

    def delete(self, *a, **kw):
        raise ValidationError('Payment records are permanent history and cannot be deleted.')


# --------------------------------------------------------------------------- employee claims
class EmployeeExpenseClaim(models.Model):
    DRAFT, SUBMITTED, UNDER_REVIEW = 'draft', 'submitted', 'under_review'
    APPROVED, PARTIALLY_APPROVED, REJECTED = 'approved', 'partially_approved', 'rejected'
    REIMBURSED, CANCELLED = 'reimbursed', 'cancelled'
    STATUSES = [(DRAFT, 'Draft'), (SUBMITTED, 'Submitted'), (UNDER_REVIEW, 'Under Review'), (APPROVED, 'Approved'),
                (PARTIALLY_APPROVED, 'Partially Approved'), (REJECTED, 'Rejected'), (REIMBURSED, 'Reimbursed'),
                (CANCELLED, 'Cancelled')]
    PENDING = (SUBMITTED, UNDER_REVIEW)
    APPROVED_STATES = (APPROVED, PARTIALLY_APPROVED, REIMBURSED)
    EDITABLE = (DRAFT, SUBMITTED, UNDER_REVIEW)

    claim_id = models.CharField('Claim ID', max_length=20, unique=True, editable=False)
    employee = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT, related_name='expense_claims')
    # Snapshot of the employee at claim time (the live record stays authoritative).
    snap_employee_code = models.CharField(max_length=20, blank=True)
    snap_name = models.CharField(max_length=250, blank=True)
    snap_department = models.CharField(max_length=120, blank=True)
    snap_designation = models.CharField(max_length=120, blank=True)
    department = models.ForeignKey('employee_management.Department', null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='expense_claims')
    claim_date = models.DateField(db_index=True)
    expense_date = models.DateField(db_index=True)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='claims')
    subcategory = models.ForeignKey(ExpenseSubcategory, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name='claims')
    description = models.CharField(max_length=300)
    amount_claimed = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=5, choices=CURRENCIES, default='INR')
    exchange_rate = models.DecimalField('Exchange rate to INR', **RATE)
    business_purpose = models.CharField(max_length=300, blank=True)
    client = models.ForeignKey('invoice_app.Client', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='expense_claims')
    project = models.ForeignKey('invoice_app.ProjectTemplate', null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='expense_claims')
    travel_from = models.CharField(max_length=120, blank=True)
    travel_to = models.CharField(max_length=120, blank=True)
    travel_start_date = models.DateField(null=True, blank=True)
    travel_end_date = models.DateField(null=True, blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default=DRAFT, db_index=True)
    approved_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    rejection_reason = models.CharField(max_length=300, blank=True)
    review_remarks = models.TextField(blank=True)
    returned_for_correction = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-claim_date', '-id']
        indexes = [models.Index(fields=['employee', 'status'], name='claim_emp_status_idx'),
                   models.Index(fields=['category', 'expense_date'], name='claim_cat_date_idx')]

    def __str__(self):
        return f'{self.claim_id} - {self.snap_name or self.employee_id}'

    def clean(self):
        errors = {}
        if self.amount_claimed is not None and self.amount_claimed <= 0:
            errors['amount_claimed'] = 'Amount claimed must be greater than zero.'
        if self.travel_start_date and self.travel_end_date and self.travel_end_date < self.travel_start_date:
            errors['travel_end_date'] = 'Travel end date is before the start date.'
        if self.subcategory_id and self.category_id and self.subcategory.category_id != self.category_id:
            errors['subcategory'] = 'Subcategory does not belong to the selected category.'
        if self.project_id:
            if self.client_id and self.project.client_id != self.client_id:
                errors['project'] = 'Project belongs to a different client.'
            elif not self.client_id:
                self.client_id = self.project.client_id
        if self.currency == 'INR':
            self.exchange_rate = Decimal('1')
        if (self.exchange_rate or 0) <= 0:
            errors['exchange_rate'] = 'Exchange rate must be positive.'
        if errors:
            raise ValidationError(errors)

    def snapshot_employee(self):
        e = self.employee
        self.snap_employee_code = e.employee_id
        self.snap_name = e.full_name
        self.snap_department = e.department.name if e.department_id else ''
        self.snap_designation = e.designation
        self.department_id = e.department_id

    def save(self, *a, **kw):
        if not self.claim_id:
            self.claim_id = ExpenseSettings.next_code('claim', EmployeeExpenseClaim, 'claim_id')
        if self.employee_id and (not self.snap_employee_code or self.status in self.EDITABLE):
            self.snapshot_employee()
        with transaction.atomic():
            super().save(*a, **kw)
            from .ledger import post_claim
            post_claim(self)

    @property
    def reimbursed_amount(self):
        if not self.pk:
            return ZERO
        return q2(self.reimbursements.aggregate(s=Sum('amount'))['s'])

    @property
    def outstanding_amount(self):
        if self.status not in self.APPROVED_STATES or self.approved_amount is None:
            return ZERO
        return q2(self.approved_amount - self.reimbursed_amount)

    @property
    def reimbursement_state(self):
        paid = self.reimbursed_amount
        if paid <= 0:
            return 'none'
        return 'full' if self.outstanding_amount <= 0 else 'partial'

    @property
    def status_label(self):
        if self.status in (self.APPROVED, self.PARTIALLY_APPROVED) and self.reimbursement_state == 'partial':
            return f'{self.get_status_display()} · Partially Reimbursed'
        if self.status == self.DRAFT and self.returned_for_correction:
            return 'Draft · Returned for Correction'
        return self.get_status_display()

    @property
    def approved_inr(self):
        return q2((self.approved_amount or ZERO) * self.exchange_rate)


class EmployeeClaimReview(models.Model):
    ACTIONS = [('submit', 'Submitted'), ('start_review', 'Review started'), ('approve', 'Approved'),
               ('partial', 'Partially approved'), ('reject', 'Rejected'), ('return', 'Returned for correction'),
               ('reimburse', 'Reimbursement recorded'), ('cancel', 'Cancelled'), ('edit', 'Edited')]
    claim = models.ForeignKey(EmployeeExpenseClaim, on_delete=models.CASCADE, related_name='reviews')
    action = models.CharField(max_length=15, choices=ACTIONS)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    remarks = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at', 'id']


class EmployeeReimbursement(models.Model):
    claim = models.ForeignKey(EmployeeExpenseClaim, on_delete=models.PROTECT, related_name='reimbursements')
    reimbursement_date = models.DateField(db_index=True)
    amount = models.DecimalField('Amount reimbursed', max_digits=14, decimal_places=2)
    method = models.CharField('Payment method', max_length=15, choices=PAYMENT_METHODS)
    bank_account = models.CharField('Bank account used', max_length=120, blank=True)
    bank = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                             related_name='reimbursements', verbose_name='Paid from (ledger)')
    reference = models.CharField('Transaction reference number', max_length=80, blank=True, db_index=True)
    remarks = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['reimbursement_date', 'id']

    def delete(self, *a, **kw):
        raise ValidationError('Reimbursement records are permanent history and cannot be deleted.')


# --------------------------------------------------------------------------- attachments
def _upload_path(prefix, instance, filename):
    ext = getattr(instance, '_validated_ext', None) or 'bin'
    return f'expenses/{prefix}/{instance.parent_id_for_path()}/{uuid.uuid4().hex}.{ext}'


def company_upload_to(instance, filename):
    return _upload_path('company', instance, filename)


def claim_upload_to(instance, filename):
    return _upload_path('claims', instance, filename)


class AttachmentBase(models.Model):
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=60, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(default=timezone.now)
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        abstract = True

    @property
    def is_image(self):
        return self.content_type.startswith('image/')


class CompanyExpenseAttachment(AttachmentBase):
    TYPES = [('vendor_invoice', 'Vendor Invoice'), ('payment_receipt', 'Payment Receipt'),
             ('supporting', 'Supporting Document'), ('purchase_order', 'Purchase Order'), ('other', 'Other Document')]
    expense = models.ForeignKey(CompanyExpense, on_delete=models.PROTECT, related_name='attachments')
    doc_type = models.CharField('Document type', max_length=20, choices=TYPES, default='vendor_invoice')
    file = models.FileField(storage=private_storage, upload_to=company_upload_to, max_length=255)

    def parent_id_for_path(self):
        return self.expense_id


class EmployeeExpenseAttachment(AttachmentBase):
    TYPES = [('travel_ticket', 'Travel Ticket'), ('hotel_bill', 'Hotel Bill'), ('food_bill', 'Food Bill'),
             ('taxi_receipt', 'Taxi Receipt'), ('fuel_bill', 'Fuel Bill'), ('other', 'Other Supporting Document')]
    claim = models.ForeignKey(EmployeeExpenseClaim, on_delete=models.PROTECT, related_name='attachments')
    doc_type = models.CharField('Document type', max_length=20, choices=TYPES, default='other')
    file = models.FileField(storage=private_storage, upload_to=claim_upload_to, max_length=255)

    def parent_id_for_path(self):
        return self.claim_id
