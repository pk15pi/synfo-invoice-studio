"""Read-only employee self-service pages.

The signed-in user is matched to an employee record by work or personal email,
so no extra link table is required. Every query is scoped to that employee;
payslip downloads re-check ownership as well.
"""
import calendar
from collections import Counter
from datetime import date

from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from attendance.models import AttendanceRecord, LeaveBalance
from employee_management.models import Employee, EmployeePayroll
from employee_management.pdf.payslip import payslip_for
from employee_management.security import local_only


def employee_for(user):
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    return (Employee.objects
            .filter(Q(work_email__iexact=email) | Q(personal_email__iexact=email))
            .order_by('employee_id').first())


def _employee_or_response(request):
    employee = employee_for(request.user)
    if employee is None:
        response = render(request, 'access_control/self_service_unlinked.html',
                          {'account_email': request.user.email}, status=200)
        return None, response
    return employee, None


def _month_start(request):
    raw = request.GET.get('month', '')
    try:
        year, month = (int(part) for part in raw.split('-', 1))
        return date(year, month, 1)
    except (ValueError, TypeError):
        today = timezone.localdate()
        return date(today.year, today.month, 1)


def _shift_month(start, delta):
    index = start.year * 12 + (start.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


@never_cache
@local_only
def dashboard(request):
    employee, response = _employee_or_response(request)
    if response is not None:
        return response
    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)
    balances = (LeaveBalance.objects.filter(employee=employee, year=today.year)
                .select_related('leave_type'))
    payslips = (EmployeePayroll.objects
                .filter(employee=employee, status=EmployeePayroll.FINALIZED, is_current=True)
                .select_related('period').order_by('-period__year', '-period__month')[:6])
    leave_applications = (employee.leave_applications.select_related('leave_type')
                          .order_by('-start_date', '-id')[:6])
    month_records = AttendanceRecord.objects.filter(
        employee=employee, date__year=today.year, date__month=today.month)
    counts = Counter(month_records.values_list('status', flat=True))
    attendance_summary = [{'label': label, 'count': counts.get(code, 0)}
                          for code, label in AttendanceRecord.STATUSES]
    return render(request, 'access_control/self_service_dashboard.html', {
        'employee': employee,
        'month_start': month_start,
        'balances': balances,
        'payslips': payslips,
        'leave_applications': leave_applications,
        'attendance_summary': attendance_summary,
        'attendance_marked': sum(counts.values()),
    })


@never_cache
@local_only
def attendance(request):
    employee, response = _employee_or_response(request)
    if response is not None:
        return response
    month_start = _month_start(request)
    last_day = calendar.monthrange(month_start.year, month_start.month)[1]
    month_end = date(month_start.year, month_start.month, last_day)
    records = (AttendanceRecord.objects
               .filter(employee=employee, date__range=(month_start, month_end))
               .select_related('leave_type').order_by('date'))
    counts = Counter(record.status for record in records)
    summary = [{'code': code, 'label': label, 'count': counts.get(code, 0)}
               for code, label in AttendanceRecord.STATUSES]
    return render(request, 'access_control/self_service_attendance.html', {
        'employee': employee,
        'month_start': month_start,
        'previous_month': _shift_month(month_start, -1),
        'next_month': _shift_month(month_start, 1),
        'records': records,
        'summary': summary,
    })


@never_cache
@local_only
def leave(request):
    employee, response = _employee_or_response(request)
    if response is not None:
        return response
    year = timezone.localdate().year
    balances = (LeaveBalance.objects.filter(employee=employee, year=year)
                .select_related('leave_type').order_by('leave_type__name'))
    applications = (employee.leave_applications.select_related('leave_type')
                    .order_by('-start_date', '-id')[:200])
    return render(request, 'access_control/self_service_leave.html', {
        'employee': employee,
        'year': year,
        'balances': balances,
        'applications': applications,
    })


@never_cache
@local_only
def payslips(request):
    employee, response = _employee_or_response(request)
    if response is not None:
        return response
    rows = (EmployeePayroll.objects
            .filter(employee=employee, status=EmployeePayroll.FINALIZED, is_current=True)
            .select_related('period').order_by('-period__year', '-period__month'))
    return render(request, 'access_control/self_service_payslips.html', {
        'employee': employee,
        'payslips': rows,
    })


@never_cache
@local_only
def payslip_pdf(request, pk):
    employee = employee_for(request.user)
    if employee is None:
        raise Http404('No employee record is linked to this account.')
    payroll = get_object_or_404(EmployeePayroll, pk=pk, employee=employee,
                                status=EmployeePayroll.FINALIZED, is_current=True)
    pdf, name = payslip_for(payroll)
    response = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{name}"'
    response['Cache-Control'] = 'no-store, private'
    return response
