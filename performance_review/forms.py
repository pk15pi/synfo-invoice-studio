from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from employee_management.models import Employee
from invoice_app.models import Client, RecruitmentCandidate

from .models import (CandidateApplication, PerformanceSettings, PerformanceStage, RecruiterActivity,
                     RecruiterAssignment, RecruitmentJD, DailyBrief, DailyBriefJDDetail)


class RecruitmentJDForm(forms.ModelForm):
    client = forms.ModelChoiceField(queryset=Client.objects.all(), required=False,
                                    empty_label='No linked customer')

    class Meta:
        model = RecruitmentJD
        fields = ['reference', 'client', 'client_name', 'job_title', 'opening_date', 'status', 'remarks']
        widgets = {'opening_date': forms.DateInput(attrs={'type': 'date'}), 'remarks': forms.Textarea(attrs={'rows': 2})}


class RecruiterAssignmentForm(forms.ModelForm):
    class Meta:
        model = RecruiterAssignment
        fields = ['jd', 'recruiter', 'started_on', 'status', 'remarks']
        widgets = {'started_on': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        recruiters = Employee.objects.filter(is_recruiter=True, status__in=Employee.EMPLOYED)
        if self.instance and self.instance.pk:
            recruiters = Employee.objects.filter(is_recruiter=True) | Employee.objects.filter(
                pk=self.instance.recruiter_id)
        self.fields['recruiter'].queryset = recruiters.select_related('department').distinct().order_by('employee_id')
        self.fields['recruiter'].label_from_instance = lambda e: (
            f'{e.employee_id} · {e.full_name} · {e.department.name} · {e.designation} · {e.get_status_display()}')

    def clean_recruiter(self):
        recruiter = self.cleaned_data['recruiter']
        if not recruiter.is_recruiter and not (self.instance.pk and recruiter.pk == self.instance.recruiter_id):
            raise ValidationError('Mark this employee as a recruiter in Employee Management before assigning a new JD.')
        if recruiter.status not in Employee.EMPLOYED and not (self.instance.pk and recruiter.pk == self.instance.recruiter_id):
            raise ValidationError('Only active, probation or confirmed employees can receive a new assignment.')
        return recruiter


class RecruiterActivityForm(forms.ModelForm):
    class Meta:
        model = RecruiterActivity
        fields = ['assignment', 'activity_date', 'resumes_submitted', 'resumes_rejected', 'candidates_selected',
                  'offers_released', 'candidates_joined', 'remarks']
        widgets = {'activity_date': forms.DateInput(attrs={'type': 'date'}), 'remarks': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignment'].queryset = RecruiterAssignment.objects.select_related('jd', 'recruiter')
        self.fields['assignment'].label_from_instance = lambda a: f'{a.jd.reference} · {a.jd.job_title} · {a.recruiter.full_name}'


class CandidateApplicationForm(forms.ModelForm):
    class Meta:
        model = CandidateApplication
        fields = ['assignment', 'candidate', 'submission_date', 'stage', 'outcome', 'rejection_stage',
                  'rejection_reason', 'last_transition_date', 'selected_date', 'rejected_date',
                  'offer_released_date', 'offer_accepted_date', 'joined_date', 'remarks']
        widgets = {name: forms.DateInput(attrs={'type': 'date'}) for name in
                   ('submission_date', 'last_transition_date', 'selected_date', 'rejected_date',
                    'offer_released_date', 'offer_accepted_date', 'joined_date')}
        widgets['remarks'] = forms.Textarea(attrs={'rows': 2})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assignment'].queryset = RecruiterAssignment.objects.select_related('jd', 'recruiter')
        self.fields['assignment'].label_from_instance = lambda a: f'{a.jd.reference} · {a.jd.job_title} · {a.recruiter.full_name}'
        self.fields['candidate'].queryset = RecruitmentCandidate.objects.order_by('candidate_name', 'candidate_code')
        self.fields['stage'].queryset = PerformanceStage.objects.filter(kind=PerformanceStage.STAGE, active=True)
        self.fields['rejection_stage'].queryset = PerformanceStage.objects.filter(
            kind__in=[PerformanceStage.STAGE, PerformanceStage.REJECTION_STAGE], active=True)
        self.fields['rejection_reason'].queryset = PerformanceStage.objects.filter(
            kind=PerformanceStage.REJECTION_REASON, active=True)
        self.fields['rejection_reason'].required = False
        self.fields['rejection_stage'].required = False


class PerformanceSettingsForm(forms.ModelForm):
    class Meta:
        model = PerformanceSettings
        fields = ['joined_weight', 'selected_weight', 'quality_weight', 'jd_weight', 'financial_year_start_month']


class PerformanceStageForm(forms.ModelForm):
    class Meta:
        model = PerformanceStage
        fields = ['name', 'kind', 'sort_order', 'active']


class DailyBriefForm(forms.ModelForm):
    class Meta:
        model = DailyBrief
        fields = ['recruiter', 'activity_date', 'achieved']
        widgets = {'recruiter': forms.Select(attrs={'class': 'field'}),
                   'activity_date': forms.DateInput(attrs={'type': 'date', 'class': 'field'}),
                   'achieved': forms.NumberInput(attrs={'class': 'field', 'min': 0, 'step': 1})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        recruiters = Employee.objects.filter(is_recruiter=True, status__in=Employee.EMPLOYED)
        if self.instance and self.instance.pk:
            recruiters = Employee.objects.filter(pk__in=recruiters.values('pk')) | Employee.objects.filter(
                pk=self.instance.recruiter_id)
        self.fields['recruiter'].queryset = recruiters.select_related('department').distinct().order_by(
            'employee_id')
        self.fields['recruiter'].label_from_instance = lambda e: f'{e.employee_id} · {e.full_name}'
        self.fields['activity_date'].initial = self.fields['activity_date'].initial or timezone.localdate
        self.fields['achieved'].required = False
        self.duplicate_record = None

    def clean_achieved(self):
        value = self.cleaned_data.get('achieved')
        return 0 if value in (None, '') else value

    def clean(self):
        cleaned = super().clean()
        recruiter = cleaned.get('recruiter')
        activity_date = cleaned.get('activity_date')
        if recruiter and activity_date:
            duplicate = DailyBrief.objects.filter(recruiter=recruiter, activity_date=activity_date)
            if self.instance and self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            self.duplicate_record = duplicate.first()
            if self.duplicate_record:
                raise ValidationError('A Daily Brief already exists for this recruiter and activity date. '
                                      'Review the existing record or open it to edit.')
        return cleaned


class DailyBriefJDDetailForm(forms.ModelForm):
    row_active = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = DailyBriefJDDetail
        fields = ['jd_number', 'total_resumes_submitted', 'row_order']
        widgets = {'jd_number': forms.TextInput(attrs={'class': 'field', 'maxlength': 80}),
                   'total_resumes_submitted': forms.NumberInput(attrs={'class': 'field', 'min': 0, 'step': 1}),
                   'row_order': forms.HiddenInput()}

    def clean_jd_number(self):
        return self.cleaned_data['jd_number'].strip()


class BaseDailyBriefJDDetailFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            data = getattr(form, 'cleaned_data', {})
            if not data or data.get('DELETE'):
                continue
            number = data.get('jd_number', '').strip()
            key = number.casefold()
            if key in seen:
                raise ValidationError(f'JD Number “{number}” appears more than once in this Daily Brief.')
            seen.add(key)


DailyBriefJDDetailFormSet = inlineformset_factory(
    DailyBrief, DailyBriefJDDetail, form=DailyBriefJDDetailForm, formset=BaseDailyBriefJDDetailFormSet,
    fields=['jd_number', 'total_resumes_submitted', 'row_order'], extra=1, min_num=1,
    validate_min=True, can_delete=True, can_delete_extra=True)


class RecruiterSelectionForm(forms.Form):
    recruiters = forms.ModelMultipleChoiceField(
        label='Active employees who work as recruiters', queryset=Employee.objects.none(), required=False,
        widget=forms.SelectMultiple(attrs={'size': 9, 'class': 'field'}),
        help_text='Select from the existing Employee records. Former employees with historical activity remain available in reports.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recruiters'].queryset = Employee.objects.filter(status__in=Employee.EMPLOYED).select_related(
            'department').order_by('employee_id')
        self.fields['recruiters'].label_from_instance = lambda e: (
            f'{e.employee_id} · {e.full_name} · {e.department.name} · {e.designation}')

    def save(self):
        selected = set(self.cleaned_data['recruiters'].values_list('pk', flat=True))
        for employee in Employee.objects.filter(status__in=Employee.EMPLOYED):
            value = employee.pk in selected
            if employee.is_recruiter != value:
                employee.is_recruiter = value
                employee.save(update_fields=['is_recruiter', 'updated_at'])

