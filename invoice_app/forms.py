import re

from decimal import Decimal
from django import forms
from django.db.models import Q as models_Q

from .models import BusinessProfile, Client, Invoice, InvoiceDescriptionTemplate, InvoiceFormatTemplate, InvoicePayment, ProjectTemplate

HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
COLOR_FIELDS = ['primary_color', 'alternate_row_color', 'white_row_color', 'border_color', 'label_color']


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault('class', 'field')


class BusinessProfileForm(StyledForm):
    class Meta:
        model = BusinessProfile
        fields = ['name', 'address', 'phone', 'email', 'gstin', 'bank_name', 'account_holder', 'account_number',
                  'bank_country', 'ifsc', 'signature_image', 'signature_text', 'footer_text', 'invoice_prefix',
                  'next_invoice_number', 'number_padding', 'payment_terms_days', 'amount_rounding'] + COLOR_FIELDS
        widgets = {'address': forms.Textarea(attrs={'rows': 3}),
                   **{f: forms.TextInput(attrs={'type': 'color'}) for f in COLOR_FIELDS}}

    def clean(self):
        data = super().clean()
        for f in COLOR_FIELDS:
            v = data.get(f)
            if v and not HEX_RE.match(v):
                self.add_error(f, 'Use a #RRGGBB colour.')
            elif v:
                data[f] = v.upper()
        return data


class ClientForm(StyledForm):
    class Meta:
        model = Client
        fields = ['name', 'address', 'country', 'contact_person', 'phone', 'email', 'tax_id', 'billing_type',
                  'terms_mode', 'terms_text', 'tax_treatment', 'gstin', 'state_code', 'state_name', 'lut_number',
                  'vendor_code', 'invoice_format', 'active', 'notes']
        labels = {'name': 'Bill To (customer name)', 'invoice_format': 'Default invoice format'}
        widgets = {'billing_type': forms.RadioSelect, 'terms_mode': forms.RadioSelect,'address': forms.Textarea(attrs={'rows': 3}), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional in POSTs from older forms/scripts: missing = Export / No tax.
        self.fields['tax_treatment'].required = False
        self.fields['gstin'].widget.attrs.update({'maxlength': 15, 'style': 'text-transform:uppercase',
                                                  'placeholder': 'e.g. 06AABFQ2999K1ZQ'})
        current = getattr(self.instance, 'invoice_format_id', None)
        fmt = self.fields['invoice_format']
        fmt.queryset = InvoiceFormatTemplate.objects.filter(
            models_Q(is_active=True) | models_Q(pk=current or 0)).order_by('name')
        fmt.empty_label = 'Default (QuantBud Standard / Tax Invoice)'

    def clean_tax_treatment(self):
        return self.cleaned_data.get('tax_treatment') or 'export'

    def clean_gstin(self):
        return (self.cleaned_data.get('gstin') or '').strip().upper()


class ProjectTemplateForm(StyledForm):
    """Project = Consultant -Project Name + Memo/Description configuration + the amount the
    customer's billing type needs. The customer's billing type is the source of truth."""
    class Meta:
        model = ProjectTemplate
        fields = ['name', 'invoice_description_type', 'description_template', 'description',
                  'monthly_amount', 'daily_rate', 'sac_code', 'gst_rate', 'active']
        labels = {'name': 'Consultant -Project Name', 'invoice_description_type': 'Invoice Memo/Description',
                  'description_template': 'Description template', 'description': 'Custom Memo/Description',
                  'monthly_amount': 'Monthly amount', 'daily_rate': 'Unit Price (per day)'}
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        if client is not None:
            if client.billing_type == Client.PER_DAY:
                self.fields.pop('monthly_amount')
            else:
                self.fields.pop('daily_rate')
        # Only active templates for new selections; a currently assigned inactive one stays valid.
        current = self.instance.description_template_id if self.instance.pk else None
        from django.db.models import Q
        self.fields['description_template'].queryset = InvoiceDescriptionTemplate.objects.filter(
            Q(is_active=True) | Q(pk=current or 0))
        self.fields['description_template'].empty_label = '-- Select a template --'
        self.fields['description'].required = False
        # GST fields are optional in POSTs (older forms/scripts): blank = SAC 998314 @ 18%.
        self.fields['sac_code'].required = False
        self.fields['gst_rate'].required = False

    def clean_sac_code(self):
        return (self.cleaned_data.get('sac_code') or '').strip() or '998314'

    def clean_gst_rate(self):
        from decimal import Decimal
        v = self.cleaned_data.get('gst_rate')
        if v is None:
            return Decimal('18.00')
        if v < 0 or v > 100:
            raise forms.ValidationError('GST rate must be between 0 and 100.')
        return v

    def clean(self):
        data = super().clean()
        kind = data.get('invoice_description_type')
        tpl = data.get('description_template')
        # Required-field rules per type live in ProjectTemplate.clean() (model level).
        if kind == ProjectTemplate.DESC_TEMPLATE:
            if tpl is not None and not tpl.is_active and tpl.pk != self.instance.description_template_id:
                self.add_error('description_template', 'This template is inactive; pick an active one.')
        else:
            data['description_template'] = None  # only Fixed Template keeps a template link
        return data


class InvoiceDescriptionTemplateForm(StyledForm):
    class Meta:
        model = InvoiceDescriptionTemplate
        fields = ['name', 'template_text', 'is_active']
        widgets = {'template_text': forms.Textarea(attrs={'rows': 2})}


class InvoiceFormatTemplateForm(StyledForm):
    class Meta:
        model = InvoiceFormatTemplate
        fields = ['name', 'code', 'layout_engine', 'page_size', 'orientation', 'accent_color', 'show_logo',
                  'title_text', 'col_project_label', 'col_description_label', 'col_qty_label',
                  'col_unit_price_label', 'col_amount_label', 'show_po_number', 'show_vendor_code',
                  'show_bank_details', 'show_signature', 'show_amount_in_words', 'notes_text', 'declaration_text',
                  'is_active']
        widgets = {'notes_text': forms.Textarea(attrs={'rows': 2}),
                   'declaration_text': forms.Textarea(attrs={'rows': 2}),
                   'accent_color': forms.TextInput(attrs={'type': 'color'})}

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('Enter a format name.')
        return name

    def clean_accent_color(self):
        v = (self.cleaned_data.get('accent_color') or '').strip()
        if not HEX_RE.match(v):
            raise forms.ValidationError('Use a hex colour like #0B5394.')
        return v.upper()

    def clean(self):
        data = super().clean()
        if data.get('layout_engine') == InvoiceFormatTemplate.STANDARD and (
                data.get('page_size') != InvoiceFormatTemplate.LETTER or
                data.get('orientation') != InvoiceFormatTemplate.PORTRAIT):
            self.add_error('page_size', 'The QuantBud Standard engine reproduces the original invoice exactly and '
                                        'is fixed to US Letter portrait. Pick another engine for A4 / landscape.')
        return data


class PdfOverrideForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['pdf_number_override']
        widgets = {'pdf_number_override': forms.TextInput(attrs={'class': 'field', 'placeholder': 'leave blank to use saved number'})}


class CreditNoteForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    reason = forms.CharField(max_length=300)
    taxable_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal('0.01'),
                                        help_text='GST is reversed proportionally to the invoice tax.')


class PaymentForm(StyledForm):
    confirm_early = forms.BooleanField(required=False, label='Yes, the receipt date is before the invoice date')

    class Meta:
        model = InvoicePayment
        fields = ['receipt_date', 'amount', 'mode', 'reference', 'deductions', 'bank_charges', 'bank_account', 'notes']
        widgets = {'receipt_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        self.overpay_warning = ''
        self.fields['bank_charges'].required = False
        from bank_management.models import BankAccount
        self.fields['bank_account'].queryset = BankAccount.objects.filter(active=True) | BankAccount.objects.filter(
            pk=getattr(self.instance, 'bank_account_id', None) or 0)
        self.fields['bank_account'].empty_label = 'Default bank account'

    def clean(self):
        d = super().clean()
        amt, ded, rd = d.get('amount'), d.get('deductions') or 0, d.get('receipt_date')
        if amt is not None and amt <= 0:
            self.add_error('amount', 'Amount must be greater than zero.')
        if ded is not None and ded < 0:
            self.add_error('deductions', 'Cannot be negative.')
        bc = d.get('bank_charges') or 0
        d['bank_charges'] = bc
        if bc < 0 or bc > ded:
            self.add_error('bank_charges', 'Bank charges must be between 0 and the total TDS / charges deducted.')
        inv = self.invoice
        if inv and rd and rd < inv.invoice_date and not d.get('confirm_early'):
            self.add_error('confirm_early', f'Receipt date is before the invoice date ({inv.invoice_date:%d %b %Y}). Tick to confirm.')
        if inv and amt:
            others = inv.payments.exclude(pk=self.instance.pk) if self.instance.pk else inv.payments.all()
            settled = sum((p.amount + p.deductions for p in others), 0) + amt + ded + inv.credited_total
            if settled > inv.amount_due:
                self.overpay_warning = f'Warning: this makes the invoice overpaid by {settled - inv.amount_due:,.2f}.'
        return d
