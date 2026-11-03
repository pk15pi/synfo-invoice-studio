import calendar
from datetime import date, datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from employee_management.exports import generic_xlsx
from employee_management.models import Employee

from . import services as svc
from .forms import (AttendanceSettingsForm, HolidayForm, ImportForm, LeaveApplicationForm, LeaveBalanceForm,
                    LeaveTypeForm)
from .models import (AttendanceRecord, AttendanceSettings, Holiday, LeaveApplication, LeaveBalance, LeaveType,
                     daterange, month_bounds)

T = 'attendance/'
FORM = 'invoice_app/form_page.html'
MONTHS = [(i, calendar.month_name[i]) for i in range(1, 13)]


def _ym(request):
    t = timezone.localdate()
    try:
        y, m = int(request.GET.get('year', t.year)), int(request.GET.get('month', t.month))
        date(y, m, 1)
        return y, m
    except (TypeError, ValueError):
        return t.year, t.month


def _years():
    t = timezone.localdate().year
    return range(t - 3, t + 2)


def _time(v):
    v = (v or '').strip()
    return datetime.strptime(v[:5], '%H:%M').time() if v else None


def _apply(emp, d, status, lt_id, tin, tout, note=None, reason=None):
    """Save one day from a grid row. Blank status clears a manual mark. Returns error text or None."""
    try:
        r = AttendanceRecord.objects.filter(employee=emp, date=d).first()
        if not status:
            if r and not r.application_id:
                r.correction_reason = reason
                r.delete()
            return None
        if r and r.application_id and (status != r.status or str(r.leave_type_id or '') != (lt_id or '')):
            return f'{emp.employee_id} {d:%d-%m}: linked to leave application #{r.application_id}; cancel it instead.'
        r = r or AttendanceRecord(employee=emp, date=d)
        r.status, r.leave_type_id = status, int(lt_id) if lt_id else None
        r.in_time, r.out_time = _time(tin), _time(tout)
        if note is not None:
            r.note = note[:160]
        r.full_clean(exclude=['employee', 'application'])
        r.correction_reason = reason
        r.save()
    except ValidationError as ex:
        return f'{emp.employee_id} {d:%d-%m}: ' + '; '.join(ex.messages)
    except ValueError:
        return f'{emp.employee_id} {d:%d-%m}: invalid time.'
    return None


def _report_errors(request, errors, ok_msg):
    for e in errors[:15]:
        messages.error(request, e)
    if not errors:
        messages.success(request, ok_msg)


# ------------------------------------------------------------------ daily bulk screen
def daily(request):
    try:
        d = date.fromisoformat(request.GET.get('date') or request.POST.get('date') or '')
    except ValueError:
        d = timezone.localdate()
    settings = AttendanceSettings.current()
    holidays = svc.holiday_map(d, d)
    emps = [e for e in svc.employees_in_month(d.year, d.month).select_related('department')
            if e.date_of_joining <= d and (not e.date_of_leaving or e.date_of_leaving >= d)]
    if request.method == 'POST':
        errors = []
        mark_all = request.POST.get('action') == 'mark_all_present'
        for e in emps:
            st = request.POST.get(f'status_{e.pk}', '')
            if mark_all and not st:
                st = AttendanceRecord.PRESENT
            err = _apply(e, d, st, request.POST.get(f'leave_{e.pk}', ''), request.POST.get(f'in_{e.pk}'),
                         request.POST.get(f'out_{e.pk}'), reason=request.POST.get('reason'))
            if err:
                errors.append(err)
        _report_errors(request, errors, f'Attendance saved for {d:%d-%m-%Y}.')
        return redirect(f"{reverse('att:daily')}?date={d.isoformat()}")
    recs = {r.employee_id: r for r in AttendanceRecord.objects.filter(date=d)}
    rows = [{'e': e, 'r': recs.get(e.pk), 'locked': svc.is_locked(e, d)} for e in emps]
    needs_reason = any(svc.needs_reason(x['e'], d) and not x['locked'] for x in rows)
    return render(request, T + 'daily.html', {
        'd': d, 'rows': rows, 'needs_reason': needs_reason, 'statuses': AttendanceRecord.STATUSES, 'leave_types': LeaveType.objects.all(),
        'default': svc.default_status(d, holidays, settings), 'holiday': holidays.get(d),
        'prev': d.fromordinal(d.toordinal() - 1).isoformat(), 'next': d.fromordinal(d.toordinal() + 1).isoformat()})


# ------------------------------------------------------------------ month grid
def month_grid(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    y, m = _ym(request) if request.method == 'GET' else (int(request.POST['year']), int(request.POST['month']))
    start, end = month_bounds(y, m)
    a, b = svc.service_range(e, start, end)
    if request.method == 'POST':
        errors = []
        mark_all = request.POST.get('action') == 'mark_all_present'
        settings, hol = AttendanceSettings.current(), svc.holiday_map(start, end)
        for d in daterange(a, b):
            k = d.isoformat()
            st = request.POST.get(f'status_{k}', '')
            if mark_all and not st:
                st = svc.default_status(d, hol, settings) or AttendanceRecord.PRESENT
            err = _apply(e, d, st, request.POST.get(f'leave_{k}', ''), request.POST.get(f'in_{k}'),
                         request.POST.get(f'out_{k}'), reason=request.POST.get('reason'))
            if err:
                errors.append(err)
        _report_errors(request, errors, f'Attendance saved for {e.employee_id}, {calendar.month_name[m]} {y}.')
        return redirect(f"{reverse('att:month_grid', args=[e.pk])}?year={y}&month={m}")
    settings, hol = AttendanceSettings.current(), svc.holiday_map(start, end)
    recs = {r.date: r for r in e.attendance.filter(date__range=(start, end)).select_related('leave_type')}
    days = [{'d': d, 'k': d.isoformat(), 'r': recs.get(d), 'default': svc.default_status(d, hol, settings),
             'holiday': hol.get(d)} for d in daterange(a, b)]
    return render(request, T + 'month_grid.html', {
        'e': e, 'y': y, 'm': m, 'days': days, 'months': MONTHS, 'years': _years(),
        'statuses': AttendanceRecord.STATUSES, 'leave_types': LeaveType.objects.all(),
        'summary': svc.month_summary(e, y, m, settings, hol, recs), 'locked': svc.is_locked(e, start),
        'needs_reason': svc.needs_reason(e, start),
        'employees': Employee.objects.order_by('employee_id'), 'label': f'{calendar.month_name[m]} {y}'})


def summary(request):
    y, m = _ym(request)
    settings = AttendanceSettings.current()
    start, end = month_bounds(y, m)
    hol = svc.holiday_map(start, end)
    rows = [(e, svc.month_summary(e, y, m, settings, hol)) for e in svc.employees_in_month(y, m)]
    return render(request, T + 'summary.html', {'rows': rows, 'y': y, 'm': m, 'months': MONTHS, 'years': _years(),
                                                'label': f'{calendar.month_name[m]} {y}'})


# ------------------------------------------------------------------ CSV
def template_csv(request):
    y, m = _ym(request)
    resp = HttpResponse(svc.csv_template(y, m), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="Attendance_Template_{calendar.month_name[m]}_{y}.csv"'
    return resp


def import_view(request):
    y, m = _ym(request)
    form = ImportForm(request.POST or None, request.FILES or None, initial={'year': y, 'month': m})
    errors = []
    if request.method == 'POST' and form.is_valid():
        f = form.cleaned_data['file']
        if f.size > 5 * 1024 * 1024:
            errors = ['File is larger than 5 MB.']
        else:
            n, errors = svc.import_csv(f.read(), form.cleaned_data['year'], form.cleaned_data['month'],
                                       reason=form.cleaned_data.get('reason') or '')
            if not errors:
                messages.success(request, f'{n} attendance row(s) imported.')
                return redirect(f"{reverse('att:summary')}?year={form.cleaned_data['year']}"
                                f"&month={form.cleaned_data['month']}")
    return render(request, T + 'import.html', {'form': form, 'errors': errors, 'y': y, 'm': m})


# ------------------------------------------------------------------ holidays & weekly off
def holidays(request):
    y = int(request.GET.get('year') or timezone.localdate().year)
    return render(request, T + 'holidays.html', {
        'y': y, 'years': _years(), 'rows': Holiday.objects.filter(date__year=y),
        'settings': AttendanceSettings.current()})


def holiday_form(request, pk=None):
    obj = get_object_or_404(Holiday, pk=pk) if pk else None
    form = HolidayForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        h = form.save()
        messages.success(request, f'Holiday {h} saved.')
        return redirect(f"{reverse('att:holidays')}?year={h.date.year}")
    return render(request, FORM, {'form': form, 'title': 'Holiday', 'back_url': reverse('att:holidays')})


@require_POST
def holiday_delete(request, pk):
    h = get_object_or_404(Holiday, pk=pk)
    y = h.date.year
    h.delete()
    messages.success(request, 'Holiday removed.')
    return redirect(f"{reverse('att:holidays')}?year={y}")


def weekly_off(request):
    form = AttendanceSettingsForm(request.POST or None, instance=AttendanceSettings.current())
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Weekly off updated.')
        return redirect('att:holidays')
    return render(request, FORM, {'form': form, 'title': 'Weekly off', 'back_url': reverse('att:holidays')})


# ------------------------------------------------------------------ leave types
def leave_types(request):
    return render(request, T + 'leave_types.html', {'rows': LeaveType.objects.all()})


def leave_type_form(request, pk=None):
    obj = get_object_or_404(LeaveType, pk=pk) if pk else None
    form = LeaveTypeForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Leave type saved.')
        return redirect('att:leave_types')
    return render(request, FORM, {'form': form, 'title': 'Leave type', 'back_url': reverse('att:leave_types')})


# ------------------------------------------------------------------ leave applications
def applications(request):
    qs = LeaveApplication.objects.select_related('employee', 'leave_type')
    st = request.GET.get('status', '')
    if st:
        qs = qs.filter(status=st)
    return render(request, T + 'applications.html', {'rows': qs[:500], 'status': st,
                                                     'statuses': LeaveApplication.STATUSES})


def application_new(request):
    form = LeaveApplicationForm(request.POST or None, initial={'employee': request.GET.get('employee')})
    if request.method == 'POST' and form.is_valid():
        app = form.save(commit=False)
        try:
            svc.save_application(app)
            if request.POST.get('approve_now'):
                svc.approve(app)
                messages.success(request, f'Leave recorded and approved ({app.days} day(s)).')
            else:
                messages.success(request, f'Leave application saved as pending ({app.days} day(s)).')
            return redirect('att:applications')
        except (svc.LeaveError, ValidationError) as ex:
            form.add_error(None, '; '.join(getattr(ex, 'messages', [str(ex)])))
    return render(request, T + 'application_form.html', {'form': form})


@require_POST
def application_action(request, pk, action):
    app = get_object_or_404(LeaveApplication, pk=pk)
    fn = {'approve': svc.approve, 'reject': svc.reject, 'cancel': svc.cancel}.get(action)
    if fn is None:
        messages.error(request, 'Unknown action.')
    else:
        try:
            fn(app)
            messages.success(request, f'Application #{app.pk}: {action} done.')
        except (svc.LeaveError, ValidationError) as ex:
            messages.error(request, '; '.join(getattr(ex, 'messages', [str(ex)])))
    return redirect(request.POST.get('next') or 'att:applications')


# ------------------------------------------------------------------ balances
def balances(request):
    y = int(request.GET.get('year') or timezone.localdate().year)
    if request.GET.get('init'):
        n = svc.ensure_balances(y)
        messages.success(request, f'{n} balance row(s) checked for {y}.')
        return redirect(f"{reverse('att:balances')}?year={y}")
    rows = LeaveBalance.objects.filter(year=y).select_related('employee', 'leave_type')
    return render(request, T + 'balances.html', {'rows': rows, 'y': y, 'years': _years()})


def balance_edit(request, pk):
    b = get_object_or_404(LeaveBalance, pk=pk)
    form = LeaveBalanceForm(request.POST or None, instance=b)
    back = f"{reverse('att:balances')}?year={b.year}"
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Balance updated.')
        return redirect(back)
    return render(request, FORM, {'form': form, 'back_url': back,
                                  'title': f'{b.employee.employee_id} {b.leave_type.name} {b.year} (used {b.used})'})


@require_POST
def carry_forward_view(request):
    y = int(request.POST['year'])
    n = svc.carry_forward(y)
    messages.success(request, f'Carried forward {n} balance(s) from {y} into {y + 1}.')
    return redirect(f"{reverse('att:balances')}?year={y + 1}")


# ------------------------------------------------------------------ reports
def _balance_rows(y):
    return [[b.employee.employee_id, b.employee.full_name, b.leave_type.name, b.opening, b.accrued, b.used,
             b.balance] for b in LeaveBalance.objects.filter(year=y).select_related('employee', 'leave_type')]


def reports(request):
    y, m = _ym(request)
    kind = request.GET.get('report', 'register')
    headers, rows = [], []
    eid = request.GET.get('employee', '')
    if kind == 'register':
        headers, rows = svc.register(y, m)
    elif kind == 'balances':
        headers = ['Employee ID', 'Name', 'Leave type', 'Opening', 'Accrued', 'Used', 'Balance']
        rows = _balance_rows(y)
    elif kind == 'history' and eid:
        headers = ['From', 'To', 'Type', 'Days', 'Half day', 'Status', 'Reason']
        rows = [[a.start_date, a.end_date, a.leave_type.name, a.days, 'Yes' if a.half_day else '',
                 a.get_status_display(), a.reason]
                for a in LeaveApplication.objects.filter(employee_id=eid).select_related('leave_type')]
    if request.GET.get('export') == 'xlsx' and headers:
        resp = HttpResponse(generic_xlsx(kind.title(), headers, rows),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="Attendance_{kind}_{y}_{m:02d}.xlsx"'
        return resp
    return render(request, T + 'reports.html', {
        'kind': kind, 'headers': headers, 'rows': rows, 'y': y, 'm': m, 'months': MONTHS, 'years': _years(),
        'employees': Employee.objects.order_by('employee_id'), 'eid': eid})


def register_export(request, fmt):
    import csv
    import io
    y, m = _ym(request)
    headers, rows = svc.register(y, m)
    name = f'Attendance_Register_{calendar.month_name[m]}_{y}'
    if fmt == 'csv':
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(headers)
        w.writerows([[str(c) for c in r] for r in rows])
        resp = HttpResponse(out.getvalue().encode('utf-8-sig'), content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="{name}.csv"'
        return resp
    rows = [[float(c) if hasattr(c, 'as_tuple') else c for c in r] for r in rows]
    resp = HttpResponse(generic_xlsx(f'Register {calendar.month_abbr[m]} {y}', headers, rows),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{name}.xlsx"'
    return resp


def balances_export(request):
    y = int(request.GET.get('year') or timezone.localdate().year)
    rows = [[float(c) if hasattr(c, 'as_tuple') else c for c in r] for r in _balance_rows(y)]
    resp = HttpResponse(generic_xlsx(f'Leave balances {y}', ['Employee ID', 'Name', 'Leave type', 'Opening',
                                                             'Accrued', 'Used', 'Balance'], rows),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="Leave_Balances_{y}.xlsx"'
    return resp


# ------------------------------------------------------------------ correction history (Stage 6)
def corrections(request):
    from .models import AttendanceCorrection
    qs = AttendanceCorrection.objects.select_related('employee')
    emp = request.GET.get('employee') or ''
    if emp.isdigit():
        qs = qs.filter(employee_id=int(emp))
    y, m = request.GET.get('year') or '', request.GET.get('month') or ''
    if y.isdigit():
        qs = qs.filter(date__year=int(y))
        if m.isdigit():
            qs = qs.filter(date__month=int(m))
    return render(request, T + 'corrections.html', {
        'rows': qs[:500], 'employees': Employee.objects.order_by('employee_id'), 'emp': emp, 'y': y, 'm': m,
        'months': MONTHS, 'years': _years()})
