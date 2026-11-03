import json

from django import forms

from .models import DueDateEntry, StatutoryPayment, StatutoryRule


class RuleForm(forms.ModelForm):
    params = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}),
                             help_text='JSON. See the format for each kind below.')

    class Meta:
        model = StatutoryRule
        fields = ['kind', 'key', 'effective_from', 'effective_to', 'params', 'active', 'source_note']
        widgets = {'effective_from': forms.DateInput(attrs={'type': 'date'}),
                   'effective_to': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk and not self.is_bound:
            self.initial['params'] = json.dumps(self.instance.params)

    def clean_params(self):
        try:
            v = json.loads(self.cleaned_data['params'])
        except ValueError:
            raise forms.ValidationError('Params must be valid JSON.')
        if not isinstance(v, dict):
            raise forms.ValidationError('Params must be a JSON object.')
        return v

    def save(self, commit=True):
        obj = super().save(commit=False)
        if obj.pk and obj.is_example and self.changed_data:
            obj.is_example = False  # the user has now edited (and is taking responsibility for) it
        if commit:
            obj.save()
        return obj


class PaymentForm(forms.ModelForm):
    class Meta:
        model = StatutoryPayment
        fields = ['kind', 'period_year', 'period_month', 'amount', 'date', 'bank_account', 'challan_ref', 'remarks']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from bank_management.models import BankAccount
        self.fields['bank_account'].queryset = BankAccount.objects.filter(active=True)


class DueDateForm(forms.ModelForm):
    class Meta:
        model = DueDateEntry
        fields = ['name', 'kind', 'day_of_month', 'month_offset', 'months', 'active', 'notes']


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=300)
