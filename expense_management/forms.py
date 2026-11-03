from django import forms

from employee_management.models import Department, Employee
from employee_management.validators import validate_ifsc, validate_pan
from invoice_app.models import Client, ProjectTemplate

from .models import (PAYMENT_METHODS, CompanyExpense, CompanyExpenseAttachment, EmployeeExpenseAttachment,
                     EmployeeExpenseClaim, ExpenseCategory, ExpenseSubcategory, Vendor)

DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
GSTIN_RE = r'^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]{3}$'


class StyledForm(forms.ModelForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            if isinstance(f.widget, forms.DateInput):
                f.widget = DATE
                f.input_formats = ['%Y-%m-%d', '%d-%m-%Y']
            if not isinstance(f.widget, (forms.CheckboxInput,)):
                f.widget.attrs.setdefault('class', 'field')


def _subcat_field(form, types):
    form.fields['subcategory'].queryset = ExpenseSubcategory.objects.filter(active=True).select_related('category')
    form.fields['subcategory'].label_from_instance = lambda s: s.name
    form.fields['category'].queryset = ExpenseCategory.objects.filter(active=True, category_type__in=types)
    inst = form.instance
    if inst.pk:  # keep a since-deactivated category/subcategory selectable on old records
        form.fields['category'].queryset = ExpenseCategory.objects.filter(
            pk__in=list(form.fields['category'].queryset.values_list('pk', flat=True)) + [inst.category_id])
        if inst.subcategory_id:
            form.fields['subcategory'].queryset = ExpenseSubcategory.objects.filter(
                pk__in=list(form.fields['subcategory'].queryset.values_list('pk', flat=True)) + [inst.subcategory_id])


def _link_fields(form):
    if 'client' in form.fields:
        form.fields['client'].queryset = Client.objects.all()
    if 'project' in form.fields:
        form.fields['project'].queryset = ProjectTemplate.objects.select_related('client')
    if 'department' in form.fields:
        form.fields['department'].queryset = Department.objects.all()


class CompanyExpenseForm(StyledForm):
    class Meta:
        model = CompanyExpense
        fields = ['expense_date', 'vendor', 'category', 'subcategory', 'description', 'invoice_number', 'invoice_date',
                  'due_date', 'amount_mode', 'amount_before_tax', 'gst_amount', 'cgst_amount', 'sgst_amount',
                  'igst_amount', 'other_taxes', 'discount', 'total_amount', 'currency', 'exchange_rate', 'client',
                  'project', 'department', 'employee', 'remarks']
        widgets = {'remarks': forms.Textarea(attrs={'rows': 2}), 'expense_date': DATE, 'invoice_date': DATE,
                   'due_date': DATE}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _subcat_field(self, [ExpenseCategory.COMPANY, ExpenseCategory.BOTH])
        _link_fields(self)
        vq = Vendor.objects.filter(status=Vendor.ACTIVE)
        if self.instance.vendor_id:
            vq = Vendor.objects.filter(pk__in=list(vq.values_list('pk', flat=True)) + [self.instance.vendor_id])
        self.fields['vendor'].queryset = vq
        self.fields['employee'].queryset = Employee.objects.select_related('department')
        self.fields['employee'].required = False
        for f in ('amount_before_tax', 'gst_amount', 'cgst_amount', 'sgst_amount', 'igst_amount', 'other_taxes',
                  'discount', 'total_amount'):
            self.fields[f].required = False
            self.fields[f].widget.attrs.update(step='0.01', min='0')
        self.fields['exchange_rate'].widget.attrs.update(step='0.0001', min='0')
        self.fields['total_amount'].help_text = 'Auto = before tax + GST + other taxes - discount (or enter directly).'
        self.fields['cgst_amount'].help_text = 'Optional split for the GST report.'

    def clean(self):
        d = super().clean()
        from .models import ZERO
        for f in ('amount_before_tax', 'gst_amount', 'cgst_amount', 'sgst_amount', 'igst_amount', 'other_taxes',
                  'discount', 'total_amount'):
            if d.get(f) is None:
                d[f] = ZERO
                setattr(self.instance, f, ZERO)
        return d


class PaymentForm(forms.Form):
    payment_date = forms.DateField(widget=DATE, input_formats=['%Y-%m-%d', '%d-%m-%Y'])
    method = forms.ChoiceField(choices=PAYMENT_METHODS)
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    reference = forms.CharField(label='Transaction reference', max_length=80, required=False)
    bank_account = forms.CharField(label='Bank account used', max_length=120, required=False)
    bank = forms.ModelChoiceField(label='Paid from (ledger)', queryset=None, required=False,
                                  empty_label='Default bank account')
    tds_amount = forms.DecimalField(label='TDS deducted', max_digits=14, decimal_places=2, min_value=0,
                                    required=False)
    remarks = forms.CharField(max_length=300, required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from bank_management.models import BankAccount
        self.fields['bank'].queryset = BankAccount.objects.filter(active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault('class', 'field')


class ReimbursementForm(PaymentForm):
    payment_date = None
    tds_amount = None
    reimbursement_date = forms.DateField(widget=DATE, input_formats=['%Y-%m-%d', '%d-%m-%Y'])
    field_order = ['reimbursement_date', 'amount', 'method', 'bank_account', 'bank', 'reference', 'remarks']


class ClaimForm(StyledForm):
    class Meta:
        model = EmployeeExpenseClaim
        fields = ['employee', 'claim_date', 'expense_date', 'category', 'subcategory', 'description', 'amount_claimed',
                  'currency', 'exchange_rate', 'business_purpose', 'client', 'project', 'travel_from', 'travel_to',
                  'travel_start_date', 'travel_end_date', 'remarks']
        widgets = {'remarks': forms.Textarea(attrs={'rows': 2}), 'claim_date': DATE, 'expense_date': DATE,
                   'travel_start_date': DATE, 'travel_end_date': DATE}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _subcat_field(self, [ExpenseCategory.CLAIM, ExpenseCategory.BOTH])
        _link_fields(self)
        eq = Employee.objects.filter(status__in=Employee.EMPLOYED)
        if self.instance.employee_id:
            eq = Employee.objects.filter(pk__in=list(eq.values_list('pk', flat=True)) + [self.instance.employee_id])
        self.fields['employee'].queryset = eq.select_related('department')
        self.fields['amount_claimed'].widget.attrs.update(step='0.01', min='0.01')
        self.fields['exchange_rate'].widget.attrs.update(step='0.0001', min='0')


class VendorForm(StyledForm):
    class Meta:
        model = Vendor
        fields = ['name', 'vendor_type', 'contact_person', 'mobile', 'email', 'address', 'city', 'state', 'country',
                  'gstin', 'pan', 'website', 'bank_name', 'bank_account_number', 'ifsc', 'remarks', 'status']
        widgets = {'address': forms.Textarea(attrs={'rows': 2}), 'remarks': forms.Textarea(attrs={'rows': 2})}

    def clean_gstin(self):
        import re
        v = (self.cleaned_data.get('gstin') or '').upper().strip()
        if v and not re.match(GSTIN_RE, v):
            raise forms.ValidationError('GSTIN must be 15 characters, e.g. 06AABFQ2999K1ZQ.')
        return v

    def clean_pan(self):
        v = (self.cleaned_data.get('pan') or '').upper().strip()
        validate_pan(v)
        return v

    def clean_ifsc(self):
        v = (self.cleaned_data.get('ifsc') or '').upper().strip()
        validate_ifsc(v)
        return v


class QuickVendorForm(VendorForm):
    class Meta(VendorForm.Meta):
        fields = ['name', 'vendor_type', 'gstin', 'mobile', 'email', 'city', 'state']


class CategoryForm(StyledForm):
    class Meta:
        model = ExpenseCategory
        fields = ['name', 'category_type', 'description', 'active']


class SubcategoryForm(StyledForm):
    class Meta:
        model = ExpenseSubcategory
        fields = ['name', 'active']


class AttachmentTypeForm(forms.Form):
    def __init__(self, choices, *a, **kw):
        super().__init__(*a, **kw)
        self.fields['doc_type'] = forms.ChoiceField(choices=choices, widget=forms.Select(attrs={'class': 'field'}))


COMPANY_DOC_TYPES = CompanyExpenseAttachment.TYPES
CLAIM_DOC_TYPES = EmployeeExpenseAttachment.TYPES
