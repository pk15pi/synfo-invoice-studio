from django import forms

from employee_management.forms import DATE, StyledForm
from employee_management.models import Employee

from .models import AttendanceRecord, AttendanceSettings, Holiday, LeaveApplication, LeaveBalance, LeaveType

TIME = forms.TimeInput(attrs={'type': 'time'}, format='%H:%M')


class AttendanceSettingsForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AttendanceSettings
        fields = ['weekly_off_days', 'off_saturdays']


class HolidayForm(StyledForm, forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['date', 'name', 'optional']
        widgets = {'date': DATE}


class LeaveTypeForm(StyledForm, forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ['code', 'name', 'paid', 'annual_quota', 'carry_forward', 'carry_forward_cap', 'active']

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class LeaveBalanceForm(StyledForm, forms.ModelForm):
    class Meta:
        model = LeaveBalance
        fields = ['opening', 'accrued']


class LeaveApplicationForm(StyledForm, forms.ModelForm):
    class Meta:
        model = LeaveApplication
        fields = ['employee', 'leave_type', 'start_date', 'end_date', 'half_day', 'reason']
        widgets = {'start_date': DATE, 'end_date': DATE}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields['employee'].queryset = Employee.objects.filter(status__in=Employee.EMPLOYED).order_by('employee_id')
        self.fields['leave_type'].queryset = LeaveType.objects.filter(active=True)


class AttendanceRecordForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ['status', 'leave_type', 'in_time', 'out_time', 'note']
        widgets = {'in_time': TIME, 'out_time': TIME}


class ImportForm(StyledForm, forms.Form):
    year = forms.IntegerField(min_value=2000, max_value=2100)
    month = forms.IntegerField(min_value=1, max_value=12)
    file = forms.FileField(label='Attendance CSV')
    reason = forms.CharField(label='Correction reason (needed for months that already have payroll)',
                             max_length=240, required=False)
