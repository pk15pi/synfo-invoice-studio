from django import forms
from django.contrib.auth import get_user_model
from django.forms import formset_factory, inlineformset_factory
from django.utils import timezone
from access_control.permissions import DOCUMENTS, can_access, is_read_only_role, role_for
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
            'year_convention', 'reset_frequency', 'approval_required', 'requires_expiry',
            'default_output_formats', 'is_active',
        )
        widgets = {'categories': forms.CheckboxSelectMultiple()}


class ApprovalStepForm(forms.Form):
    approver = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False,
                                      empty_label='Select an approver')
    ORDER = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_users = get_user_model().objects.filter(is_active=True).order_by(
            'first_name', 'last_name', 'username',
        )
        eligible = [user.pk for user in active_users
                    if user.is_superuser
                    or (can_access(user, DOCUMENTS) and not is_read_only_role(role_for(user)))]
        self.fields['approver'].queryset = active_users.filter(pk__in=eligible)


ApprovalStepFormSet = formset_factory(ApprovalStepForm, extra=3, max_num=10, validate_max=True,
                                      can_delete=True, can_order=True)


class IssueDocumentForm(forms.Form):
    official_issue_date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))


class SendDocumentForm(forms.Form):
    sent_recipient = forms.CharField(max_length=180, label='Recipient')
    delivery_method = forms.ChoiceField(choices=[
        ('email', 'Email'), ('physical_copy', 'Physical Copy'),
        ('hand_delivery', 'Hand Delivery'), ('other', 'Other'),
    ])
    sent_date = forms.DateField(initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    sent_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))


class SignedDocumentForm(forms.Form):
    signer_name = forms.CharField(max_length=160, label='Confirmed signer')
    comments = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    confirmed = forms.BooleanField(label='I confirm this signing was verified manually', required=True)


class CancelDocumentForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), max_length=2000)


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
