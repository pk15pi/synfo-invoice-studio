from decimal import Decimal

from django import forms
from django.conf import settings as dj_settings
from django.core.exceptions import ValidationError



class StyledForm:
    """Adds the shared 'field' CSS class (same look as invoice_app forms); works for Form and ModelForm."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                f.widget.attrs.setdefault('class', 'field')

from .models import EmployeeAdvance, SalaryPayment
from .models import (DEDUCTION_CODES, EARNING_CODES, Department, EmergencyContact, Employee, EmployeeBankAccount,
                     EmployeeDocument, EmployeeSalaryStructure, PayrollPeriod, PayrollSettings, StatutoryRule)
from .validators import CONTENT_TYPES, sniff_upload

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


def max_upload_bytes():
    return PayrollSettings.current().document_max_mb * 1024 * 1024


class DepartmentForm(StyledForm, forms.ModelForm):
    class Meta:
        model = Department
        fields = ['name', 'code', 'description', 'head', 'active']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields['head'].queryset = Employee.objects.filter(status__in=Employee.EMPLOYED)

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


SENSITIVE = ('aadhaar_number', 'pan_number', 'passport_number', 'driving_licence_number', 'uan_number', 'esic_number')


class EmployeeForm(StyledForm, forms.ModelForm):
    employee_id = forms.CharField(
        label='Employee ID', max_length=20, required=False,
        help_text='Leave blank to auto-generate from Payroll Settings (e.g. EMP001).')
    photo_upload = forms.FileField(label='Profile photograph (JPG/PNG)', required=False)

    class Meta:
        model = Employee
        exclude = ['photo', 'created_at', 'updated_at']
        widgets = {f: DATE for f in ('date_of_birth', 'date_of_joining', 'probation_end_date',
                                     'confirmation_date', 'date_of_leaving')}
        widgets.update({f: forms.Textarea(attrs={'rows': 2}) for f in
                        ('current_address', 'permanent_address', 'notes')})

    SECTIONS = [
        ('Personal details', ['employee_id', 'first_name', 'middle_name', 'last_name', 'alias', 'photo_upload', 'gender',
                              'date_of_birth', 'blood_group', 'marital_status', 'mobile', 'alternate_mobile',
                              'personal_email', 'current_address', 'permanent_address', 'city', 'state', 'pin_code',
                              'country']),
        ('Employment details', ['department', 'designation', 'reporting_manager', 'employment_type',
                                'date_of_joining', 'probation_months', 'probation_end_date', 'confirmation_date',
                                'status', 'date_of_leaving', 'work_location', 'notice_period_days', 'work_email',
                                'official_mobile']),
        ('Identity & statutory (confidential)', list(SENSITIVE)),
        ('Notes', ['notes']),
        ('Documents (optional)', ['doc_aadhaar', 'doc_pan', 'doc_experience', 'doc_skills_zip']),
    ]
    doc_aadhaar = forms.FileField(label='Aadhaar document', required=False, help_text='PDF, JPG or PNG')
    doc_pan = forms.FileField(label='PAN document', required=False, help_text='PDF, JPG or PNG')
    doc_experience = forms.FileField(label='Experience certificate', required=False, help_text='PDF, JPG or PNG')
    doc_skills_zip = forms.FileField(label='Skills & education documents (ZIP)', required=False,
                                     help_text='One .zip with all skill / education documents')

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        inst = self.instance
        depts = Department.objects.filter(active=True)
        if inst.pk and inst.department_id:
            depts = Department.objects.filter(pk=inst.department_id) | depts
        self.fields['department'].queryset = depts.distinct()
        mgr = Employee.objects.filter(status__in=Employee.EMPLOYED)
        if inst.pk:
            mgr = mgr.exclude(pk=inst.pk)
        self.fields['reporting_manager'].queryset = mgr
        for f in SENSITIVE:
            self.fields[f].widget.attrs['autocomplete'] = 'off'
        if inst.pk:
            # Never echo full identity numbers back into the page; blank = keep existing.
            for f in SENSITIVE:
                cur = getattr(inst, f)
                self.initial[f] = ''
                if cur:
                    from .validators import mask
                    self.fields[f].help_text = f'On file: {mask(cur)}. Leave blank to keep.'
            self.fields['employee_id'].initial = inst.employee_id

    def sections(self):
        return [(title, [self[f] for f in names]) for title, names in self.SECTIONS]

    def _clean_doc(self, name, kind):
        from .validators import form_doc_limits, sniff_upload, sniff_zip
        f = self.cleaned_data.get(name)
        if not f:
            return None
        per_file, zip_mb = form_doc_limits()
        self._doc_ext = getattr(self, '_doc_ext', {})
        self._doc_ext[name] = sniff_zip(f, zip_mb * 1024 * 1024) if kind == 'zip' else sniff_upload(f, per_file * 1024 * 1024)
        return f

    def clean_doc_aadhaar(self):
        return self._clean_doc('doc_aadhaar', 'file')

    def clean_doc_pan(self):
        return self._clean_doc('doc_pan', 'file')

    def clean_doc_experience(self):
        return self._clean_doc('doc_experience', 'file')

    def clean_doc_skills_zip(self):
        return self._clean_doc('doc_skills_zip', 'zip')

    def save_documents(self, employee):
        """Store each uploaded optional document in the private repository; a new upload replaces the
        current one of that type (the old row stays, marked not current). Returns the new documents."""
        import os
        from django.db import transaction
        from .models import DOCUMENT_TYPE_LABELS, FORM_DOCUMENTS
        made = []
        with transaction.atomic():
            for name, dtype, _ in FORM_DOCUMENTS:
                f = self.cleaned_data.get(name)
                if not f:
                    continue
                ext = self._doc_ext[name]
                old = (EmployeeDocument.objects.select_for_update()
                       .filter(employee=employee, document_type=dtype, is_current=True).order_by('-upload_date').first())
                doc = EmployeeDocument(employee=employee, document_type=dtype, document_name=DOCUMENT_TYPE_LABELS[dtype],
                                       original_filename=os.path.basename(f.name)[:255], content_type=CONTENT_TYPES[ext],
                                       file_size=f.size, replaces=old)
                doc._validated_ext = ext
                doc.file.save('upload', f, save=False)
                doc.save()
                if old is not None:
                    EmployeeDocument.objects.filter(employee=employee, document_type=dtype, is_current=True) \
                        .exclude(pk=doc.pk).update(is_current=False)
                made.append(doc)
        return made

    def clean_employee_id(self):
        v = (self.cleaned_data.get('employee_id') or '').strip().upper()
        if not v:
            return self.instance.employee_id if self.instance.pk else ''
        qs = Employee.objects.filter(employee_id__iexact=v)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('This Employee ID is already used.')
        return v

    def _clean_sensitive(self, f):
        v = (self.cleaned_data.get(f) or '').strip()
        if not v and self.instance.pk:
            return getattr(self.instance, f)
        return v

    def clean_aadhaar_number(self):
        return self._clean_sensitive('aadhaar_number').replace(' ', '').replace('-', '')

    def clean_pan_number(self):
        return self._clean_sensitive('pan_number').upper()

    def clean_passport_number(self):
        return self._clean_sensitive('passport_number')

    def clean_driving_licence_number(self):
        return self._clean_sensitive('driving_licence_number')

    def clean_uan_number(self):
        return self._clean_sensitive('uan_number')

    def clean_esic_number(self):
        return self._clean_sensitive('esic_number')

    def clean_photo_upload(self):
        f = self.cleaned_data.get('photo_upload')
        if f:
            self._photo_ext = sniff_upload(f, max_upload_bytes(), allowed=('png', 'jpg'))
        return f

    def _post_clean(self):
        self.instance.employee_id = self.cleaned_data.get('employee_id') or self.instance.employee_id
        super()._post_clean()

    def save(self, commit=True):
        emp = super().save(commit=False)
        f = self.cleaned_data.get('photo_upload')
        if commit:
            emp.save()
            if f:
                old = emp.photo.name if emp.photo else None
                emp._validated_ext = self._photo_ext
                emp.photo.save('photo', f, save=False)
                emp.save()
                if old:
                    emp.photo.storage.delete(old)
        return emp


class EmergencyContactForm(StyledForm, forms.ModelForm):
    class Meta:
        model = EmergencyContact
        exclude = ['employee']
        widgets = {'address': forms.Textarea(attrs={'rows': 2})}


class DocumentForm(StyledForm, forms.ModelForm):
    upload = forms.FileField(label='File (PDF, JPG, JPEG or PNG)')

    class Meta:
        model = EmployeeDocument
        fields = ['document_type', 'document_name', 'document_number', 'issue_date', 'expiry_date', 'remarks']
        widgets = {'issue_date': DATE, 'expiry_date': DATE, 'remarks': forms.Textarea(attrs={'rows': 2})}

    def clean_upload(self):
        f = self.cleaned_data['upload']
        self.ext = sniff_upload(f, max_upload_bytes())
        return f

    def clean(self):
        cd = super().clean()
        if cd.get('issue_date') and cd.get('expiry_date') and cd['expiry_date'] < cd['issue_date']:
            self.add_error('expiry_date', 'Expiry date cannot be before issue date.')
        return cd

    def save_for(self, employee, replaces=None):
        import os
        doc = super().save(commit=False)
        doc.employee = employee
        doc._validated_ext = self.ext
        f = self.cleaned_data['upload']
        doc.original_filename = os.path.basename(f.name)[:255]
        doc.content_type = CONTENT_TYPES[self.ext]
        doc.file_size = f.size
        doc.replaces = replaces
        doc.file.save('upload', f, save=False)
        doc.save()
        if replaces is not None:
            replaces.is_current = False
            replaces.save(update_fields=['is_current'])
        return doc


class BankAccountForm(StyledForm, forms.ModelForm):
    confirm_account_number = forms.CharField(label='Confirm account number', max_length=34,
                                             widget=forms.TextInput(attrs={'autocomplete': 'off'}))

    class Meta:
        model = EmployeeBankAccount
        fields = ['bank_name', 'account_holder_name', 'account_number', 'confirm_account_number', 'ifsc_code',
                  'branch_name', 'branch_address', 'account_type', 'bank_country']
        widgets = {'branch_address': forms.Textarea(attrs={'rows': 2}),
                   'account_number': forms.TextInput(attrs={'autocomplete': 'off'})}

    def clean_ifsc_code(self):
        return self.cleaned_data['ifsc_code'].strip().upper()

    def clean(self):
        cd = super().clean()
        a = (cd.get('account_number') or '').replace(' ', '')
        b = (cd.get('confirm_account_number') or '').replace(' ', '')
        if a and a != b:
            self.add_error('confirm_account_number', 'Account numbers do not match.')
        if a and not a.isalnum():
            self.add_error('account_number', 'Account number may contain only letters and digits.')
        return cd


class SalaryStructureForm(StyledForm, forms.ModelForm):
    class Meta:
        model = EmployeeSalaryStructure
        fields = ['structure_type', 'effective_from', 'rate', 'divisor', 'pay_full_month', 'use_statutory_rules',
                  'notes']
        widgets = {'effective_from': DATE}

    def __init__(self, *a, employee=None, **kw):
        super().__init__(*a, **kw)
        self.employee = employee
        src = kw.get('instance')
        for code, label in EARNING_CODES:
            self.fields[f'e_{code}'] = forms.DecimalField(label=label, required=False, min_value=0,
                                                          max_digits=14, decimal_places=2,
                                                          widget=forms.NumberInput(attrs={'class': 'field num',
                                                                                          'step': '0.01'}))
        for code, label in DEDUCTION_CODES:
            self.fields[f'd_{code}'] = forms.DecimalField(label=label, required=False, min_value=0,
                                                          max_digits=14, decimal_places=2,
                                                          widget=forms.NumberInput(attrs={'class': 'field num',
                                                                                          'step': '0.01'}))
        if src is not None and src.pk:
            for comp in src.components.all():
                self.initial[f'{comp.kind[0]}_{comp.code}'] = comp.amount
            self.instance = EmployeeSalaryStructure(employee=employee)  # edits always create a new structure
            self.initial['effective_from'] = None

    def earning_fields(self):
        return [self[f'e_{c}'] for c, _ in EARNING_CODES]

    def deduction_fields(self):
        return [self[f'd_{c}'] for c, _ in DEDUCTION_CODES]

    def main_fields(self):
        return [self[f] for f in self.Meta.fields]

    def clean(self):
        cd = super().clean()
        t = cd.get('structure_type')
        if t == EmployeeSalaryStructure.MONTHLY and not cd.get('e_basic'):
            self.add_error('e_basic', 'Basic salary is required for a monthly structure.')
        if t in ('daily', 'hourly', 'contract') and not cd.get('rate'):
            self.add_error('rate', 'Enter the daily wage, hourly rate or contract amount.')
        if cd.get('pay_full_month') and t != EmployeeSalaryStructure.MONTHLY:
            self.add_error('pay_full_month', 'Only applies to Monthly Fixed Salary.')
        eff = cd.get('effective_from')
        if eff and self.employee is not None:
            from .models import EmployeePayroll
            later = self.employee.salary_structures.filter(effective_from__gte=eff)
            if later.exists():
                self.add_error('effective_from', 'A structure already starts on or after this date. '
                                                 'New structures must start after the latest one.')
            from django.db.models import Q
            if EmployeePayroll.objects.filter(employee=self.employee, status='finalized').filter(
                    Q(period__year__gt=eff.year) | Q(period__year=eff.year, period__month__gte=eff.month)).exists():
                self.add_error('effective_from', 'Payroll is already finalized for this month or later. '
                                                 'Revise that payroll instead or choose a later date.')
        return cd

    def save_structure(self):
        from datetime import timedelta
        from django.db import transaction
        from .models import SalaryComponent
        cd = self.cleaned_data
        with transaction.atomic():
            st = self.save(commit=False)
            st.employee = self.employee
            prev = (self.employee.salary_structures.filter(effective_to__isnull=True)
                    .order_by('-effective_from').first())
            if prev:
                prev.effective_to = st.effective_from - timedelta(days=1)
                prev.save(update_fields=['effective_to'])
            st.save()
            for code, _ in EARNING_CODES:
                if cd.get(f'e_{code}'):
                    SalaryComponent.objects.create(structure=st, kind='earning', code=code, amount=cd[f'e_{code}'])
            for code, _ in DEDUCTION_CODES:
                if cd.get(f'd_{code}'):
                    SalaryComponent.objects.create(structure=st, kind='deduction', code=code,
                                                   amount=cd[f'd_{code}'])
            return st


class PayrollSettingsForm(StyledForm, forms.ModelForm):
    signature_upload = forms.FileField(label='Upload payslip signature (PNG/JPG)', required=False)

    class Meta:
        model = PayrollSettings
        exclude = ['signature_image']
        widgets = {'default_payroll_month': DATE}

    def clean_signature_upload(self):
        f = self.cleaned_data.get('signature_upload')
        if f:
            self._ext = sniff_upload(f, max_upload_bytes(), allowed=('png', 'jpg'))
        return f

    def save(self, commit=True):
        obj = super().save(commit=commit)
        f = self.cleaned_data.get('signature_upload')
        if f and commit:
            obj._validated_ext = self._ext
            obj.signature_image.save('sig', f, save=True)
        return obj


class StatutoryRuleForm(StyledForm, forms.ModelForm):
    class Meta:
        model = StatutoryRule
        fields = '__all__'
        widgets = {'effective_from': DATE, 'effective_to': DATE}

    def clean(self):
        cd = super().clean()
        if cd.get('effective_to') and cd.get('effective_from') and cd['effective_to'] < cd['effective_from']:
            self.add_error('effective_to', 'Effective To cannot be before Effective From.')
        if cd.get('calculation') == 'percent' and cd.get('rate') is not None and cd['rate'] > 100:
            self.add_error('rate', 'Percentage cannot exceed 100.')
        return cd


class PeriodForm(StyledForm, forms.ModelForm):
    class Meta:
        model = PayrollPeriod
        fields = ['divisor', 'payment_date']
        widgets = {'payment_date': DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Stage 4: actual payments are SalaryPayment rows; the old planned date stays visible but read-only.
        self.fields['payment_date'].disabled = True
        self.fields['payment_date'].required = False
        self.fields['payment_date'].help_text = 'Read-only (legacy). Record actual salary payments per payslip.'


class SalaryPaymentForm(StyledForm, forms.Form):
    payment_date = forms.DateField(widget=DATE)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    bank_account = forms.ModelChoiceField(queryset=None, required=False, empty_label='Default bank account')
    mode = forms.ChoiceField(choices=SalaryPayment.MODES, initial='bank')
    reference = forms.CharField(max_length=80, required=False)
    remarks = forms.CharField(max_length=240, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from bank_management.models import BankAccount
        self.fields['bank_account'].queryset = BankAccount.objects.filter(active=True)


class BulkSalaryPaymentForm(SalaryPaymentForm):
    amount = None
    remarks = None


DEC = dict(required=False, min_value=0, max_digits=14, decimal_places=2)


class PayrollInputForm(StyledForm, forms.Form):
    working_days = forms.DecimalField(label='Working days (blank = days in service)', **DEC)
    payable_days = forms.DecimalField(label='Payable days (blank = working - LWP - unpaid)', **DEC)
    lwp_days = forms.DecimalField(label='LWP days', **DEC)
    paid_leave_days = forms.DecimalField(label='Paid leave days', **DEC)
    unpaid_leave_days = forms.DecimalField(label='Unpaid leave days', **DEC)
    approved_hours = forms.DecimalField(label='Approved hours (hourly staff)', **DEC)
    overtime_amount = forms.DecimalField(label='Overtime amount', **DEC)
    incentive_amount = forms.DecimalField(label='Incentives', **DEC)
    additional_earnings = forms.DecimalField(label='Additional earnings', **DEC)
    additional_deductions = forms.DecimalField(label='Additional deductions', **DEC)
    remarks = forms.CharField(max_length=240, required=False)
    advance_recovery_override = forms.DecimalField(
        label='Advance / loan recovery (blank = automatic instalment)', **DEC)


class RevisionForm(StyledForm, forms.Form):
    reason = forms.CharField(label='Reason for revision', max_length=240)


# ------------------------------------------------------------------ advances / loans (Stage 6)
class EmployeeAdvanceForm(StyledForm, forms.ModelForm):
    class Meta:
        model = EmployeeAdvance
        fields = ['employee', 'kind', 'date', 'principal', 'interest_amount', 'instalment_amount',
                  'instalment_count', 'start_year', 'start_month', 'bank_account', 'reference', 'reason']
        widgets = {'date': DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from bank_management.models import BankAccount
        self.fields['bank_account'].queryset = BankAccount.objects.filter(active=True)
        self.fields['bank_account'].empty_label = 'Default bank account'
        self.fields['employee'].queryset = Employee.objects.order_by('employee_id')


class AdvanceRepaymentForm(StyledForm, forms.Form):
    date = forms.DateField(widget=DATE)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'))
    bank_account = forms.ModelChoiceField(queryset=None, required=False, empty_label='Default bank account')
    reference = forms.CharField(max_length=80, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from bank_management.models import BankAccount
        self.fields['bank_account'].queryset = BankAccount.objects.filter(active=True)


class AdvanceReasonForm(StyledForm, forms.Form):
    reason = forms.CharField(max_length=240)
