import re

from django import forms

from .models import BusinessProfile, Client, Invoice, ProjectTemplate

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
        fields = ['name', 'address', 'country', 'contact_person', 'phone', 'email', 'tax_id', 'default_terms_days', 'notes']
        widgets = {'address': forms.Textarea(attrs={'rows': 3}), 'notes': forms.Textarea(attrs={'rows': 2})}


class ProjectTemplateForm(StyledForm):
    class Meta:
        model = ProjectTemplate
        fields = ['name', 'description', 'pricing_type', 'monthly_amount', 'fixed_monthly_even_if_partial',
                  'proration_basis', 'daily_rate']


class PdfOverrideForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['pdf_number_override']
        widgets = {'pdf_number_override': forms.TextInput(attrs={'class': 'field', 'placeholder': 'leave blank to use saved number'})}
