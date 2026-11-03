from datetime import date, time

from django.core.management.base import BaseCommand, CommandError

from employee_management.models import Employee

from ... import services as svc
from ...models import AttendanceRecord, LeaveApplication, LeaveType, daterange, month_bounds

TAG = '[sample]'


class Command(BaseCommand):
    help = 'Seed sample attendance for EMP001 (default November 2026): present days, 1 CL, 1 LWP, 1 half day, 1 absent.'

    def add_arguments(self, parser):
        parser.add_argument('--employee', default='EMP001')
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--month', type=int, default=11)

    def handle(self, *a, employee, year, month, **kw):
        e = Employee.objects.filter(employee_id=employee).first()
        if not e:
            raise CommandError(f'{employee} not found (run make_sample_employee first).')
        start, end = month_bounds(year, month)
        for app in LeaveApplication.objects.filter(employee=e, reason__startswith=TAG,
                                                   status__in=LeaveApplication.OPEN,
                                                   start_date__range=(start, end)):
            svc.cancel(app)
        LeaveApplication.objects.filter(employee=e, reason__startswith=TAG, status=LeaveApplication.CANCELLED,
                                        start_date__range=(start, end)).delete()
        AttendanceRecord.objects.filter(employee=e, date__range=(start, end), application__isnull=True).delete()
        settings, hol = svc.AttendanceSettings.current(), svc.holiday_map(start, end)
        svc.ensure_balances(year, [e])
        work = [d for d in daterange(*svc.service_range(e, start, end)) if svc.is_working_day(d, hol, settings)]
        cl_day, lwp_day, hd_day, ab_day = work[3], work[9], work[14], work[18]
        for d in daterange(*svc.service_range(e, start, end)):
            st = svc.default_status(d, hol, settings) or AttendanceRecord.PRESENT
            if d in (cl_day, lwp_day, hd_day):
                continue
            r = AttendanceRecord(employee=e, date=d, status=st, note=TAG)
            if d == ab_day:
                r.status = AttendanceRecord.ABSENT
            if r.status == AttendanceRecord.PRESENT:
                r.in_time, r.out_time = time(9, 30), time(18, 30)
            r.save()
        for d, code, half in ((cl_day, 'CL', False), (lwp_day, 'LWP', False), (hd_day, 'CL', True)):
            app = LeaveApplication(employee=e, leave_type=LeaveType.objects.get(code=code), start_date=d,
                                   end_date=d, half_day=half, reason=f'{TAG} sample leave')
            svc.save_application(app)
            svc.approve(app)
        s = svc.month_summary(e, year, month)
        self.stdout.write(self.style.SUCCESS(
            f'{e.employee_id} {year}-{month:02d}: working {s.working_days}, present {s.present}, half days '
            f'{s.half_days}, paid leave {s.paid_leave}, LWP {s.lwp}, absent {s.absent}, holidays {s.holidays}, '
            f'week offs {s.week_offs}, payable {s.payable_days}'))
