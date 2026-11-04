import re
from calendar import monthrange
from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from reporting import periods


PERIOD_CHOICES = [
    ('current_fy', 'Current financial year'),
    ('prev_fy', 'Previous financial year'),
    ('fy', 'Financial year / quarter / half-year'),
    ('month', 'Month'),
    ('this_quarter', 'Current quarter'),
    ('prev_quarter', 'Previous quarter'),
    ('h1', 'Current FY - first half'),
    ('h2', 'Current FY - second half'),
    ('cal_year', 'Calendar year'),
    ('custom', 'Custom date range'),
]


class ReportingPeriodForm(forms.Form):
    period = forms.ChoiceField(choices=PERIOD_CHOICES, initial='current_fy', required=False)
    financial_year = forms.IntegerField(required=False, min_value=1900, max_value=9998)
    financial_year_part = forms.ChoiceField(required=False, choices=periods.FY_PARTS)
    month = forms.CharField(required=False, max_length=7)
    date_from = forms.DateField(required=False, input_formats=['%Y-%m-%d'])
    date_to = forms.DateField(required=False, input_formats=['%Y-%m-%d'])

    def clean(self):
        cleaned = super().clean()
        period = cleaned.get('period') or 'current_fy'
        year = cleaned.get('financial_year')
        month = cleaned.get('month', '')
        date_from, date_to = cleaned.get('date_from'), cleaned.get('date_to')

        if period == 'fy' and not year:
            self.add_error('financial_year', 'Choose the financial year.')
        if period == 'month':
            if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
                self.add_error('month', 'Choose a month in YYYY-MM format.')
        if period == 'custom':
            if not date_from:
                self.add_error('date_from', 'Enter the start date.')
            if not date_to:
                self.add_error('date_to', 'Enter the end date.')
            if date_from and date_to and date_to < date_from:
                self.add_error('date_to', 'End date cannot be before start date.')
        if period != 'custom' and (date_from or date_to):
            raise ValidationError('Custom dates are only accepted for a custom date range.')
        if period != 'month' and month:
            raise ValidationError('A month can only be selected for a monthly period.')
        if period != 'fy' and (year or cleaned.get('financial_year_part')):
            raise ValidationError('Financial year selections require the financial year period.')
        return cleaned

    def period_params(self):
        if not self.is_valid():
            raise ValueError('The reporting period form must be valid.')
        period = self.cleaned_data.get('period') or 'current_fy'
        params = {'period': period}
        if period == 'fy':
            params['fy'] = str(self.cleaned_data['financial_year'])
            if self.cleaned_data.get('financial_year_part'):
                params['fy_part'] = self.cleaned_data['financial_year_part']
        elif period == 'month':
            params['month'] = self.cleaned_data['month']
        elif period == 'custom':
            params['date_from'] = self.cleaned_data['date_from'].isoformat()
            params['date_to'] = self.cleaned_data['date_to'].isoformat()
        return params
