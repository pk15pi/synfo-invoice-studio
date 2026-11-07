from django import forms
from django.forms import inlineformset_factory
from django.utils.text import slugify

from .models import (
    CompanyDocumentProfile, DocumentCategory, DocumentClause, DocumentTemplate,
    DocumentTemplateClause, DocumentTemplateVersion, DocumentType,
)


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


class DocumentCategoryForm(forms.ModelForm):
    class Meta:
        model = DocumentCategory
        fields = ('code', 'name', 'description', 'display_order', 'is_active')


class DocumentTypeForm(forms.ModelForm):
    class Meta:
        model = DocumentType
        fields = (
            'code', 'name', 'categories', 'description', 'number_prefix', 'number_padding',
            'year_convention', 'reset_frequency', 'approval_required', 'default_output_formats', 'is_active',
        )
        widgets = {'categories': forms.CheckboxSelectMultiple()}


class DocumentTemplateForm(forms.ModelForm):
    class Meta:
        model = DocumentTemplate
        fields = (
            'code', 'name', 'document_type', 'category', 'description',
            'applicable_party_type', 'business_scenario', 'template_format',
            'default_output_format', 'effective_date', 'retirement_date',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'effective_date': forms.DateInput(attrs={'type': 'date'}),
            'retirement_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document_type'].queryset = DocumentType.objects.filter(is_active=True)
        self.fields['category'].queryset = DocumentCategory.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('code') and cleaned.get('document_type') and cleaned.get('name'):
            base = slugify(f"{cleaned['document_type'].code}-{cleaned['name']}")[:100].strip('-') or 'document-template'
            code, suffix = base, 2
            query = DocumentTemplate.objects.exclude(pk=self.instance.pk) if self.instance.pk else DocumentTemplate.objects.all()
            while query.filter(code=code).exists():
                tail = f'-{suffix}'
                code = f'{base[:100-len(tail)]}{tail}'
                suffix += 1
            cleaned['code'] = code
        return cleaned


class DocumentTemplateVersionForm(forms.ModelForm):
    class Meta:
        model = DocumentTemplateVersion
        fields = ('header_text', 'body_template', 'footer_text', 'output_formats',
                  'signature_configuration', 'effective_date', 'retirement_date', 'change_description')
        widgets = {
            'header_text': forms.Textarea(attrs={'rows': 2}),
            'body_template': forms.Textarea(attrs={'rows': 22, 'spellcheck': 'false'}),
            'footer_text': forms.Textarea(attrs={'rows': 2}),
            'signature_configuration': forms.Textarea(attrs={'rows': 3}),
            'effective_date': forms.DateInput(attrs={'type': 'date'}),
            'retirement_date': forms.DateInput(attrs={'type': 'date'}),
            'change_description': forms.Textarea(attrs={'rows': 2}),
        }


class DocumentClauseForm(forms.ModelForm):
    class Meta:
        model = DocumentClause
        fields = (
            'code', 'name', 'category', 'clause_text', 'applicable_document_types',
            'is_mandatory', 'default_inclusion', 'effective_date', 'review_date', 'admin_notes',
        )
        widgets = {
            'clause_text': forms.Textarea(attrs={'rows': 12}),
            'applicable_document_types': forms.CheckboxSelectMultiple(),
            'effective_date': forms.DateInput(attrs={'type': 'date'}),
            'review_date': forms.DateInput(attrs={'type': 'date'}),
            'admin_notes': forms.Textarea(attrs={'rows': 3}),
        }


class DocumentTemplateClauseForm(forms.ModelForm):
    class Meta:
        model = DocumentTemplateClause
        fields = ('clause', 'display_order', 'is_mandatory', 'text_override')
        widgets = {'text_override': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['clause'].queryset = DocumentClause.objects.filter(is_active=True).order_by('category', 'name', '-version')


DocumentTemplateClauseFormSet = inlineformset_factory(
    DocumentTemplateVersion, DocumentTemplateClause, form=DocumentTemplateClauseForm,
    extra=1, can_delete=True,
)
