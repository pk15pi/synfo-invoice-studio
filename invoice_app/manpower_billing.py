"""Client billing calculations for manpower deployments (never linked to salary amounts)."""
import calendar
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError

from . import billing
from .models import ManpowerDeployment


def month_bounds(day):
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def overlap(start, end, period_start, period_end):
    left = max(start, period_start)
    right = min(end or period_end, period_end)
    return (left, right) if left <= right else (None, None)


def working_days(start, end):
    """Use the project's configured Attendance weekly-off pattern and official holidays."""
    from attendance.models import AttendanceSettings, Holiday
    settings = AttendanceSettings.objects.filter(pk=1).first() or AttendanceSettings()
    holidays = set(Holiday.objects.filter(date__gte=start, date__lte=end, optional=False)
                   .values_list('date', flat=True))
    count, day = 0, start
    while day <= end:
        if not settings.is_week_off(day) and day not in holidays:
            count += 1
        day += timedelta(days=1)
    return count


def _active_rate(deployment, at_date):
    revision = deployment.rate_revisions.filter(effective_from__lte=at_date).order_by('-effective_from', '-id').first()
    if revision:
        return revision.billing_method, revision.rate, revision.proration_method
    return deployment.billing_method, deployment.billing_rate, deployment.proration_method


def _round(value, rounding):
    return billing.round_amount(value, rounding)


def calculate_deployment(deployment, period_start, period_end, rounding, *, quantity=Decimal('1'),
                         billable_quantity=None, manual_amount=None, remarks=''):
    """Calculate a deployment charge and return immutable billable values for invoice snapshots.

    Monthly rates use rate-revision segments. Day/hour rates with a mid-period rate change require
    separately approved quantities; the caller must split them instead of guessing the allocation.
    """
    if period_end < period_start:
        raise ValidationError('Billing period end must be on or after the start date.')
    start, end = overlap(deployment.start_date, deployment.end_date, period_start, period_end)
    if start is None:
        raise ValidationError('The deployment does not overlap the invoice billing period.')
    revisions = list(deployment.rate_revisions.filter(effective_from__lte=end).order_by('effective_from', 'id'))
    method, rate, proration = _active_rate(deployment, start)
    if method == ManpowerDeployment.MONTHLY:
        if proration == ManpowerDeployment.MANUAL:
            if manual_amount is None or not remarks.strip():
                raise ValidationError('Manual amount override requires an amount and a reason.')
            return method, 'month', quantity, rate, billing.q2(manual_amount), start, end
        amount = Decimal('0.00')
        cursor = start
        while cursor <= end:
            month_start, month_end = month_bounds(cursor)
            slice_end = min(end, month_end)
            slice_revisions = [r for r in revisions if cursor < r.effective_from <= slice_end]
            if proration == ManpowerDeployment.FULL_MONTH and slice_revisions:
                raise ValidationError('A full-month fixed charge cannot be split by a mid-month rate revision; use another proration method or enter a reviewed manual amount.')
            segments = []
            seg_start, seg_method, seg_rate, seg_proration = cursor, method, rate, proration
            for revision in slice_revisions:
                segments.append((seg_start, revision.effective_from - timedelta(days=1), seg_method, seg_rate, seg_proration))
                seg_start, seg_method, seg_rate, seg_proration = revision.effective_from, revision.billing_method, revision.rate, revision.proration_method
            segments.append((seg_start, slice_end, seg_method, seg_rate, seg_proration))
            for seg_start, seg_end, seg_method, seg_rate, seg_proration in segments:
                if seg_method != ManpowerDeployment.MONTHLY:
                    raise ValidationError('A monthly billing period contains a non-monthly rate change; split the invoice rows by effective date.')
                if seg_proration == ManpowerDeployment.FULL_MONTH:
                    amount += seg_rate
                    continue
                if seg_proration == ManpowerDeployment.CALENDAR:
                    divisor = Decimal(calendar.monthrange(cursor.year, cursor.month)[1])
                    billable = Decimal((seg_end - seg_start).days + 1)
                elif seg_proration == ManpowerDeployment.THIRTY:
                    divisor, billable = Decimal('30'), Decimal((seg_end - seg_start).days + 1)
                elif seg_proration == ManpowerDeployment.WORKING:
                    divisor = Decimal(working_days(month_start, month_end))
                    billable = Decimal(working_days(seg_start, seg_end))
                else:
                    raise ValidationError('Choose a supported monthly proration method.')
                if divisor <= 0:
                    raise ValidationError('The configured proration divisor is zero for this billing period.')
                amount += seg_rate * billable / divisor
            # The latest commercial terms apply to the next calendar slice.
            after_slice = slice_end + timedelta(days=1)
            if after_slice <= end:
                method, rate, proration = _active_rate(deployment, after_slice)
            cursor = after_slice
        return ManpowerDeployment.MONTHLY, 'month', quantity, rate, _round(amount, rounding), start, end
    if method in (ManpowerDeployment.DAILY, ManpowerDeployment.HOURLY):
        mid_period = [r for r in revisions if start < r.effective_from <= end]
        if mid_period:
            raise ValidationError('This period contains a rate revision. Split the approved billable quantity across the effective dates before invoicing.')
        q = billable_quantity
        if q is None or q <= 0:
            raise ValidationError('Enter an approved billable day/hour quantity greater than zero.')
        return method, 'day' if method == ManpowerDeployment.DAILY else 'hour', q, rate, _round(rate * q, rounding), start, end
    if method == ManpowerDeployment.FIXED:
        amount = manual_amount if manual_amount is not None else rate
        if manual_amount is not None and not remarks.strip():
            raise ValidationError('A fixed amount adjustment requires a reason.')
        return method, 'contract', Decimal('1'), rate, billing.q2(amount), start, end
    raise ValidationError('Choose a supported manpower billing method.')
