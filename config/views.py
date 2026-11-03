"""QuantBud Business Management landing page (links into every section)."""
from django.shortcuts import redirect, render

from employee_management.models import Department, Employee, EmployeePayroll, PayrollPeriod
from invoice_app.models import Client, Invoice


def home(request):
    if request.META.get('REMOTE_ADDR') not in ('127.0.0.1', '::1'):
        return redirect('dashboard')  # employee figures are local-only
    period = PayrollPeriod.objects.first()
    ctx = {'n_customers': Client.objects.count(), 'n_invoices': Invoice.objects.count(),
           'n_employed': Employee.objects.filter(status__in=Employee.EMPLOYED).count(),
           'n_departments': Department.objects.filter(active=True).count(), 'period': period,
           'n_payslips': EmployeePayroll.objects.filter(status='finalized', is_current=True).count()}
    from django.utils import timezone
    from attendance.models import AttendanceRecord, LeaveApplication
    ctx.update(n_marked_today=AttendanceRecord.objects.filter(date=timezone.localdate()).count(),
               n_pending_leave=LeaveApplication.objects.filter(status='pending').count())
    return render(request, 'invoice_app/home.html', ctx)
