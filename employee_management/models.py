"""Employee Management & Payroll models.

History rules
* Employees are never hard-deleted (Employee.department is PROTECT; no delete view).
* Bank accounts and salary structures are append-only: a change creates a new row.
* EmployeePayroll rows are frozen once finalized (save() guard); corrections create a
  new revision that points at the original, which is kept.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import ProtectedError, Q
from django.utils import timezone

from .storage import document_upload_to, photo_upload_to, private_storage, signature_upload_to
from .validators import (mask, validate_aadhaar, validate_esic, validate_ifsc, validate_pan, validate_phone,
                         validate_pin, validate_uan)

ZERO = Decimal('0.00')
MONEY = dict(max_digits=14, decimal_places=2, default=ZERO)
DAYS = dict(max_digits=6, decimal_places=2, default=ZERO)


# --------------------------------------------------------------------------- settings
class PayrollSettings(models.Model):
    """Singleton (pk=1). Company details come from invoice_app.BusinessProfile."""
    DIV_ACTUAL = 'actual'
    DIV_FIXED30 = 'fixed30'
    DIVISORS = [(DIV_ACTUAL, 'Actual calendar days in the month (28-31)'), (DIV_FIXED30, 'Fixed 30-day divisor')]
    ROUND_2DP = 'half_up_2dp'
    ROUND_RUPEE = 'net_nearest_rupee'
    ROUNDING = [(ROUND_RUPEE, '2 decimals, then Net Salary to nearest rupee (0.50 rounds up; adjustment shown)'),
                (ROUND_2DP, 'Every amount to 2 decimals (ROUND_HALF_UP)')]

    employee_id_prefix = models.CharField('Employee ID prefix', max_length=10, default='EMP')
    next_employee_sequence = models.PositiveIntegerField('Next employee sequence', default=1)
    employee_id_padding = models.PositiveSmallIntegerField('Digits in employee number', default=3)
    default_divisor = models.CharField('Default salary divisor', max_length=10, choices=DIVISORS, default=DIV_ACTUAL)
    payroll_start_day = models.PositiveSmallIntegerField(
        'Payroll start day', default=1, help_text='Informational: payroll periods are calendar months (day 1 to month end).')
    standard_working_days = models.PositiveSmallIntegerField('Standard working days per month', default=26)
    standard_working_hours = models.DecimalField('Standard working hours per day', max_digits=5, decimal_places=2,
                                                 default=Decimal('8.00'))
    rounding = models.CharField('Salary rounding rule', max_length=20, choices=ROUNDING, default=ROUND_RUPEE)
    payslip_footer = models.CharField(max_length=240, blank=True,
                                      default='This is a computer-generated payslip.')
    default_payroll_month = models.DateField('Default payroll month', null=True, blank=True,
                                             help_text='Any date in the month. Blank = previous calendar month.')
    use_business_signature = models.BooleanField(
        'Use Business Profile signature on payslips', default=True)
    signature_image = models.FileField('Payslip signature (PNG/JPG)', storage=private_storage,
                                       upload_to=signature_upload_to, blank=True)
    signatory_name = models.CharField('Authorised signatory label', max_length=120, blank=True,
                                      default='Authorised Signatory')
    document_max_mb = models.PositiveSmallIntegerField('Max document size (MB)', default=5)
    expiry_alert_days = models.PositiveSmallIntegerField('Warn about documents expiring within (days)', default=30)

    class Meta:
        verbose_name_plural = 'payroll settings'

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def format_employee_id(self, n):
        return f'{self.employee_id_prefix}{n:0{self.employee_id_padding}d}'

    def default_month(self):
        if self.default_payroll_month:
            return self.default_payroll_month.year, self.default_payroll_month.month
        first = timezone.localdate().replace(day=1) - timedelta(days=1)
        return first.year, first.month


class StatutoryRule(models.Model):
    """User-configured statutory deduction/contribution. Nothing is hardcoded: every
    rate, base, ceiling and threshold must be verified by an accountant."""
    CODES = [('pf', 'Provident Fund (PF)'), ('esi', 'Employee State Insurance (ESI)'),
             ('professional_tax', 'Professional Tax'), ('tds', 'TDS'), ('other_deduction', 'Other')]
    EMPLOYEE, EMPLOYER = 'employee', 'employer'
    SIDES = [(EMPLOYEE, 'Employee deduction (reduces net salary)'),
             (EMPLOYER, 'Employer contribution (does NOT reduce net salary)')]
    PERCENT, FIXED = 'percent', 'fixed'
    CALC = [(PERCENT, 'Percentage of base'), (FIXED, 'Fixed amount')]
    BASES = [('basic', 'Basic (earned)'), ('gross', 'Gross earnings')]

    code = models.CharField(max_length=20, choices=CODES)
    name = models.CharField(max_length=80)
    side = models.CharField(max_length=10, choices=SIDES, default=EMPLOYEE)
    calculation = models.CharField(max_length=10, choices=CALC, default=PERCENT)
    rate = models.DecimalField('Rate (%) or fixed amount', max_digits=10, decimal_places=4, default=0)
    base = models.CharField('Percentage base', max_length=10, choices=BASES, default='basic')
    base_ceiling = models.DecimalField('Cap the base at (optional)', max_digits=14, decimal_places=2,
                                       null=True, blank=True)
    applies_if_gross_upto = models.DecimalField('Applies only if gross is at most (optional)', max_digits=14,
                                                decimal_places=2, null=True, blank=True)
    applies_if_gross_from = models.DecimalField('Applies only if gross is at least (optional)', max_digits=14,
                                                decimal_places=2, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=240, blank=True, default='Verify with your accountant / payroll professional.')

    class Meta:
        ordering = ['code', '-effective_from']

    def __str__(self):
        return f'{self.name} ({self.get_side_display()})'

    def applies_on(self, d):
        return self.active and self.effective_from <= d and (self.effective_to is None or self.effective_to >= d)


# --------------------------------------------------------------------------- employees
class Department(models.Model):
    name = models.CharField('Department name', max_length=120, unique=True)
    code = models.CharField('Department code', max_length=20, unique=True)
    description = models.TextField('Department description', blank=True)
    head = models.ForeignKey('Employee', verbose_name='Department head', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='heads_departments')
    active = models.BooleanField('Active', default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Employee(models.Model):
    GENDERS = [('male', 'Male'), ('female', 'Female'), ('other', 'Other'), ('undisclosed', 'Prefer not to say')]
    BLOOD = [(b, b) for b in ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')]
    MARITAL = [('single', 'Single'), ('married', 'Married'), ('divorced', 'Divorced'), ('widowed', 'Widowed')]
    TYPES = [('full_time', 'Full Time'), ('part_time', 'Part Time'), ('contract', 'Contract'), ('intern', 'Intern'),
             ('consultant', 'Consultant')]
    ACTIVE, PROBATION, CONFIRMED, RESIGNED, TERMINATED, LEFT = (
        'active', 'probation', 'confirmed', 'resigned', 'terminated', 'left')
    STATUSES = [(ACTIVE, 'Active'), (PROBATION, 'Probation'), (CONFIRMED, 'Confirmed'), (RESIGNED, 'Resigned'),
                (TERMINATED, 'Terminated'), (LEFT, 'Left')]
    EMPLOYED = (ACTIVE, PROBATION, CONFIRMED)
    GONE = (TERMINATED, LEFT)
    TRACKED = ('department_id', 'designation', 'status', 'employment_type', 'date_of_joining', 'date_of_leaving',
               'reporting_manager_id', 'work_location')

    employee_id = models.CharField('Employee ID', max_length=20, unique=True, db_index=True)
    # A. personal
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    alias = models.CharField('Alias', max_length=100, blank=True, help_text='Defaults to first name')
    photo = models.FileField('Profile photograph', storage=private_storage, upload_to=photo_upload_to, blank=True)
    gender = models.CharField(max_length=12, choices=GENDERS, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    blood_group = models.CharField(max_length=3, choices=BLOOD, blank=True)
    marital_status = models.CharField(max_length=10, choices=MARITAL, blank=True)
    mobile = models.CharField('Personal mobile number', max_length=20, blank=True, validators=[validate_phone])
    alternate_mobile = models.CharField('Alternate mobile number', max_length=20, blank=True,
                                        validators=[validate_phone])
    personal_email = models.EmailField(blank=True)
    current_address = models.TextField(blank=True)
    permanent_address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pin_code = models.CharField('PIN code', max_length=10, blank=True)
    country = models.CharField(max_length=80, default='India')
    # B. employment
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name='employees')
    designation = models.CharField(max_length=120, db_index=True)
    is_recruiter = models.BooleanField(
        'Works as a recruiter', default=False,
        help_text='Makes this existing employee available for recruiter performance tracking.')
    reporting_manager = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name='reportees')
    employment_type = models.CharField(max_length=12, choices=TYPES, default='full_time')
    date_of_joining = models.DateField(db_index=True)
    probation_months = models.PositiveSmallIntegerField('Probation period (months)', null=True, blank=True)
    probation_end_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True)
    date_of_leaving = models.DateField(null=True, blank=True, db_index=True,
                                       help_text='Leave blank while the employee is employed.')
    status = models.CharField('Employment status', max_length=12, choices=STATUSES, default=ACTIVE, db_index=True)
    work_location = models.CharField(max_length=120, blank=True)
    notice_period_days = models.PositiveSmallIntegerField('Notice period (days)', null=True, blank=True)
    work_email = models.EmailField('Employee work email', blank=True)
    official_mobile = models.CharField('Official mobile number', max_length=20, blank=True,
                                       validators=[validate_phone])
    # C. identity / statutory (masked in ordinary screens)
    aadhaar_number = models.CharField(max_length=14, blank=True, validators=[validate_aadhaar])
    pan_number = models.CharField('PAN number', max_length=10, blank=True, validators=[validate_pan])
    passport_number = models.CharField(max_length=20, blank=True)
    driving_licence_number = models.CharField(max_length=25, blank=True)
    uan_number = models.CharField('UAN number', max_length=14, blank=True, validators=[validate_uan])
    esic_number = models.CharField('ESIC number', max_length=20, blank=True, validators=[validate_esic])
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_id']
        indexes = [models.Index(fields=['department', 'status'], name='emp_dept_status_idx')]

    def __str__(self):
        return f'{self.employee_id} - {self.full_name}'

    @property
    def full_name(self):
        return ' '.join(p for p in (self.first_name, self.middle_name, self.last_name) if p)

    @property
    def is_employed(self):
        return self.status in self.EMPLOYED

    @property
    def aadhaar_masked(self):
        return mask(self.aadhaar_number)

    @property
    def pan_masked(self):
        return mask(self.pan_number)

    @property
    def current_bank(self):
        return self.bank_accounts.filter(is_current=True).order_by('-id').first()

    def clean(self):
        errors = {}
        if self.status in self.EMPLOYED and self.date_of_leaving:
            errors['date_of_leaving'] = 'Date of leaving must be blank while the employee is Active/Probation/Confirmed.'
        if self.status in self.GONE and not self.date_of_leaving:
            errors['date_of_leaving'] = 'Enter the date of leaving for a Terminated/Left employee.'
        if self.date_of_leaving and self.date_of_joining and self.date_of_leaving < self.date_of_joining:
            errors['date_of_leaving'] = 'Date of leaving cannot be before the date of joining.'
        if self.country.strip().lower() == 'india' and self.pin_code:
            try:
                validate_pin(self.pin_code)
            except ValidationError as e:
                errors['pin_code'] = e.messages
        if self.reporting_manager_id and self.pk and self.reporting_manager_id == self.pk:
            errors['reporting_manager'] = 'An employee cannot report to themselves.'
        if errors:
            raise ValidationError(errors)

    def default_alias(self):
        return ((self.first_name or '').split() or [''])[0][:100]

    def save(self, *args, **kwargs):
        self.alias = (self.alias or '').strip()
        if not self.alias:
            self.alias = self.default_alias()
        self.pan_number = (self.pan_number or '').upper()
        self.aadhaar_number = (self.aadhaar_number or '').replace(' ', '').replace('-', '')
        if self.probation_months and self.date_of_joining and not self.probation_end_date:
            m = self.date_of_joining.month - 1 + self.probation_months
            y, m = self.date_of_joining.year + m // 12, m % 12 + 1
            self.probation_end_date = date(y, m, min(self.date_of_joining.day, calendar.monthrange(y, m)[1]))
        old = None
        with transaction.atomic():
            if not self.employee_id:
                self.employee_id = allocate_employee_id()
            if self.pk:
                old = Employee.objects.filter(pk=self.pk).values(*self.TRACKED).first()
            super().save(*args, **kwargs)
            if old:
                for f in self.TRACKED:
                    if old[f] != getattr(self, f):
                        EmployeeChangeLog.objects.create(employee=self, field=f.replace('_id', ''),
                                                         old_value=_display(f, old[f]),
                                                         new_value=_display(f, getattr(self, f)))


    def delete(self, *args, **kwargs):
        """Former employees are retained. Hard delete is refused once any payroll exists."""
        if self.pk and self.payrolls.exists():
            raise ProtectedError('Employee has payroll history and cannot be deleted; mark them Left instead.',
                                 set(self.payrolls.all()))
        return super().delete(*args, **kwargs)


def _display(field, value):
    if value is None:
        return ''
    if field == 'department_id':
        return Department.objects.filter(pk=value).values_list('name', flat=True).first() or str(value)
    if field == 'reporting_manager_id':
        e = Employee.objects.filter(pk=value).first()
        return e.full_name if e else str(value)
    if field == 'status':
        return dict(Employee.STATUSES).get(value, value)
    if field == 'employment_type':
        return dict(Employee.TYPES).get(value, value)
    return str(value)


def allocate_employee_id():
    """Next free ID from the configured prefix/sequence (skips IDs already taken manually)."""
    with transaction.atomic():
        s = PayrollSettings.objects.select_for_update().get_or_create(pk=1)[0]
        while True:
            candidate = s.format_employee_id(s.next_employee_sequence)
            s.next_employee_sequence += 1
            if not Employee.objects.filter(employee_id__iexact=candidate).exists():
                break
        s.save(update_fields=['next_employee_sequence'])
        return candidate


class EmployeeChangeLog(models.Model):
    """Audit trail of employment changes (department, designation, status, dates ...)."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='change_log')
    field = models.CharField(max_length=40)
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at', '-id']


class EmergencyContact(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='emergency_contacts')
    name = models.CharField('Contact person name', max_length=120)
    relationship = models.CharField(max_length=60)
    mobile = models.CharField('Mobile number', max_length=20, validators=[validate_phone])
    alternate_number = models.CharField(max_length=20, blank=True, validators=[validate_phone])
    address = models.TextField(blank=True)

    def __str__(self):
        return f'{self.name} ({self.relationship})'


# --------------------------------------------------------------------------- documents
DOCUMENT_TYPES = [
    ('Identity Documents', [('aadhaar', 'Aadhaar Card'), ('pan', 'PAN Card'), ('passport', 'Passport'),
                            ('driving_licence', 'Driving Licence'), ('voter_id', 'Voter ID')]),
    ('Banking Documents', [('bank_passbook', 'Bank Passbook'), ('cancelled_cheque', 'Cancelled Cheque'),
                           ('bank_statement', 'Bank Statement')]),
    ('Employment Documents', [('resume', 'Resume'), ('offer_letter', 'Offer Letter'),
                              ('appointment_letter', 'Appointment Letter'),
                              ('employment_agreement', 'Employment Agreement'), ('joining_form', 'Joining Form'),
                              ('confirmation_letter', 'Confirmation Letter'),
                              ('experience_letter', 'Experience Letter'),
                              ('experience_certificate', 'Experience Certificate'), ('relieving_letter', 'Relieving Letter')]),
    ('Educational Documents', [('cert_10', '10th Certificate'), ('cert_12', '12th Certificate'),
                               ('graduation', 'Graduation Certificate'),
                               ('post_graduation', 'Post Graduation Certificate'),
                               ('other_certificate', 'Other Certificates'),
                               ('skills_education_zip', 'Skills & Education (ZIP)')]),
    ('Other Documents', [('photograph', 'Photograph'), ('medical_certificate', 'Medical Certificate'),
                         ('address_proof', 'Address Proof'), ('other', 'Other Supporting Documents')]),
]
DOCUMENT_TYPE_LABELS = {k: v for _, group in DOCUMENT_TYPES for k, v in group}
DOCUMENT_CATEGORY = {k: cat for cat, group in DOCUMENT_TYPES for k, _ in group}
# Optional uploads offered directly on the employee add/edit form: (form field, document type, kind).
FORM_DOCUMENTS = [('doc_aadhaar', 'aadhaar', 'file'), ('doc_pan', 'pan', 'file'),
                  ('doc_experience', 'experience_certificate', 'file'), ('doc_skills_zip', 'skills_education_zip', 'zip')]
KEY_DOCUMENTS = ['aadhaar', 'pan', 'driving_licence', 'bank_passbook', 'photograph', 'resume', 'offer_letter']


class EmployeeDocument(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='documents')
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPES, db_index=True)
    document_name = models.CharField(max_length=160)
    document_number = models.CharField('Document number (optional)', max_length=60, blank=True)
    upload_date = models.DateTimeField(default=timezone.now)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    file = models.FileField(storage=private_storage, upload_to=document_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=60, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)
    is_current = models.BooleanField(default=True)
    replaces = models.OneToOneField('self', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='replaced_by')

    class Meta:
        ordering = ['document_type', '-upload_date']

    def __str__(self):
        return f'{self.get_document_type_display()} - {self.document_name}'

    @property
    def category(self):
        return DOCUMENT_CATEGORY.get(self.document_type, '')

    @property
    def number_masked(self):
        return mask(self.document_number)

    def expiry_state(self, alert_days=30):
        if not self.expiry_date:
            return ''
        today = timezone.localdate()
        if self.expiry_date < today:
            return 'expired'
        if self.expiry_date <= today + timedelta(days=alert_days):
            return 'expiring'
        return ''

    def history(self):
        chain, cur = [], self.replaces
        while cur is not None:
            chain.append(cur)
            cur = cur.replaces
        return chain


# --------------------------------------------------------------------------- bank
class EmployeeBankAccount(models.Model):
    """Append-only: updating bank details adds a new row and retires the old one."""
    TYPES = [('savings', 'Savings'), ('current', 'Current'), ('other', 'Other')]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='bank_accounts')
    bank_name = models.CharField(max_length=120)
    account_holder_name = models.CharField(max_length=160)
    account_number = models.CharField(max_length=34)
    ifsc_code = models.CharField('IFSC code', max_length=11, validators=[validate_ifsc])
    branch_name = models.CharField(max_length=120, blank=True)
    branch_address = models.TextField(blank=True)
    account_type = models.CharField(max_length=10, choices=TYPES, default='savings')
    bank_country = models.CharField(max_length=60, default='India')
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-is_current', '-created_at', '-id']

    def __str__(self):
        return f'{self.bank_name} {self.account_masked}'

    @property
    def account_masked(self):
        return mask(self.account_number)

    def save(self, *args, **kwargs):
        self.ifsc_code = (self.ifsc_code or '').upper()
        self.account_number = (self.account_number or '').replace(' ', '')
        if self.pk and EmployeeBankAccount.objects.filter(pk=self.pk).exists() and not kwargs.get('update_fields'):
            raise ValidationError('Bank accounts are history records; add a new account instead of editing.')
        super().save(*args, **kwargs)

    @classmethod
    def add_for(cls, employee, **fields):
        with transaction.atomic():
            employee.bank_accounts.filter(is_current=True).update(is_current=False, retired_at=timezone.now())
            return cls.objects.create(employee=employee, is_current=True, **fields)


# --------------------------------------------------------------------------- salary
EARNING_CODES = [('basic', 'Basic Salary'), ('hra', 'House Rent Allowance (HRA)'),
                 ('conveyance', 'Conveyance Allowance'), ('medical', 'Medical Allowance'),
                 ('special', 'Special Allowance'), ('other_allowance', 'Other Allowances'), ('bonus', 'Bonus'),
                 ('incentive', 'Incentives'), ('overtime', 'Overtime'), ('other_earning', 'Other Earnings')]
DEDUCTION_CODES = [('pf', 'Provident Fund (PF)'), ('esi', 'Employee State Insurance (ESI)'),
                   ('professional_tax', 'Professional Tax'), ('tds', 'TDS'), ('loan', 'Loan Deduction'),
                   ('advance', 'Advance Salary Recovery'), ('other_deduction', 'Other Deductions')]
EARNING_LABELS = dict(EARNING_CODES)
DEDUCTION_LABELS = dict(DEDUCTION_CODES)
# Earnings in a monthly structure that are NOT prorated by payable days
UNPRORATED_EARNINGS = ('bonus', 'incentive', 'overtime')


class EmployeeSalaryStructure(models.Model):
    MONTHLY, DAILY, HOURLY, CONTRACT = 'monthly', 'daily', 'hourly', 'contract'
    TYPES = [(MONTHLY, 'Monthly Fixed Salary'), (DAILY, 'Daily Wage'), (HOURLY, 'Hourly Wage'),
             (CONTRACT, 'Contract-Based Payment')]
    DIVISORS = [('default', 'Use Payroll Settings default')] + PayrollSettings.DIVISORS

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='salary_structures')
    structure_type = models.CharField(max_length=10, choices=TYPES, default=MONTHLY)
    rate = models.DecimalField('Daily wage / hourly rate / contract amount', max_digits=14, decimal_places=2,
                               default=ZERO, help_text='Not used for Monthly Fixed (enter components instead).')
    divisor = models.CharField('Salary divisor', max_length=10, choices=DIVISORS, default='default')
    pay_full_month = models.BooleanField(
        'Pay full monthly salary regardless of calendar days', default=False,
        help_text='Monthly only. LWP / unpaid leave are still deducted at monthly / divisor per day.')
    use_statutory_rules = models.BooleanField(
        'Calculate PF/ESI/PT/TDS from Statutory Rules', default=False,
        help_text='Off = use the fixed deduction amounts entered below.')
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee', '-effective_from', '-id']
        indexes = [models.Index(fields=['employee', 'effective_from'], name='salary_emp_eff_idx')]

    def __str__(self):
        return f'{self.employee.employee_id} {self.get_structure_type_display()} from {self.effective_from}'

    def amount(self, kind, code):
        c = next((c for c in self.components.all() if c.kind == kind and c.code == code), None)
        return c.amount if c else ZERO

    @property
    def earnings(self):
        return [c for c in self.components.all() if c.kind == SalaryComponent.EARNING]

    @property
    def deductions(self):
        return [c for c in self.components.all() if c.kind == SalaryComponent.DEDUCTION]

    @property
    def basic_salary(self):
        return self.amount(SalaryComponent.EARNING, 'basic')

    @property
    def allowances(self):
        return sum((c.amount for c in self.earnings if c.code != 'basic'), ZERO)

    @property
    def total_earnings(self):
        if self.structure_type == self.MONTHLY:
            return sum((c.amount for c in self.earnings), ZERO)
        return sum((c.amount for c in self.earnings), ZERO) + (self.rate if self.structure_type == self.CONTRACT else ZERO)

    gross_monthly = total_earnings

    @property
    def total_deductions(self):
        return sum((c.amount for c in self.deductions), ZERO)

    @property
    def net_salary(self):
        return self.total_earnings - self.total_deductions

    @property
    def used_in_finalized_payroll(self):
        return self.payrolls.filter(status=EmployeePayroll.FINALIZED).exists()

    @classmethod
    def applicable(cls, employee, period_start, period_end):
        return (cls.objects.filter(employee=employee, effective_from__lte=period_end)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period_start))
                .order_by('-effective_from', '-id').first())

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({'effective_to': 'Effective To cannot be before Effective From.'})

    # Once finalized payroll used a structure it is history: only closing it (effective_to, not before the
    # last finalized month) or editing notes is allowed. Salary changes must be a new effective-dated version.
    VERSION_MUTABLE = ('effective_to', 'notes')

    def last_finalized_end(self):
        p = (self.payrolls.filter(status=EmployeePayroll.FINALIZED).select_related('period')
             .order_by('-period__year', '-period__month').first())
        return p.period.end if p else None

    def save(self, *args, **kwargs):
        if self.pk:
            old = EmployeeSalaryStructure.objects.filter(pk=self.pk).first()
            if old is not None and old.used_in_finalized_payroll:
                changed = [f.attname for f in self._meta.concrete_fields
                           if getattr(old, f.attname) != getattr(self, f.attname)]
                if [f for f in changed if f not in self.VERSION_MUTABLE]:
                    raise FinalizedRecordError('This salary structure is used by finalized payroll. '
                                               'Add a new effective-dated structure instead of editing it.')
                end = old.last_finalized_end()
                if 'effective_to' in changed and self.effective_to is not None and self.effective_to < end:
                    raise FinalizedRecordError('Effective To cannot be moved before finalized payroll '
                                               f'that used this structure (last month ends {end}).')
        super().save(*args, **kwargs)


class SalaryComponent(models.Model):
    EARNING, DEDUCTION = 'earning', 'deduction'
    structure = models.ForeignKey(EmployeeSalaryStructure, on_delete=models.CASCADE, related_name='components')
    kind = models.CharField(max_length=10, choices=[(EARNING, 'Earning'), (DEDUCTION, 'Deduction')])
    code = models.CharField(max_length=20, choices=EARNING_CODES + DEDUCTION_CODES)
    amount = models.DecimalField('Monthly amount', **MONEY)

    class Meta:
        unique_together = [('structure', 'kind', 'code')]

    @property
    def label(self):
        return (EARNING_LABELS if self.kind == self.EARNING else DEDUCTION_LABELS).get(self.code, self.code)

    def _guard(self, structure_id):
        if structure_id and EmployeePayroll.objects.filter(structure_id=structure_id,
                                                           status=EmployeePayroll.FINALIZED).exists():
            raise FinalizedRecordError('Components of a salary structure used by finalized payroll cannot change. '
                                       'Add a new effective-dated structure instead.')

    def save(self, *args, **kwargs):
        self._guard(self.structure_id)
        if self.pk:
            old = SalaryComponent.objects.filter(pk=self.pk).values_list('structure_id', flat=True).first()
            if old != self.structure_id:
                self._guard(old)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard(self.structure_id)
        return super().delete(*args, **kwargs)


# --------------------------------------------------------------------------- payroll
class PayrollPeriod(models.Model):
    DRAFT, FINALIZED = 'draft', 'finalized'
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=[(DRAFT, 'Draft'), (FINALIZED, 'Finalized')], default=DRAFT)
    divisor = models.CharField('Salary divisor for this month', max_length=10, choices=PayrollSettings.DIVISORS,
                               default=PayrollSettings.DIV_ACTUAL)
    working_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    payment_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-year', '-month']
        constraints = [models.UniqueConstraint(fields=['year', 'month'], name='unique_payroll_month')]
        indexes = [models.Index(fields=['year', 'month'], name='payroll_month_idx')]

    def __str__(self):
        return self.label

    @property
    def start(self):
        return date(self.year, self.month, 1)

    @property
    def end(self):
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def calendar_days(self):
        return calendar.monthrange(self.year, self.month)[1]

    @property
    def label(self):
        return f'{calendar.month_name[self.month]} {self.year}'

    @property
    def divisor_days(self):
        return Decimal(30) if self.divisor == PayrollSettings.DIV_FIXED30 else Decimal(self.calendar_days)


class FinalizedRecordError(Exception):
    pass


class EmployeePayroll(models.Model):
    DRAFT, FINALIZED = 'draft', 'finalized'
    INPUT_FIELDS = ('working_days', 'payable_days', 'lwp_days', 'paid_leave_days', 'unpaid_leave_days',
                    'approved_hours', 'overtime_amount', 'incentive_amount', 'additional_earnings',
                    'additional_deductions', 'remarks', 'advance_recovery_override')
    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name='payrolls')
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='payrolls')
    structure = models.ForeignKey(EmployeeSalaryStructure, null=True, on_delete=models.PROTECT,
                                  related_name='payrolls')
    bank_account = models.ForeignKey(EmployeeBankAccount, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='payrolls')
    status = models.CharField(max_length=10, choices=[(DRAFT, 'Draft'), (FINALIZED, 'Finalized')],
                              default=DRAFT, db_index=True)
    revision = models.PositiveSmallIntegerField(default=1)
    revises = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='revisions')
    revision_reason = models.CharField(max_length=240, blank=True)
    is_current = models.BooleanField(default=True)
    # inputs
    calendar_days = models.PositiveSmallIntegerField(default=30)
    divisor_days = models.DecimalField(**DAYS)
    working_days = models.DecimalField(**DAYS)
    payable_days = models.DecimalField(**DAYS)
    lwp_days = models.DecimalField('LWP days', **DAYS)
    paid_leave_days = models.DecimalField(**DAYS)
    unpaid_leave_days = models.DecimalField(**DAYS)
    approved_hours = models.DecimalField(max_digits=7, decimal_places=2, default=ZERO)
    overtime_amount = models.DecimalField(**MONEY)
    incentive_amount = models.DecimalField(**MONEY)
    additional_earnings = models.DecimalField(**MONEY)
    additional_deductions = models.DecimalField(**MONEY)
    remarks = models.CharField(max_length=240, blank=True)
    advance_recovery_override = models.DecimalField(
        'Advance / loan recovery override', max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='Blank = automatic (instalment, capped at outstanding and net pay).')
    SOURCE_MANUAL, SOURCE_ATTENDANCE, SOURCE_OVERRIDE = 'manual', 'attendance', 'override'
    INPUT_SOURCES = [(SOURCE_MANUAL, 'Manual entry'), (SOURCE_ATTENDANCE, 'From attendance'),
                     (SOURCE_OVERRIDE, 'Attendance, manually overridden')]
    input_source = models.CharField(max_length=12, choices=INPUT_SOURCES, default=SOURCE_MANUAL)
    attendance_snapshot = models.TextField(blank=True, help_text='Attendance summary (JSON) used to prefill inputs.')
    # results
    monthly_salary = models.DecimalField(**MONEY)
    gross_earnings = models.DecimalField(**MONEY)
    total_deductions = models.DecimalField(**MONEY)
    employer_contributions = models.DecimalField(**MONEY)
    rounding_adjustment = models.DecimalField(**MONEY)
    net_salary = models.DecimalField(**MONEY)
    # snapshot (frozen at finalize so payslips never change)
    snap_employee_code = models.CharField(max_length=20, blank=True)
    snap_name = models.CharField(max_length=250, blank=True)
    snap_department = models.CharField(max_length=120, blank=True)
    snap_designation = models.CharField(max_length=120, blank=True)
    snap_date_of_joining = models.DateField(null=True, blank=True)
    snap_pan_masked = models.CharField(max_length=12, blank=True)
    snap_bank_name = models.CharField(max_length=120, blank=True)
    snap_account_number = models.CharField(max_length=34, blank=True)
    snap_ifsc = models.CharField(max_length=11, blank=True)
    snap_structure_type = models.CharField(max_length=10, blank=True)
    snap_rate = models.DecimalField(**MONEY)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['period', 'snap_employee_code', 'employee__employee_id', 'revision']
        constraints = [
            models.UniqueConstraint(fields=['period', 'employee'], condition=Q(is_current=True),
                                    name='one_current_payroll_per_employee_month'),
            models.UniqueConstraint(fields=['period', 'employee', 'revision'], name='unique_payroll_revision'),
        ]
        indexes = [models.Index(fields=['period', 'status'], name='payroll_period_status_idx')]

    def __str__(self):
        return f'{self.code} {self.period.label} r{self.revision}'

    @property
    def code(self):
        return self.snap_employee_code or self.employee.employee_id

    @property
    def account_masked(self):
        return mask(self.snap_account_number)

    @property
    def is_finalized(self):
        return self.status == self.FINALIZED

    def save(self, *args, **kwargs):
        if self.pk:
            prev = EmployeePayroll.objects.filter(pk=self.pk).values_list('status', flat=True).first()
            if prev == self.FINALIZED:
                allowed = {'is_current'}
                if not kwargs.get('update_fields') or not set(kwargs['update_fields']) <= allowed:
                    raise FinalizedRecordError('Finalized payroll is read-only. Create a revision instead.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.FINALIZED:
            raise FinalizedRecordError('Finalized payroll cannot be deleted.')
        return super().delete(*args, **kwargs)


class PayrollLine(models.Model):
    """Base for frozen line items (earnings / deductions)."""
    code = models.CharField(max_length=24)
    label = models.CharField(max_length=80)
    amount = models.DecimalField(**MONEY)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ['order', 'id']


class PayrollEarning(PayrollLine):
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name='earnings')


class PayrollDeduction(PayrollLine):
    EMPLOYEE, EMPLOYER = 'employee', 'employer'
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name='deductions')
    side = models.CharField(max_length=10, choices=[(EMPLOYEE, 'Employee deduction'),
                                                    (EMPLOYER, 'Employer contribution')], default=EMPLOYEE)


class PayrollAdjustment(models.Model):
    """Itemised manual adjustment entered before finalizing (extra earning or deduction)."""
    EARNING, DEDUCTION = 'earning', 'deduction'
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name='adjustments')
    kind = models.CharField(max_length=10, choices=[(EARNING, 'Additional earning'), (DEDUCTION, 'Additional deduction')])
    label = models.CharField(max_length=80)
    amount = models.DecimalField(**MONEY)
    note = models.CharField(max_length=240, blank=True)


class SalaryPayment(models.Model):
    """A (possibly partial) salary payment against a finalized payslip. Posts Dr salary payable / Cr bank.
    Paid / balance are computed across revisions of the same employee + month (payments stay attached to the
    record they were made against). Never deleted: a wrong payment is cancelled, which reverses its entry."""
    MODES = [('bank', 'Bank transfer'), ('neft', 'NEFT/RTGS/IMPS'), ('upi', 'UPI'), ('cheque', 'Cheque'),
             ('cash', 'Cash'), ('other', 'Other')]
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.PROTECT, related_name='salary_payments')
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(**MONEY)
    bank_account = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='salary_payments', help_text='Blank = the default bank account.')
    mode = models.CharField(max_length=10, choices=MODES, default='bank')
    reference = models.CharField('Reference / UTR', max_length=80, blank=True)
    remarks = models.CharField(max_length=240, blank=True)
    cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-id']
        indexes = [models.Index(fields=['payment_date'])]

    def __str__(self):
        return f'{self.payroll.code} {self.payment_date} {self.amount}'


# ------------------------------------------------------------------ advances / loans (Stage 6)
class EmployeeAdvance(models.Model):
    """Salary advance or loan. Outstanding = principal + interest - recoveries (current finalized payslips)
    - repayments - write-off. Disbursement posts Dr employee advances (party employee) / Cr bank."""
    ADVANCE, LOAN = 'advance', 'loan'
    KINDS = [(ADVANCE, 'Salary advance'), (LOAN, 'Loan')]
    ACTIVE, CLOSED, WRITTEN_OFF, CANCELLED = 'active', 'closed', 'written_off', 'cancelled'
    STATUSES = [(ACTIVE, 'Active'), (CLOSED, 'Closed'), (WRITTEN_OFF, 'Written off'), (CANCELLED, 'Cancelled')]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='advances')
    kind = models.CharField('Type', max_length=10, choices=KINDS, default=ADVANCE)
    date = models.DateField('Disbursement date', default=timezone.localdate)
    principal = models.DecimalField(**MONEY)
    interest_amount = models.DecimalField('Interest (flat, simple)', **MONEY)
    instalment_amount = models.DecimalField('Instalment amount', max_digits=14, decimal_places=2, null=True,
                                            blank=True)
    instalment_count = models.PositiveSmallIntegerField('Number of instalments', null=True, blank=True)
    start_year = models.PositiveSmallIntegerField('Recovery from (year)')
    start_month = models.PositiveSmallIntegerField('Recovery from (month)')
    bank_account = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='employee_advances', help_text='Blank = the default bank account.')
    reference = models.CharField(max_length=80, blank=True)
    reason = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=ACTIVE, db_index=True)
    closed_on = models.DateField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=240, blank=True)
    write_off_reason = models.CharField(max_length=240, blank=True)
    written_off_amount = models.DecimalField(**MONEY)
    written_off_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']

    def __str__(self):
        return f'{self.get_kind_display()} #{self.pk} {self.employee.employee_id}'

    def clean(self):
        err = {}
        if self.principal is None or self.principal <= 0:
            err['principal'] = 'Principal must be more than zero.'
        if self.interest_amount is not None and self.interest_amount < 0:
            err['interest_amount'] = 'Interest cannot be negative.'
        if not self.instalment_amount and not self.instalment_count:
            err['instalment_amount'] = 'Give the instalment amount or the number of instalments.'
        if self.instalment_amount is not None and self.instalment_amount <= 0:
            err['instalment_amount'] = 'Instalment must be more than zero.'
        if self.start_month and not 1 <= self.start_month <= 12:
            err['start_month'] = 'Month must be 1-12.'
        if err:
            raise ValidationError(err)

    @property
    def total(self):
        return (self.principal or ZERO) + (self.interest_amount or ZERO)

    @property
    def instalment(self):
        if self.instalment_amount:
            return self.instalment_amount
        return (self.total / self.instalment_count).quantize(Decimal('0.01')) if self.instalment_count else self.total

    @property
    def start_key(self):
        return self.start_year * 12 + self.start_month

    def recoveries_qs(self):
        """Recoveries that count: on current finalized payslips only."""
        return self.recoveries.filter(payroll__status=EmployeePayroll.FINALIZED, payroll__is_current=True)

    def recovered(self, exclude_period=None):
        qs = self.recoveries_qs()
        if exclude_period is not None:
            qs = qs.exclude(payroll__period=exclude_period)
        return qs.aggregate(s=models.Sum('amount'))['s'] or ZERO

    def repaid(self):
        return self.repayments.filter(cancelled=False).aggregate(s=models.Sum('amount'))['s'] or ZERO

    def outstanding(self, exclude_period=None):
        if self.status == self.CANCELLED:
            return ZERO
        return self.total - self.recovered(exclude_period) - self.repaid() - (self.written_off_amount or ZERO)


class AdvanceRecovery(models.Model):
    """Recovery of an advance through one payslip (rebuilt with the draft; frozen once finalized)."""
    advance = models.ForeignKey(EmployeeAdvance, on_delete=models.PROTECT, related_name='recoveries')
    payroll = models.ForeignKey(EmployeePayroll, on_delete=models.CASCADE, related_name='advance_recoveries')
    amount = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['payroll__period__year', 'payroll__period__month', 'id']


class AdvanceRepayment(models.Model):
    """Repayment outside payroll: Dr bank / Cr employee advances. Never deleted; cancelling reverses."""
    advance = models.ForeignKey(EmployeeAdvance, on_delete=models.PROTECT, related_name='repayments')
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(**MONEY)
    bank_account = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='advance_repayments', help_text='Blank = the default bank account.')
    reference = models.CharField(max_length=80, blank=True)
    cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
