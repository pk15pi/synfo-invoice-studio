"""Attendance & Leave models.

Rules
* One AttendanceRecord per employee per day. Unmarked days are derived from the calendar
  (holiday / weekly off) or counted as present ("attendance by exception").
* A month is LOCKED for an employee once their current payroll for that month is finalized
  and no draft revision is open. Changes then need a payroll revision first.
* Leave balances are per employee, leave type and calendar year; `used` moves only through
  approving / cancelling leave applications.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

ZERO = Decimal('0.00')
DAYS = dict(max_digits=6, decimal_places=2, default=ZERO)
WEEKDAYS = [(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday'),
            (6, 'Sunday')]


class AttendanceLockedError(ValidationError):
    pass


class AttendanceSettings(models.Model):
    """Singleton (pk=1): weekly-off pattern."""
    weekly_off_days = models.CharField(
        'Weekly off days', max_length=20, default='6',
        help_text='Comma-separated weekday numbers, Monday=0 ... Sunday=6. Default: Sunday only.')
    off_saturdays = models.CharField(
        'Also off on these Saturdays of the month', max_length=20, blank=True,
        help_text='E.g. "2,4" for 2nd and 4th Saturday. Leave blank if Saturday is listed above or is working.')

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @staticmethod
    def _ints(s):
        return {int(x) for x in (s or '').replace(' ', '').split(',') if x.strip().isdigit()}

    @property
    def off_weekdays(self):
        return self._ints(self.weekly_off_days)

    @property
    def off_saturday_numbers(self):
        return self._ints(self.off_saturdays)

    def is_week_off(self, d):
        if d.weekday() in self.off_weekdays:
            return True
        return d.weekday() == 5 and ((d.day - 1) // 7 + 1) in self.off_saturday_numbers

    def clean(self):
        if any(x > 6 for x in self.off_weekdays):
            raise ValidationError({'weekly_off_days': 'Use weekday numbers 0 (Monday) to 6 (Sunday).'})
        if any(x < 1 or x > 5 for x in self.off_saturday_numbers):
            raise ValidationError({'off_saturdays': 'Saturday numbers must be 1 to 5.'})

    @property
    def description(self):
        names = [n for i, n in WEEKDAYS if i in self.off_weekdays]
        sat = sorted(self.off_saturday_numbers)
        if sat:
            names.append(' & '.join({1: '1st', 2: '2nd', 3: '3rd', 4: '4th', 5: '5th'}[x] for x in sat) + ' Saturday')
        return ', '.join(names) or 'None'


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)
    optional = models.BooleanField('Optional / restricted holiday (not auto-applied)', default=False)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f'{self.date:%d-%m-%Y} {self.name}'


class LeaveType(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=80)
    paid = models.BooleanField(default=True)
    annual_quota = models.DecimalField('Annual quota (days)', max_digits=6, decimal_places=2, default=ZERO)
    carry_forward = models.BooleanField(default=False)
    carry_forward_cap = models.DecimalField('Carry-forward cap (days, blank = no cap)', max_digits=6,
                                            decimal_places=2, null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.code})'

    @property
    def needs_balance(self):
        """Unpaid leave (LWP) never needs a balance."""
        return self.paid


class LeaveBalance(models.Model):
    employee = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                 related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='balances')
    year = models.PositiveSmallIntegerField()
    opening = models.DecimalField('Opening (carried forward)', **DAYS)
    accrued = models.DecimalField(**DAYS)
    used = models.DecimalField(**DAYS)

    class Meta:
        ordering = ['-year', 'employee__employee_id', 'leave_type__name']
        constraints = [models.UniqueConstraint(fields=['employee', 'leave_type', 'year'],
                                               name='unique_leave_balance')]

    @property
    def balance(self):
        return self.opening + self.accrued - self.used

    @classmethod
    def for_year(cls, employee, leave_type, year):
        return cls.objects.get_or_create(employee=employee, leave_type=leave_type, year=year,
                                         defaults={'accrued': leave_type.annual_quota})[0]


class LeaveApplication(models.Model):
    PENDING, APPROVED, REJECTED, CANCELLED = 'pending', 'approved', 'rejected', 'cancelled'
    STATUSES = [(PENDING, 'Pending'), (APPROVED, 'Approved'), (REJECTED, 'Rejected'), (CANCELLED, 'Cancelled')]
    OPEN = (PENDING, APPROVED)
    employee = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                 related_name='leave_applications')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name='applications')
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField('Half day (single date only)', default=False)
    days = models.DecimalField(**DAYS)
    reason = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-start_date', '-id']

    def __str__(self):
        return f'{self.employee_id} {self.leave_type.code} {self.start_date:%d-%m-%Y}'

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before start date.'})
        if self.half_day and self.start_date != self.end_date:
            raise ValidationError({'half_day': 'Half day is only allowed for a single date.'})


class AttendanceRecord(models.Model):
    PRESENT, ABSENT, HALF_DAY, LEAVE, HOLIDAY, WEEK_OFF = 'P', 'A', 'HD', 'L', 'H', 'WO'
    STATUSES = [(PRESENT, 'Present'), (ABSENT, 'Absent'), (HALF_DAY, 'Half Day'), (LEAVE, 'Leave'),
                (HOLIDAY, 'Holiday'), (WEEK_OFF, 'Week Off')]
    employee = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                 related_name='attendance')
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=2, choices=STATUSES)
    leave_type = models.ForeignKey(LeaveType, null=True, blank=True, on_delete=models.PROTECT,
                                   help_text='Required for Leave; optional for Half Day (other half on leave).')
    in_time = models.TimeField(null=True, blank=True)
    out_time = models.TimeField(null=True, blank=True)
    application = models.ForeignKey(LeaveApplication, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='attendance_days')
    note = models.CharField(max_length=160, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'employee__employee_id']
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='one_attendance_per_day')]

    def clean(self):
        if self.status == self.LEAVE and not self.leave_type_id:
            raise ValidationError({'leave_type': 'Choose the leave type.'})
        if self.status not in (self.LEAVE, self.HALF_DAY):
            self.leave_type = None
        if self.in_time and self.out_time and self.out_time < self.in_time:
            raise ValidationError({'out_time': 'Out time cannot be before in time.'})

    # Stage 6: set ``correction_reason`` before saving / deleting a day whose month already has payroll.
    correction_reason = None

    def describe(self):
        s = self.get_status_display()
        if self.leave_type_id:
            s += f' ({self.leave_type.code})'
        if self.in_time or self.out_time:
            s += f' {self.in_time:%H:%M}' if self.in_time else ' --'
            s += f'-{self.out_time:%H:%M}' if self.out_time else '-'
        if self.note:
            s += f' [{self.note}]'
        return s

    def save(self, *args, **kwargs):
        from .services import assert_unlocked, record_correction
        assert_unlocked(self.employee, self.date)
        if self.status not in (self.LEAVE, self.HALF_DAY):
            self.leave_type = None
        old = AttendanceRecord.objects.filter(pk=self.pk).first() if self.pk else \
            AttendanceRecord.objects.filter(employee_id=self.employee_id, date=self.date).first()
        before = old.describe() if old else '(not marked)'
        after = self.describe()
        if before != after:
            record_correction(self, before, after, self.correction_reason, check_only=True)
        super().save(*args, **kwargs)
        if before != after:
            record_correction(self, before, after, self.correction_reason)

    def delete(self, *args, **kwargs):
        from .services import assert_unlocked, record_correction
        assert_unlocked(self.employee, self.date)
        record_correction(self, self.describe(), '(not marked)', self.correction_reason, check_only=True)
        res = super().delete(*args, **kwargs)
        record_correction(self, self.describe(), '(not marked)', self.correction_reason)
        return res


class AttendanceCorrection(models.Model):
    """History of changes to attendance days whose month already has a payroll record (draft revision or
    generated draft). Changes in a month with a finalized payroll are blocked until a revision is opened."""
    employee = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                 related_name='attendance_corrections')
    date = models.DateField(db_index=True)
    old_value = models.CharField(max_length=200)
    new_value = models.CharField(max_length=200)
    reason = models.CharField(max_length=240)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at', '-id']

    def __str__(self):
        return f'{self.employee_id} {self.date}: {self.old_value} -> {self.new_value}'


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def daterange(a, b):
    while a <= b:
        yield a
        a += timedelta(days=1)


def now():
    return timezone.now()
