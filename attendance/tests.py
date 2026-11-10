from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from employee_management import payroll as engine
from employee_management.models import (Department, Employee, EmployeePayroll, EmployeeSalaryStructure,
                                        PayrollPeriod, SalaryComponent)

from . import services as svc
from .models import (AttendanceRecord as AR, AttendanceSettings, Holiday, LeaveApplication, LeaveBalance,
                     LeaveType)


def D(x):
    return Decimal(str(x))


class Base(TestCase):
    """November 2026: 30 days, Sundays 1/8/15/22/29 (5 week offs) -> 25 working days."""

    def setUp(self):
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.emp = Employee.objects.create(first_name='Asha', last_name='Rao', department=self.dept,
                                           designation='Engineer', date_of_joining=date(2025, 1, 1),
                                           status=Employee.CONFIRMED)
        self.cl = LeaveType.objects.get(code='CL')
        self.lwp = LeaveType.objects.get(code='LWP')

    def mark(self, day, status, lt=None, m=11, y=2026, emp=None):
        return AR.objects.create(employee=emp or self.emp, date=date(y, m, day), status=status, leave_type=lt)

    def apply(self, a, b=None, lt=None, half=False, emp=None):
        app = LeaveApplication(employee=emp or self.emp, leave_type=lt or self.cl, start_date=a,
                               end_date=b or a, half_day=half)
        return svc.save_application(app)

    def structure(self):
        st = EmployeeSalaryStructure.objects.create(employee=self.emp, effective_from=date(2025, 1, 1))
        SalaryComponent.objects.create(structure=st, kind='earning', code='basic', amount=D('30000'))
        SalaryComponent.objects.create(structure=st, kind='earning', code='hra', amount=D('12000'))
        return st


class SeedTests(Base):
    def test_default_leave_types_seeded(self):
        self.assertEqual(set(LeaveType.objects.values_list('code', flat=True)), {'CL', 'SL', 'EL', 'LWP', 'CO'})
        self.assertFalse(self.lwp.paid)
        self.assertEqual(AttendanceSettings.current().weekly_off_days, '6')


class SummaryTests(Base):
    def test_status_counts(self):
        self.mark(2, AR.PRESENT)
        self.mark(3, AR.ABSENT)
        self.mark(4, AR.LEAVE, self.cl)
        self.mark(5, AR.LEAVE, self.lwp)
        s = svc.month_summary(self.emp, 2026, 11)
        self.assertEqual(s.week_offs, 5)
        self.assertEqual(s.working_days, 25)
        self.assertEqual(s.absent, 1)
        self.assertEqual(s.paid_leave, 1)
        self.assertEqual(s.lwp, 1)
        self.assertEqual(s.present, 22)  # 1 marked + 21 unmarked working days
        self.assertEqual(s.unmarked, 21)
        self.assertEqual(s.payable_days, 28)
        self.assertTrue(s.has_data)

    def test_half_days(self):
        self.mark(2, AR.HALF_DAY)               # half present, half absent
        self.mark(3, AR.HALF_DAY, self.cl)      # half present, half paid leave
        self.mark(4, AR.HALF_DAY, self.lwp)     # half present, half LWP
        s = svc.month_summary(self.emp, 2026, 11)
        self.assertEqual(s.half_days, 3)
        self.assertEqual(s.absent, D('0.5'))
        self.assertEqual(s.paid_leave, D('0.5'))
        self.assertEqual(s.lwp, D('0.5'))
        self.assertEqual(s.payable_days, 29)

    def test_holidays_and_week_offs(self):
        Holiday.objects.create(date=date(2026, 11, 10), name='Festival')
        st = AttendanceSettings.current()
        st.weekly_off_days, st.off_saturdays = '6', '2,4'
        st.save()
        self.mark(2, AR.PRESENT)
        s = svc.month_summary(self.emp, 2026, 11)
        self.assertEqual(s.holidays, 1)
        self.assertEqual(s.week_offs, 7)  # 5 Sundays + 2nd/4th Saturday (14th, 28th)
        self.assertEqual(s.working_days, 22)
        self.assertTrue(st.is_week_off(date(2026, 11, 14)))
        self.assertFalse(st.is_week_off(date(2026, 11, 7)))
        st.weekly_off_days, st.off_saturdays = '5,6', ''
        st.save()
        self.assertEqual(svc.month_summary(self.emp, 2026, 11).week_offs, 9)

    def test_joining_mid_month(self):
        self.emp.date_of_joining = date(2026, 11, 16)
        self.emp.save()
        self.mark(16, AR.PRESENT)
        s = svc.month_summary(self.emp, 2026, 11)
        self.assertEqual(s.days_in_service, 15)

    def test_leave_status_requires_type(self):
        r = AR(employee=self.emp, date=date(2026, 11, 2), status=AR.LEAVE)
        with self.assertRaises(ValidationError):
            r.full_clean()


class LeaveTests(Base):
    def test_approval_updates_attendance_and_balance(self):
        app = self.apply(date(2026, 11, 6), date(2026, 11, 9))  # Fri, Sat, Sun(off), Mon -> 3 days
        self.assertEqual(app.days, 3)
        svc.approve(app)
        self.assertEqual(AR.objects.filter(application=app, status=AR.LEAVE).count(), 3)
        self.assertFalse(AR.objects.filter(date=date(2026, 11, 8)).exists())
        b = LeaveBalance.objects.get(employee=self.emp, leave_type=self.cl, year=2026)
        self.assertEqual(b.used, 3)
        self.assertEqual(b.balance, b.accrued - 3)
        self.assertEqual(svc.month_summary(self.emp, 2026, 11).paid_leave, 3)
        svc.cancel(app)
        b.refresh_from_db()
        self.assertEqual(b.used, 0)
        self.assertFalse(AR.objects.filter(application=app).exists())

    def test_half_day_leave(self):
        app = svc.approve(self.apply(date(2026, 11, 2), half=True))
        self.assertEqual(app.days, D('0.5'))
        self.assertEqual(AR.objects.get(date=date(2026, 11, 2)).status, AR.HALF_DAY)
        self.assertEqual(LeaveBalance.objects.get(employee=self.emp, leave_type=self.cl, year=2026).used, D('0.5'))
        bad = LeaveApplication(employee=self.emp, leave_type=self.cl, start_date=date(2026, 11, 2),
                               end_date=date(2026, 11, 3), half_day=True)
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_overlap_blocked(self):
        svc.approve(self.apply(date(2026, 11, 2), date(2026, 11, 4)))
        with self.assertRaises(svc.LeaveError):
            self.apply(date(2026, 11, 4), date(2026, 11, 5))
        with self.assertRaises(svc.LeaveError):
            self.apply(date(2026, 11, 3), lt=self.lwp)
        self.apply(date(2026, 11, 5))  # adjacent is fine

    def test_insufficient_balance_blocked_except_lwp(self):
        b = LeaveBalance.for_year(self.emp, self.cl, 2026)
        b.accrued, b.opening = D('1'), D('0')
        b.save()
        with self.assertRaises(svc.LeaveError):
            self.apply(date(2026, 11, 2), date(2026, 11, 3))
        app = self.apply(date(2026, 11, 2), date(2026, 11, 6), lt=self.lwp)
        svc.approve(app)
        self.assertEqual(svc.month_summary(self.emp, 2026, 11).lwp, 5)

    def test_reject(self):
        app = self.apply(date(2026, 11, 2))
        svc.reject(app)
        self.assertEqual(app.status, LeaveApplication.REJECTED)
        self.assertFalse(AR.objects.exists())
        with self.assertRaises(svc.LeaveError):
            svc.approve(app)

    def test_carry_forward(self):
        el = LeaveType.objects.get(code='EL')
        el.carry_forward, el.carry_forward_cap = True, D('5')
        el.save()
        self.cl.carry_forward = False
        self.cl.save()
        b = LeaveBalance.for_year(self.emp, el, 2026)
        b.accrued, b.used = D('15'), D('2')
        b.save()
        LeaveBalance.for_year(self.emp, self.cl, 2026)
        svc.carry_forward(2026)
        nxt = LeaveBalance.objects.get(employee=self.emp, leave_type=el, year=2027)
        self.assertEqual(nxt.opening, 5)  # capped
        self.assertEqual(LeaveBalance.objects.get(employee=self.emp, leave_type=self.cl, year=2027).opening, 0)
        el.carry_forward_cap = None
        el.save()
        svc.carry_forward(2026)  # idempotent re-run recomputes
        nxt.refresh_from_db()
        self.assertEqual(nxt.opening, 13)


class CsvTests(Base):
    def test_template_and_import(self):
        tpl = svc.csv_template(2026, 11)
        text = tpl.decode('utf-8-sig') if isinstance(tpl, bytes) else tpl
        self.assertIn('employee_id,date,status', text)
        self.assertIn(self.emp.employee_id, text)
        data = ('employee_id,date,status,leave_type,in_time,out_time,note\n'
                f'{self.emp.employee_id},2026-11-02,P,,09:30,18:00,\n'
                f'{self.emp.employee_id},03-11-2026,A,,,,sick\n'
                f'{self.emp.employee_id},2026-11-04,L,CL,,,\n'
                f'{self.emp.employee_id},2026-11-05,,,,,\n').encode()
        n, errors = svc.import_csv(data, 2026, 11)
        self.assertEqual(errors, [])
        self.assertEqual(n, 3)
        self.assertEqual(AR.objects.get(date=date(2026, 11, 3)).status, AR.ABSENT)
        self.assertEqual(AR.objects.get(date=date(2026, 11, 2)).in_time.hour, 9)

    def test_import_all_or_nothing(self):
        data = ('employee_id,date,status,leave_type,in_time,out_time,note\n'
                f'{self.emp.employee_id},2026-11-02,P,,,,\n'
                'EMP999,2026-11-03,P,,,,\n'
                f'{self.emp.employee_id},2026-11-04,XX,,,,\n'
                f'{self.emp.employee_id},2026-12-04,P,,,,\n').encode()
        n, errors = svc.import_csv(data, 2026, 11)
        self.assertEqual(n, 0)
        self.assertGreaterEqual(len(errors), 3)
        self.assertFalse(AR.objects.exists())

    def test_import_view(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('a.csv', f'employee_id,date,status\n{self.emp.employee_id},2026-11-02,P\n'.encode())
        r = self.client.post(reverse('att:import'), {'year': 2026, 'month': 11, 'file': f})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AR.objects.filter(date=date(2026, 11, 2)).exists())
        r = self.client.get(reverse('att:template') + '?year=2026&month=11')
        self.assertEqual(r.status_code, 200)


class PayrollIntegrationTests(Base):
    def setUp(self):
        super().setUp()
        self.structure()
        self.period = PayrollPeriod.objects.create(year=2026, month=11, divisor='actual')

    def gen(self):
        engine.generate(self.period)
        return EmployeePayroll.objects.get(period=self.period, employee=self.emp, is_current=True)

    def test_no_data_falls_back_to_manual(self):
        p = self.gen()
        self.assertEqual(p.input_source, 'manual')
        self.assertEqual(p.attendance_snapshot, '')
        self.assertEqual(p.lwp_days or 0, 0)
        self.assertEqual(p.payable_days, 30)
        self.assertEqual(p.gross_earnings, D('42000'))

    def test_prefill_from_attendance(self):
        self.mark(3, AR.ABSENT)
        svc.approve(self.apply(date(2026, 11, 4), lt=self.lwp))
        svc.approve(self.apply(date(2026, 11, 5)))
        p = self.gen()
        self.assertEqual(p.input_source, 'attendance')
        self.assertIn('"payable_days": "28.00"', p.attendance_snapshot)
        self.assertEqual(p.lwp_days, 1)
        self.assertEqual(p.unpaid_leave_days, 1)
        self.assertEqual(p.paid_leave_days, 1)
        self.assertEqual(p.payable_days, 28)
        self.assertEqual(p.gross_earnings, D('39200'))  # 42000 * 28 / 30
        r = self.client.get(reverse('em:payroll_detail', args=[p.pk]))
        self.assertContains(r, 'From attendance')

    def test_manual_override_keeps_source_visible(self):
        self.mark(3, AR.ABSENT)
        p = self.gen()
        data = {f: '' for f in EmployeePayroll.INPUT_FIELDS}
        data.update(lwp_days='2', unpaid_leave_days='1', paid_leave_days='0')
        r = self.client.post(reverse('em:payroll_edit', args=[p.pk]), data)
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        p.refresh_from_db()
        self.assertEqual(p.input_source, 'override')
        self.assertEqual(p.lwp_days, 2)
        self.assertIn('"absent": "1.00"', p.attendance_snapshot)
        self.assertContains(self.client.get(reverse('em:payroll_detail', args=[p.pk])), 'manually overridden')
        self.client.post(reverse('em:payroll_refresh_attendance', args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual((p.input_source, p.lwp_days), ('attendance', 0))

    def test_lock_after_finalize_and_revision_unlocks(self):
        self.mark(3, AR.ABSENT)
        p = self.gen()
        engine.finalize([p])
        p.refresh_from_db()
        self.assertTrue(svc.is_locked(self.emp, date(2026, 11, 10)))
        with self.assertRaises(ValidationError):
            self.mark(10, AR.ABSENT)
        with self.assertRaises(ValidationError):
            AR.objects.get(date=date(2026, 11, 3)).delete()
        with self.assertRaises(ValidationError):
            svc.approve(self.apply(date(2026, 11, 12)))
        self.assertFalse(svc.is_locked(self.emp, date(2026, 12, 1)))
        r = engine.start_revision(p, 'attendance correction')
        self.assertEqual(r.input_source, 'attendance')
        self.assertFalse(svc.is_locked(self.emp, date(2026, 11, 10)))
        x = AR(employee=self.emp, date=date(2026, 11, 10), status=AR.ABSENT)
        x.correction_reason = 'Stage 6: corrections in a payroll month need a reason'
        x.save()
        r2, _ = engine.refresh_from_attendance(r)
        self.assertEqual(r2.unpaid_leave_days, 2)

    def test_daily_screen_respects_lock(self):
        p = self.gen()
        engine.finalize([p])
        self.client.post(reverse('att:daily'), {'date': '2026-11-02', 'action': 'mark_all_present'})
        self.assertFalse(AR.objects.exists())


class ScreenTests(Base):
    def test_daily_mark_all_present(self):
        other = Employee.objects.create(first_name='B', last_name='K', department=self.dept, designation='X',
                                        date_of_joining=date(2025, 1, 1), status=Employee.CONFIRMED)
        r = self.client.post(reverse('att:daily'), {'date': '2026-11-02', f'status_{other.pk}': 'A',
                                                    'action': 'mark_all_present'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AR.objects.get(employee=self.emp).status, AR.PRESENT)
        self.assertEqual(AR.objects.get(employee=other).status, AR.ABSENT)

    def test_month_grid_save(self):
        url = reverse('att:month_grid', args=[self.emp.pk])
        self.assertEqual(self.client.get(url + '?year=2026&month=11').status_code, 200)
        self.client.post(url, {'year': 2026, 'month': 11, 'status_2026-11-02': 'HD', 'in_2026-11-02': '09:00'})
        self.assertEqual(AR.objects.get(date=date(2026, 11, 2)).status, AR.HALF_DAY)

    def test_balances_filters_leave_type_and_month(self):
        svc.approve(self.apply(date(2026, 11, 2), date(2026, 11, 3)))  # 2 CL days in November
        sl = LeaveType.objects.get(code='SL')
        LeaveBalance.for_year(self.emp, sl, 2026)
        self.mark(2, AR.LEAVE, sl, m=12)  # 1 SL day in December
        base = reverse('att:balances') + '?year=2026'
        r = self.client.get(base + f'&leave_type={self.cl.pk}')
        self.assertEqual([b.leave_type_id for b in r.context['rows']], [self.cl.pk])
        r = self.client.get(base + '&month=11')
        self.assertContains(r, 'Used in November')
        self.assertEqual([(b.leave_type_id, b.month_used) for b in r.context['rows']],
                         [(self.cl.pk, D('2'))])
        r = self.client.get(base + '&month=12')
        self.assertEqual([(b.leave_type_id, b.month_used) for b in r.context['rows']],
                         [(sl.pk, D('1'))])
        self.assertEqual(self.client.get(base + f'&month=11&leave_type={sl.pk}').context['rows'], [])
        r = self.client.get(reverse('att:balances_export') + f'?year=2026&month=11&leave_type={self.cl.pk}')
        self.assertEqual(r.content[:2], b'PK')

    def test_application_new_view_and_actions(self):
        r = self.client.post(reverse('att:application_new'), {
            'employee': self.emp.pk, 'leave_type': self.cl.pk, 'start_date': '2026-11-02',
            'end_date': '2026-11-02', 'reason': 'x'})
        self.assertEqual(r.status_code, 302)
        app = LeaveApplication.objects.get()
        self.assertEqual(app.status, 'pending')
        self.client.post(reverse('att:application_action', args=[app.pk, 'approve']))
        app.refresh_from_db()
        self.assertEqual(app.status, 'approved')

    def test_pages_render(self):
        svc.approve(self.apply(date(2026, 11, 2)))
        for name in ('daily', 'summary', 'import', 'holidays', 'holiday_new', 'weekly_off', 'leave_types',
                     'leave_type_new', 'applications', 'application_new', 'balances', 'reports'):
            self.assertEqual(self.client.get(reverse('att:' + name)).status_code, 200, name)
        for q in ('?report=register&year=2026&month=11', '?report=balances&year=2026',
                  f'?report=history&employee={self.emp.pk}', f'?report=history&employee={self.emp.pk}&export=xlsx'):
            self.assertEqual(self.client.get(reverse('att:reports') + q).status_code, 200, q)
        r = self.client.get(reverse('att:register_export', args=['csv']) + '?year=2026&month=11')
        self.assertIn(self.emp.employee_id, r.content.decode('utf-8-sig'))
        r = self.client.get(reverse('att:register_export', args=['xlsx']) + '?year=2026&month=11')
        self.assertEqual(r.content[:2], b'PK')
        self.assertEqual(self.client.get(reverse('att:balances_export')).content[:2], b'PK')
        self.client.post(reverse('att:carry_forward'), {'year': 2026})
        self.assertTrue(LeaveBalance.objects.filter(year=2027).exists())

    def test_non_local_blocked(self):
        r = self.client.get(reverse('att:daily'), REMOTE_ADDR='10.0.0.5')
        self.assertIn(r.status_code, (403, 404))


class NavTests(TestCase):
    def test_nav_and_home_card(self):
        r = self.client.get(reverse('home'))
        self.assertContains(r, 'Attendance &amp; Leave')
        for name in ('daily', 'holidays', 'leave_types', 'applications', 'balances', 'reports'):
            self.assertContains(r, reverse('att:' + name))
        for label in ('Leave Types', 'Leave Applications', 'Leave Balances', 'Holidays'):
            self.assertContains(r, label)
        self.assertContains(self.client.get(reverse('dashboard')), reverse('att:daily'))
