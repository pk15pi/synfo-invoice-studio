from django import forms

from .models import CompanyDocumentProfile


class CompanyDocumentProfileForm(forms.ModelForm):
    class Meta:
        model = CompanyDocumentProfile
        fields = (
            'legal_entity_name', 'brand_name', 'llpin', 'registered_office_address',
            'business_address', 'gstin', 'pan', 'official_email', 'contact_number',
            'website', 'logo', 'partner_details', 'document_footer', 'jurisdiction',
            'default_language',
        )
        widgets = {
            'logo': forms.FileInput,
            'partner_details': forms.Textarea(attrs={'rows': 4}),
        }
