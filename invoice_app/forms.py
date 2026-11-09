import re

from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q as models_Q

from .models import (BusinessProfile, Client, Invoice, InvoiceDescriptionTemplate, InvoiceEmailDefaults,
                     InvoiceEmailTemplate, InvoiceFormatTemplate, InvoicePayment, ManpowerDeployment,
                     ProjectTemplate, RecruitmentAgreement, RecruitmentCandidate, RecruitmentPlacement,
                     RecruitmentRequisition)

HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
COLOR_FIELDS = ['primary_color', 'alternate_row_color', 'white_row_color', 'border_color', 'label_color']


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault('class', 'field')


class BusinessProfileForm(StyledForm):
    manpower_resource_display_default = forms.ChoiceField(
        choices=BusinessProfile.MANPOWER_DISPLAY, required=False,
        label='Default manpower invoice display')

    class Meta:
        model = BusinessProfile
        fields = ['name', 'address', 'phone', 'email', 'gstin', 'bank_name', 'account_holder', 'account_number',
                  'bank_country', 'ifsc', 'brand_name', 'business_logo', 'signature_image', 'signature_text', 'footer_text', 'invoice_prefix',
                  'next_invoice_number', 'number_padding', 'payment_terms_days', 'amount_rounding'] + COLOR_FIELDS
        fields += ['manpower_resource_display_default']
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

    LOGO_TYPES = {'png': b'\x89PNG\r\n\x1a\n', 'jpg': b'\xff\xd8\xff', 'jpeg': b'\xff\xd8\xff', 'webp': b'RIFF', 'svg': None}
    LOGO_MAX = 3 * 1024 * 1024

    def clean_business_logo(self):
        f = self.cleaned_data.get('business_logo')
        if not f or getattr(f, '_committed', False):  # nothing new uploaded (or the logo was cleared)
            return f
        ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
        if ext not in self.LOGO_TYPES:
            raise forms.ValidationError('Upload a PNG, JPG, WebP or SVG image.')
        if f.size > self.LOGO_MAX:
            raise forms.ValidationError('The logo must be 3 MB or smaller.')
        f.seek(0)
        data = f.read()
        f.seek(0)
        if ext == 'svg':
            from .branding import svg_is_safe
            if not svg_is_safe(data):
                raise forms.ValidationError('This SVG contains scripts or external content and cannot be used.')
            return f
        magic = self.LOGO_TYPES[ext]
        if not data.startswith(magic) or (ext == 'webp' and data[8:12] != b'WEBP'):
            raise forms.ValidationError('The file is not a valid image of that type.')
        try:
            from io import BytesIO
            from PIL import Image
            Image.open(BytesIO(data)).verify()
        except Exception:
            raise forms.ValidationError('The image could not be read.')
        return f

    def clean_manpower_resource_display_default(self):
        return (self.cleaned_data.get('manpower_resource_display_default') or
                getattr(self.instance, 'manpower_resource_display_default', '') or 'designation')


class ClientForm(StyledForm):
    class Meta:
        model = Client
        fields = ['name', 'brand_name', 'address', 'country_ref', 'state_ref', 'district_ref', 'pin_code', 'contact_person', 'phone', 'email', 'tax_id', 'billing_type',
                  'terms_mode', 'terms_text', 'tax_treatment', 'gstin', 'state_code', 'state_name', 'lut_number',
                  'vendor_code', 'invoice_format', 'cin_llpin', 'nda_signed_date', 'nda_expiry_date',
                  'is_email_active', 'invoice_email_to', 'invoice_email_cc', 'invoice_email_bcc',
                  'invoice_email_use_custom_subject', 'invoice_email_subject_template', 'invoice_email_custom_subject',
                  'invoice_email_use_custom_body', 'invoice_email_body_template', 'invoice_email_custom_body',
                  'active', 'notes']
        labels = {'name': 'Bill To (customer name)', 'invoice_format': 'Default invoice format'}
        widgets = {'nda_signed_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'nda_expiry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'billing_type': forms.RadioSelect, 'terms_mode': forms.RadioSelect,'address': forms.Textarea(attrs={'rows': 3}), 'notes': forms.Textarea(attrs={'rows': 2})}

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
        self._add_doc_links()
        self._setup_address()
        self._setup_invoice_email()

    ADDRESS_BLOCK = ('name', 'country_ref', 'state_ref', 'district_ref', 'pin_code')
    INVOICE_EMAIL_BLOCK = (
        'is_email_active', 'invoice_email_to', 'invoice_email_cc', 'invoice_email_bcc',
        'invoice_email_use_custom_subject', 'invoice_email_subject_template', 'invoice_email_custom_subject',
        'invoice_email_use_custom_body', 'invoice_email_body_template', 'invoice_email_custom_body',
    )
    save_subject_template_name = forms.CharField(
        required=False, label='Save subject as template named',
        help_text='Optional. When set with custom subject text, saves a new subject template and selects it.')
    save_body_template_name = forms.CharField(
        required=False, label='Save body as template named',
        help_text='Optional. When set with custom body text, saves a new body template and selects it.')


    def _setup_address(self):
        from .models import Country, District, State
        cf, sf, df = self.fields['country_ref'], self.fields['state_ref'], self.fields['district_ref']
        cf.required = True
        cf.queryset = Country.objects.all()
        cf.empty_label = 'Select country'
        sf.empty_label = 'Select state'
        df.empty_label = 'Select district'
        sf.queryset = State.objects.select_related('country')
        df.queryset = District.objects.select_related('state')
        if not self.instance.pk and not self.is_bound:
            india = Country.objects.filter(iso2='IN').first()
            if india:
                self.initial.setdefault('country_ref', india.pk)
        # Only the options that belong to the current country / state are rendered;
        # the page fetches the others when the country / state changes.
        def val(name):
            v = self.data.get(self.add_prefix(name)) if self.is_bound else (
                self.initial.get(name) or getattr(self.instance, f'{name}_id', None))
            return int(v) if str(v or '').isdigit() else None
        cid, sid = val('country_ref'), val('state_ref')
        sf.widget.choices = [('', 'Select state')] + list(State.objects.filter(country_id=cid).values_list('pk', 'name')) if cid else [('', 'Select state')]
        df.widget.choices = [('', 'Select district')] + list(District.objects.filter(state_id=sid).values_list('pk', 'name')) if sid else [('', 'Select district')]
        from django.urls import reverse
        cf.widget.attrs.update({'data-combo': 'country'})
        sf.widget.attrs.update({'data-combo': 'state', 'data-src': reverse('geo_states')})
        df.widget.attrs.update({'data-combo': 'district', 'data-src': reverse('geo_districts')})
        self.fields['pin_code'].widget.attrs.update({'inputmode': 'numeric', 'maxlength': 12, 'autocomplete': 'postal-code',
                                                     'placeholder': '6-digit PIN (India)'})
        self.fields['pin_code'].help_text = ''
        sf.help_text = 'Required for India.'

    def _clean_address(self, data):
        country, state, district = data.get('country_ref'), data.get('state_ref'), data.get('district_ref')
        if state and country and state.country_id != country.pk:
            self.add_error('state_ref', 'Choose a state from the selected country.')
            state = None
        if country and country.iso2 == 'IN' and not state and 'state_ref' not in self.errors:
            self.add_error('state_ref', 'State is required for India.')
        if district and (not state or district.state_id != state.pk):
            self.add_error('district_ref', 'Choose a district from the selected state.')

    nda_file = forms.FileField(label='NDA document', required=False, help_text='PDF, JPG or PNG.')
    mca_file = forms.FileField(label='MCA document', required=False, help_text='PDF, JPG or PNG (e.g. MCA master data / incorporation certificate).')
    DOC_FIELDS = [('nda_file', 'nda'), ('mca_file', 'mca')]

    def _add_doc_links(self):
        from django.urls import reverse
        from django.utils.html import format_html
        if not self.instance.pk:
            return
        for name, kind in self.DOC_FIELDS:
            doc = self.instance.current_document(kind)
            if doc:
                self.fields[name].help_text = format_html(
                    'Current: <a href="{}" target="_blank">{}</a> (uploaded {}). Uploading a new file replaces it; the old one is kept in history.',
                    reverse('client_document', args=[doc.pk]), doc.original_filename or 'file', doc.uploaded_at.date())

    def _clean_doc(self, name):
        from employee_management.validators import form_doc_limits, sniff_upload
        f = self.cleaned_data.get(name)
        if f:
            self._doc_ext = getattr(self, '_doc_ext', {})
            self._doc_ext[name] = sniff_upload(f, form_doc_limits()[0] * 1024 * 1024)
        return f


    def _setup_invoice_email(self):
        from .invoice_email import PLACEHOLDERS
        ph = ' '.join(PLACEHOLDERS)
        for name in ('invoice_email_to', 'invoice_email_cc', 'invoice_email_bcc'):
            self.fields[name].widget = forms.TextInput(attrs={'class': 'field'})
        self.fields['invoice_email_to'].widget.attrs['placeholder'] = 'to1@example.com, to2@example.com'
        self.fields['invoice_email_cc'].widget.attrs['placeholder'] = 'cc@example.com'
        self.fields['invoice_email_bcc'].widget.attrs['placeholder'] = 'bcc@example.com'
        self.fields['invoice_email_custom_subject'].widget.attrs.update({'class': 'field', 'placeholder': 'Custom subject'})
        self.fields['invoice_email_custom_body'].widget = forms.Textarea(attrs={'class': 'field', 'rows': 5})
        self.fields['invoice_email_custom_subject'].help_text = f'Placeholders: {ph}'
        self.fields['invoice_email_custom_body'].help_text = f'Placeholders: {ph}'
        self.fields['invoice_email_subject_template'].queryset = InvoiceEmailTemplate.for_customer(
            InvoiceEmailTemplate.SUBJECT)
        self.fields['invoice_email_subject_template'].empty_label = '— Select subject template —'
        self.fields['invoice_email_body_template'].queryset = InvoiceEmailTemplate.for_customer(
            InvoiceEmailTemplate.BODY)
        self.fields['invoice_email_body_template'].empty_label = '— Select body template —'
        self.fields['save_subject_template_name'].widget.attrs.setdefault('class', 'field')
        self.fields['save_body_template_name'].widget.attrs.setdefault('class', 'field')
        # Prefill last-selected defaults for Add Customer
        if not self.instance.pk and not self.is_bound:
            defaults = InvoiceEmailDefaults.current()
            self.initial.setdefault('invoice_email_use_custom_subject', defaults.use_custom_subject)
            self.initial.setdefault('invoice_email_custom_subject', defaults.custom_subject)
            self.initial.setdefault('invoice_email_subject_template', defaults.subject_template_id)
            self.initial.setdefault('invoice_email_use_custom_body', defaults.use_custom_body)
            self.initial.setdefault('invoice_email_custom_body', defaults.custom_body)
            self.initial.setdefault('invoice_email_body_template', defaults.body_template_id)

    def _clean_address_list_field(self, field_name):
        from .invoice_email import parse_address_list
        raw = (self.cleaned_data.get(field_name) or '').strip()
        if not raw:
            return ''
        try:
            addrs = parse_address_list(raw)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages if hasattr(exc, 'messages') else [str(exc)]) from exc
        return ', '.join(addrs)

    def clean_invoice_email_to(self):
        return self._clean_address_list_field('invoice_email_to')

    def clean_invoice_email_cc(self):
        return self._clean_address_list_field('invoice_email_cc')

    def clean_invoice_email_bcc(self):
        return self._clean_address_list_field('invoice_email_bcc')

    def clean_nda_file(self):
        return self._clean_doc('nda_file')

    def clean_mca_file(self):
        return self._clean_doc('mca_file')

    def clean(self):
        data = super().clean()
        s, e = data.get('nda_signed_date'), data.get('nda_expiry_date')
        if s and e and e < s:
            self.add_error('nda_expiry_date', 'Expiry date cannot be before the signed date.')
        self._clean_address(data)
        if data.get('invoice_email_use_custom_subject'):
            if not (data.get('invoice_email_custom_subject') or '').strip():
                # Allowed empty — send uses fallback subject
                pass
        else:
            tpl = data.get('invoice_email_subject_template')
            if tpl and tpl.kind != InvoiceEmailTemplate.SUBJECT:
                self.add_error('invoice_email_subject_template', 'Choose a subject template.')
        if data.get('invoice_email_use_custom_body'):
            pass
        else:
            tpl = data.get('invoice_email_body_template')
            if tpl and tpl.kind != InvoiceEmailTemplate.BODY:
                self.add_error('invoice_email_body_template', 'Choose a body template.')
        return data

    def save(self, commit=True):
        client = super().save(commit=False)
        # Save-as-template for subject
        subj_name = (self.cleaned_data.get('save_subject_template_name') or '').strip()
        custom_subj = (self.cleaned_data.get('invoice_email_custom_subject') or '').strip()
        if subj_name and custom_subj:
            tpl = InvoiceEmailTemplate.objects.create(
                category=InvoiceEmailTemplate.CUSTOMER,
                kind=InvoiceEmailTemplate.SUBJECT, name=subj_name[:120], text=custom_subj)
            client.invoice_email_subject_template = tpl
            client.invoice_email_use_custom_subject = False
            client.invoice_email_custom_subject = ''
        body_name = (self.cleaned_data.get('save_body_template_name') or '').strip()
        custom_body = (self.cleaned_data.get('invoice_email_custom_body') or '').strip()
        if body_name and custom_body:
            tpl = InvoiceEmailTemplate.objects.create(
                category=InvoiceEmailTemplate.CUSTOMER,
                kind=InvoiceEmailTemplate.BODY, name=body_name[:120], text=custom_body)
            client.invoice_email_body_template = tpl
            client.invoice_email_use_custom_body = False
            client.invoice_email_custom_body = ''
        if commit:
            client.save()
            self.save_m2m()
            # Remember last selection as Add Customer default
            defaults = InvoiceEmailDefaults.current()
            defaults.use_custom_subject = client.invoice_email_use_custom_subject
            defaults.custom_subject = client.invoice_email_custom_subject
            defaults.subject_template = client.invoice_email_subject_template
            defaults.use_custom_body = client.invoice_email_use_custom_body
            defaults.custom_body = client.invoice_email_custom_body
            defaults.body_template = client.invoice_email_body_template
            defaults.save()
        return client

    def save_documents(self, client):
        import os
        from django.db import transaction
        from employee_management.validators import CONTENT_TYPES
        from .models import ClientDocument
        made = []
        with transaction.atomic():
            for name, kind in self.DOC_FIELDS:
                f = self.cleaned_data.get(name)
                if not f:
                    continue
                ext = self._doc_ext[name]
                doc = ClientDocument(client=client, kind=kind, original_filename=os.path.basename(f.name)[:255],
                                     content_type=CONTENT_TYPES[ext], file_size=f.size)
                doc._validated_ext = ext
                doc.file.save('upload', f, save=False)
                doc.save()
                ClientDocument.objects.filter(client=client, kind=kind, is_current=True).exclude(pk=doc.pk) \
                    .update(is_current=False)
                made.append(doc)
        return made

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


class ManpowerDeploymentForm(StyledForm):
    rate_note = forms.CharField(label='Rate revision note', required=False, max_length=240,
                                help_text='Required when changing an existing commercial rate.')

    class Meta:
        model = ManpowerDeployment
        fields = ['client', 'project', 'employee', 'resource_name', 'designation', 'department_skill',
                  'work_location', 'work_location_other', 'start_date', 'end_date', 'billing_method',
                  'billing_rate', 'billing_frequency', 'proration_method', 'rate_effective_from',
                  'contract_reference', 'po_number', 'agreement_reference', 'contract_start', 'contract_end',
                  'payment_terms', 'overtime_rate', 'weekend_rate', 'minimum_monthly_commitment',
                  'additional_charges', 'discount', 'recurring', 'status', 'remarks']
        widgets = {'start_date': forms.DateInput(attrs={'type': 'date'}),
                   'end_date': forms.DateInput(attrs={'type': 'date'}),
                   'rate_effective_from': forms.DateInput(attrs={'type': 'date'}),
                   'contract_start': forms.DateInput(attrs={'type': 'date'}),
                   'contract_end': forms.DateInput(attrs={'type': 'date'}),
                   'remarks': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.order_by('name')
        self.fields['project'].queryset = ProjectTemplate.objects.select_related('client').order_by('client__name', 'name')
        from employee_management.models import Employee
        self.fields['employee'].queryset = Employee.objects.order_by('employee_id')
        self.fields['project'].required = False
        self.fields['employee'].required = False
        self.fields['resource_name'].required = False
        self.fields['rate_effective_from'].required = False
        self.fields['rate_effective_from'].help_text = 'The commercial rate applies from this date. Earlier revisions remain in history.'

    def clean(self):
        data = super().clean()
        if not data.get('rate_effective_from') and data.get('start_date'):
            data['rate_effective_from'] = data['start_date']
        if data.get('project') and data.get('client') and data['project'].client_id != data['client'].pk:
            self.add_error('project', 'Choose a project belonging to the selected client.')
        if not data.get('employee') and not (data.get('resource_name') or '').strip():
            self.add_error('resource_name', 'Enter a resource description or select an employee.')
        if data.get('billing_method') == ManpowerDeployment.MONTHLY and data.get('proration_method') == ManpowerDeployment.MANUAL:
            self.add_error('proration_method', 'Manual proration is selected on the invoice with a mandatory reason.')
        return data


class RecruitmentCandidateForm(StyledForm):
    class Meta:
        model = RecruitmentCandidate
        fields = ['candidate_name', 'default_designation', 'candidate_reference', 'notes']
        widgets = {'notes': forms.Textarea(attrs={'rows': 2})}

    def clean_candidate_reference(self):
        return self.cleaned_data.get('candidate_reference') or None

    def clean_candidate_name(self):
        name = (self.cleaned_data.get('candidate_name') or '').strip()
        qs = RecruitmentCandidate.objects.filter(candidate_name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists() and not self.cleaned_data.get('candidate_reference'):
            raise forms.ValidationError('A candidate with this name already exists. Select that record for another client, or provide a distinct existing recruitment reference if this is a different person with the same name.')
        return name


class RecruitmentRequisitionForm(StyledForm):
    class Meta:
        model = RecruitmentRequisition
        fields = ['client', 'title', 'reference', 'status']


class RecruitmentAgreementForm(StyledForm):
    class Meta:
        model = RecruitmentAgreement
        fields = ['client', 'billing_method', 'percentage', 'fixed_fee', 'ctc_basis', 'ctc_basis_description',
                  'effective_start', 'effective_end', 'agreement_reference', 'minimum_fee', 'maximum_fee',
                  'payment_terms', 'sac_code', 'gst_rate', 'billing_trigger', 'retention_days', 'custom_trigger_description',
                  'guarantee_days', 'replacement_terms', 'refund_terms', 'guarantee_conditions', 'status']
        widgets = {'effective_start': forms.DateInput(attrs={'type': 'date'}),
                   'effective_end': forms.DateInput(attrs={'type': 'date'}),
                   'replacement_terms': forms.Textarea(attrs={'rows': 2}),
                   'refund_terms': forms.Textarea(attrs={'rows': 2}),
                   'guarantee_conditions': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['client'].queryset = Client.objects.order_by('name')


class RecruitmentPlacementForm(StyledForm):
    fee_override_amount = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=2,
        label='Approved fee override', help_text='Optional. Requires a reason and is audit logged.')
    fee_override_reason = forms.CharField(required=False, max_length=500, label='Fee override reason')

    class Meta:
        model = RecruitmentPlacement
        fields = ['client', 'candidate', 'requisition', 'agreement', 'designation', 'expected_annual_ctc', 'finalized_annual_ctc',
                  'monthly_ctc', 'fixed_components', 'variable_components', 'employer_contributions',
                  'other_ctc_components', 'agreed_basis_amount', 'ctc_finalized', 'ctc_approved_on',
                  'client_approval_reference', 'offer_reference', 'offer_accepted_on', 'joining_date',
                  'custom_trigger_date', 'custom_trigger_reference', 'invoice_display_format']
        widgets = {f: forms.DateInput(attrs={'type': 'date'}) for f in
                   ('ctc_approved_on', 'offer_accepted_on', 'joining_date', 'custom_trigger_date')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        client_id = (self.data.get('client') if self.is_bound else
                     self.instance.client_id if self.instance.pk else self.initial.get('client'))
        if not str(client_id or '').isdigit():
            client_id = None
        self.fields['client'].queryset = Client.objects.order_by('name')
        self.fields['candidate'].queryset = RecruitmentCandidate.objects.order_by('candidate_name')
        self.fields['requisition'].queryset = RecruitmentRequisition.objects.filter(client_id=client_id).order_by('title')
        self.fields['agreement'].queryset = RecruitmentAgreement.objects.filter(client_id=client_id).exclude(status='draft').order_by('-effective_start')
        self.fields['requisition'].required = False
        self.fields['client_approval_reference'].help_text = 'Client confirmation of the final offer and compensation basis.'
        self.fields['ctc_finalized'].help_text = 'Check only after the client has approved the final CTC; expected/proposed amounts do not qualify.'

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.invoice_id and self.instance.invoice.status != Invoice.DRAFT:
            raise forms.ValidationError('This placement is already invoiced; use the existing credit-note workflow for adjustments.')
        override = data.get('fee_override_amount')
        if override is not None and not (data.get('fee_override_reason') or '').strip():
            self.add_error('fee_override_reason', 'A fee override requires a reason.')
        if data.get('ctc_finalized') and not (data.get('client_approval_reference') or '').strip():
            self.add_error('client_approval_reference', 'Record the client approval reference for finalized CTC.')
        return data


class InvoiceDescriptionTemplateForm(StyledForm):
    class Meta:
        model = InvoiceDescriptionTemplate
        fields = ['name', 'template_text', 'is_active']
        widgets = {'template_text': forms.Textarea(attrs={'rows': 2})}




class InvoiceEmailTemplateForm(StyledForm):
    class Meta:
        model = InvoiceEmailTemplate
        fields = ['category', 'kind', 'name', 'text']
        labels = {'kind': 'Type', 'text': 'Text'}
        widgets = {'text': forms.Textarea(attrs={'rows': 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .invoice_email import PLACEHOLDERS
        self.fields['text'].help_text = (
            'Optional placeholders: ' + ', '.join(PLACEHOLDERS) + '. '
            '{month}/{year} follow the invoice date or salary month. '
            'Salary-only: {employee_name}, {employee_id}, {salary_month}, {salary_year}.'
        )

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('Enter a template name.')
        return name[:120]

    def clean_text(self):
        return (self.cleaned_data.get('text') or '').strip()


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
