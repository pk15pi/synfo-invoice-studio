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
            'renewal_enabled', 'renewal_lead_time_days', 'default_output_formats', 'is_active',
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


class DocumentShareForm(forms.Form):
    recipient_type = forms.ChoiceField(choices=[
        ('employee', 'Employee'), ('client', 'Client'),
        ('external', 'External recipient'), ('other', 'Other authorised recipient'),
    ])
    party = forms.ModelChoiceField(queryset=None, required=False, label='Linked employee / client / party')
    recipient_name = forms.CharField(max_length=180)
    recipient_email = forms.EmailField(required=False)
    sharing_method = forms.ChoiceField(choices=[
        ('email', 'Email (record manually)'), ('physical_copy', 'Physical copy'),
        ('hand_delivery', 'Hand delivery'), ('other', 'Other'),
    ])
    acknowledgement_requested = forms.BooleanField(required=False, initial=False)
    signature_required = forms.BooleanField(required=False)
    signatories = forms.CharField(required=False, label='Required signatories (one per line: Name | Role)',
                                  widget=forms.Textarea(attrs={'rows': 3}))
    next_follow_up_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.document = document
        if document:
            from .models import DocumentParty
            self.fields['party'].queryset = DocumentParty.objects.filter(
                generated_document=document,
            ).order_by('display_order', 'display_name')

    def clean(self):
        cleaned = super().clean()
        party = cleaned.get('party')
        if party:
            cleaned['recipient_name'] = party.display_name
        parsed = []
        for line in (cleaned.get('signatories') or '').splitlines():
            name, _, role = line.partition('|')
            if name.strip():
                parsed.append((name.strip()[:180], role.strip()[:120]))
        if cleaned.get('signature_required') and not parsed:
            self.add_error('signatories', 'Add at least one required signatory.')
        if not cleaned.get('signature_required') and parsed:
            self.add_error('signatories', 'Select that a signature is required before adding signatories.')
        cleaned['parsed_signatories'] = parsed
        return cleaned


class DocumentDeliveryForm(forms.Form):
    delivered = forms.BooleanField(required=True, label='I confirm that the document was handed over / delivered')
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    evidence = forms.FileField(required=False, label='Delivery evidence (PDF/JPG/PNG, up to 10 MB)')


class DocumentAcknowledgementForm(forms.Form):
    status = forms.ChoiceField(choices=[
        ('acknowledged', 'Acknowledged'), ('declined', 'Declined'), ('unable', 'Unable to acknowledge'),
    ])
    acknowledgement_name = forms.CharField(max_length=180, label='Recipient name')
    method = forms.ChoiceField(choices=[
        ('manual', 'Recorded by staff'), ('email', 'Email reply recorded'),
        ('physical', 'Signed physical evidence'), ('other', 'Other recorded method'),
    ])
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    evidence = forms.FileField(required=False, label='Evidence (PDF/JPG/PNG, up to 10 MB)')


class DocumentReminderForm(forms.Form):
    method = forms.ChoiceField(choices=[('email', 'Email (tracked manually)'), ('phone', 'Phone'),
                                        ('other', 'Other')])
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    next_follow_up_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))


class DocumentVerificationForm(forms.Form):
    decision = forms.ChoiceField(choices=[('verified', 'Verified'), ('rejected', 'Verification rejected')])
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('decision') == 'rejected' and not cleaned.get('reason', '').strip():
            self.add_error('reason', 'Enter a reason for rejecting verification.')
        return cleaned


class SignedCopyUploadForm(forms.Form):
    file = forms.FileField(label='Signed copy (PDF/JPG/PNG, up to 10 MB)')


class RevokeShareForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), max_length=2000)


class SignatoryDecisionForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), max_length=2000)


class RenewalUpdateForm(forms.Form):
    status = forms.ChoiceField(choices=[
        ('initiated', 'Renewal Initiated'), ('in_progress', 'Renewal In Progress'),
        ('renewed', 'Renewed'), ('declined', 'Renewal Declined'),
    ])
    lead_time_days = forms.IntegerField(min_value=0, max_value=3650, initial=90)
    renewal_document = forms.ModelChoiceField(queryset=None, required=False,
                                              label='Issued superseding document')
    renewal_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    new_expiry_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import GeneratedDocument
        self.document = document
        self.fields['renewal_document'].queryset = GeneratedDocument.objects.filter(
            supersedes=document,
            status__in=('issued', 'sent', 'signed', 'acknowledged'),
        ) if document else GeneratedDocument.objects.none()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('status') == 'renewed' and not cleaned.get('renewal_document'):
            self.add_error('renewal_document', 'Choose an issued superseding version before marking renewed.')
        return cleaned


class DocumentAutomationRuleForm(forms.ModelForm):
    intervals = forms.CharField(required=False, help_text='Comma-separated calendar days, e.g. 1,3,5',
                                max_length=120)

    class Meta:
        from .models import DocumentAutomationRule
        model = DocumentAutomationRule
        fields = ('name', 'enabled', 'escalation_after_days', 'escalation_recipients',
                  'calendar_days', 'in_app_enabled')
        widgets = {'escalation_recipients': forms.SelectMultiple(attrs={'size': 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['intervals'].initial = ','.join(str(day) for day in (self.instance.intervals_days or []))
        eligible = [user.pk for user in get_user_model().objects.filter(is_active=True)
                    if user.is_superuser or (can_access(user, DOCUMENTS)
                    and not is_read_only_role(role_for(user)))]
        self.fields['escalation_recipients'].queryset = get_user_model().objects.filter(
            pk__in=eligible).order_by('first_name', 'last_name', 'username')

    def clean_intervals(self):
        value = self.cleaned_data.get('intervals', '')
        try:
            days = sorted(set(int(part.strip()) for part in value.split(',') if part.strip()))
        except ValueError as exc:
            raise forms.ValidationError('Enter whole numbers separated by commas.') from exc
        if any(day < 0 or day > 3650 for day in days):
            raise forms.ValidationError('Reminder intervals must be between 0 and 3650 days.')
        return days

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.intervals_days = self.cleaned_data['intervals']
        if commit:
            instance.save()
        return instance


class DocumentComplianceRequirementForm(forms.ModelForm):
    class Meta:
        from .models import DocumentComplianceRequirement
        model = DocumentComplianceRequirement
        fields = ('name', 'document_type', 'recipient_type', 'mandatory', 'validity_period_days',
                  'approval_required', 'signature_required', 'acknowledgement_required',
                  'responsible_person', 'review_frequency_days', 'is_active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['document_type'].queryset = DocumentType.objects.order_by('name')
        self.fields['responsible_person'].queryset = get_user_model().objects.filter(is_active=True)


class DocumentCorrectiveActionForm(forms.ModelForm):
    class Meta:
        from .models import DocumentCorrectiveAction
        model = DocumentCorrectiveAction
        fields = ('requirement', 'review', 'generated_document', 'problem', 'responsible_person',
                  'target_date', 'status', 'resolution_notes')
        widgets = {'target_date': forms.DateInput(attrs={'type': 'date'}),
                   'problem': forms.Textarea(attrs={'rows': 3}),
                   'resolution_notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import DocumentComplianceRequirement
        self.fields['requirement'].queryset = DocumentComplianceRequirement.objects.filter(is_active=True)
        self.fields['responsible_person'].queryset = get_user_model().objects.filter(is_active=True)


class DocumentNotificationPreferenceForm(forms.ModelForm):
    class Meta:
        from .models import DocumentNotificationPreference
        model = DocumentNotificationPreference
        fields = ('approval_reminders', 'expiry_reminders', 'renewal_reminders', 'signature_reminders',
                  'acknowledgement_reminders', 'compliance_alerts')


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
