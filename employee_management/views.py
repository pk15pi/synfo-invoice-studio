import json
import calendar
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payroll as engine
from .exports import (bank_csv, bank_xlsx, generic_xlsx, salary_register_csv, salary_register_xlsx)
from .forms import (BankAccountForm, DepartmentForm, DocumentForm, EmergencyContactForm, EmployeeForm,
                    PayrollInputForm, PayrollSettingsForm, PeriodForm, RevisionForm, SalaryStructureForm,
                    StatutoryRuleForm)
from .models import (DOCUMENT_TYPE_LABELS, DOCUMENT_TYPES, KEY_DOCUMENTS, ZERO, Department, EmergencyContact,
                     Employee, EmployeeBankAccount, EmployeeDocument, EmployeePayroll, EmployeeSalaryStructure,
                     FinalizedRecordError, PayrollPeriod, PayrollSettings, StatutoryRule)
from .pdf.payslip import payslip_filename, payslip_for
from .security import private_file_response

FORM = 'invoice_app/form_page.html'
T = 'employee_management/'


def _form(request, form, title, back_url):
    return render(request, FORM, {'form': form, 'title': title, 'back_url': back_url})


# ------------------------------------------------------------------ home
def hr_dashboard(request):
    s = PayrollSettings.current()
    today = timezone.localdate()
    soon = today + timedelta(days=s.expiry_alert_days)
    y, m = s.default_month()
    period = PayrollPeriod.objects.filter(year=y, month=m).first()
    ctx = {
        'counts': {k: Employee.objects.filter(status=k).count() for k, _ in Employee.STATUSES},
        'total_employed': Employee.objects.filter(status__in=Employee.EMPLOYED).count(),
        'departments': Department.objects.filter(active=True).count(),
        'expiring': EmployeeDocument.objects.filter(is_current=True, expiry_date__isnull=False,
                                                    expiry_date__lte=soon).select_related('employee')
        .order_by('expiry_date')[:20],
        'today': today,
        'missing_bank': Employee.objects.filter(status__in=Employee.EMPLOYED).exclude(
            bank_accounts__is_current=True).count(),
        'missing_salary': [e for e in Employee.objects.filter(status__in=Employee.EMPLOYED)
                           if not e.salary_structures.exists()][:20],
        'period': period, 'y': y, 'm': m, 'month_label': f'{calendar.month_name[m]} {y}',
    }
    return render(request, T + 'hr_dashboard.html', ctx)


# ------------------------------------------------------------------ departments
def departments(request):
    rows = Department.objects.annotate(n=Count('employees')).select_related('head')
    return render(request, T + 'departments.html', {'rows': rows})


def department_form(request, pk=None):
    obj = get_object_or_404(Department, pk=pk) if pk else None
    form = DepartmentForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Department saved.')
        return redirect('em:departments')
    return _form(request, form, 'Edit department' if obj else 'New department', reverse('em:departments'))


@require_POST
def department_toggle(request, pk):
    d = get_object_or_404(Department, pk=pk)
    d.active = not d.active
    d.save(update_fields=['active'])
    messages.success(request, f'{d.name} is now {"active" if d.active else "inactive"}.')
    return redirect('em:departments')


@require_POST
def department_delete(request, pk):
    d = get_object_or_404(Department, pk=pk)
    if d.employees.exists():
        messages.error(request, f'Cannot delete {d.name}: employees are linked to it. Deactivate it instead.')
        return redirect('em:departments')
    try:
        d.delete()
        messages.success(request, 'Department deleted.')
    except ProtectedError:
        messages.error(request, 'Department is in use and cannot be deleted.')
    return redirect('em:departments')


# ------------------------------------------------------------------ employees
def employee_list(request):
    qs = Employee.objects.select_related('department').order_by('employee_id')
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'employed')
    dept = request.GET.get('department', '')
    if q:
        qs = qs.filter(Q(employee_id__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) |
                       Q(designation__icontains=q) | Q(mobile__icontains=q) | Q(work_email__icontains=q))
    if status == 'employed':
        qs = qs.filter(status__in=Employee.EMPLOYED)
    elif status and status != 'all':
        qs = qs.filter(status=status)
    if dept:
        qs = qs.filter(department_id=dept)
    return render(request, T + 'employee_list.html', {
        'rows': qs, 'q': q, 'status': status, 'dept': dept, 'statuses': Employee.STATUSES,
        'departments': Department.objects.all()})


def employee_form(request, pk=None):
    obj = get_object_or_404(Employee, pk=pk) if pk else None
    if not obj and not Department.objects.filter(active=True).exists():
        messages.error(request, 'Create an active department first.')
        return redirect('em:department_new')
    form = EmployeeForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        emp = form.save()
        messages.success(request, f'Employee {emp.employee_id} saved.')
        return redirect('em:employee_detail', emp.pk)
    back = reverse('em:employee_detail', args=[obj.pk]) if obj else reverse('em:employees')
    return render(request, T + 'employee_form.html', {'form': form, 'obj': obj, 'back_url': back})


def employee_detail(request, pk):
    e = get_object_or_404(Employee.objects.select_related('department', 'reporting_manager'), pk=pk)
    s = PayrollSettings.current()
    docs = list(e.documents.filter(is_current=True))
    have = {d.document_type for d in docs}
    tab = request.GET.get('tab', 'overview')
    return render(request, T + 'employee_detail.html', {
        'e': e, 'tab': tab, 'docs': docs, 'alert_days': s.expiry_alert_days,
        'doc_status': [(DOCUMENT_TYPE_LABELS[k], k in have) for k in KEY_DOCUMENTS],
        'doc_states': {d.pk: d.expiry_state(s.expiry_alert_days) for d in docs},
        'banks': e.bank_accounts.all(), 'structures': e.salary_structures.prefetch_related('components'),
        'payrolls': e.payrolls.select_related('period').order_by('-period__year', '-period__month', '-revision'),
        'contacts': e.emergency_contacts.all(), 'log': e.change_log.all()[:50],
    })


def employee_photo(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    if not e.photo:
        raise Http404
    ext = e.photo.name.rsplit('.', 1)[-1]
    return private_file_response(e.photo, 'image/png' if ext == 'png' else 'image/jpeg', f'{e.employee_id}.{ext}')


# emergency contacts
def contact_form(request, pk, cid=None):
    e = get_object_or_404(Employee, pk=pk)
    obj = get_object_or_404(EmergencyContact, pk=cid, employee=e) if cid else None
    form = EmergencyContactForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        c = form.save(commit=False)
        c.employee = e
        c.save()
        messages.success(request, 'Emergency contact saved.')
        return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=contacts')
    return _form(request, form, f'Emergency contact - {e.full_name}',
                 reverse('em:employee_detail', args=[pk]) + '?tab=contacts')


@require_POST
def contact_delete(request, pk, cid):
    get_object_or_404(EmergencyContact, pk=cid, employee_id=pk).delete()
    messages.success(request, 'Emergency contact removed.')
    return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=contacts')


# ------------------------------------------------------------------ documents
def document_upload(request, pk, replace_id=None):
    e = get_object_or_404(Employee, pk=pk)
    old = get_object_or_404(EmployeeDocument, pk=replace_id, employee=e, is_current=True) if replace_id else None
    initial = {}
    if old:
        initial = {f: getattr(old, f) for f in ('document_type', 'document_name', 'document_number', 'issue_date',
                                                 'expiry_date')}
    form = DocumentForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save_for(e, replaces=old)
        messages.success(request, 'Document replaced (previous version kept in history).' if old
                         else 'Document uploaded.')
        return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=documents')
    title = f'Replace document - {e.full_name}' if old else f'Upload document - {e.full_name}'
    return _form(request, form, title, reverse('em:employee_detail', args=[pk]) + '?tab=documents')


def _doc(pk, did):
    return get_object_or_404(EmployeeDocument, pk=did, employee_id=pk)


def document_view(request, pk, did):
    d = _doc(pk, did)
    return private_file_response(d.file, d.content_type, _doc_name(d), inline=True)


def document_download(request, pk, did):
    d = _doc(pk, did)
    return private_file_response(d.file, d.content_type, _doc_name(d), inline=False)


def _doc_name(d):
    ext = d.file.name.rsplit('.', 1)[-1]
    return f'{d.employee.employee_id}_{d.document_type}_{d.pk}.{ext}'


def document_history(request, pk, did):
    d = _doc(pk, did)
    return render(request, T + 'document_history.html', {'d': d, 'e': d.employee, 'history': d.history()})


def document_delete(request, pk, did):
    d = _doc(pk, did)
    if request.method == 'POST':
        storage, name = d.file.storage, d.file.name
        with transaction.atomic():
            prev = d.replaces
            d.delete()
            if prev is not None and not EmployeeDocument.objects.filter(
                    employee_id=pk, document_type=prev.document_type, is_current=True).exists():
                prev.is_current = True
                prev.save(update_fields=['is_current'])
        storage.delete(name)
        messages.success(request, 'Document deleted.')
        return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=documents')
    return render(request, T + 'confirm.html', {
        'title': 'Delete document?', 'text': f'{d} for {d.employee.full_name} will be permanently deleted. '
                                             'Older versions (if any) are kept.',
        'back_url': reverse('em:employee_detail', args=[pk]) + '?tab=documents'})


# ------------------------------------------------------------------ bank
def bank_add(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    form = BankAccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items() if k != 'confirm_account_number'}
        EmployeeBankAccount.add_for(e, **data)
        messages.success(request, 'Bank account saved. Previous account kept in history.')
        return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=bank')
    return _form(request, form, f'Bank account - {e.full_name}',
                 reverse('em:employee_detail', args=[pk]) + '?tab=bank')


# ------------------------------------------------------------------ salary
def salary_new(request, pk, from_id=None):
    e = get_object_or_404(Employee, pk=pk)
    src = get_object_or_404(EmployeeSalaryStructure, pk=from_id, employee=e) if from_id else (
        e.salary_structures.order_by('-effective_from').first())
    form = SalaryStructureForm(request.POST or None, employee=e, instance=src)
    if request.method == 'POST' and form.is_valid():
        form.save_structure()
        messages.success(request, 'Salary structure saved. The previous structure was closed, not overwritten.')
        return redirect(reverse('em:employee_detail', args=[pk]) + '?tab=salary')
    return render(request, T + 'salary_form.html', {'form': form, 'e': e,
                                                    'back_url': reverse('em:employee_detail', args=[pk]) + '?tab=salary'})


# ------------------------------------------------------------------ settings
def payroll_settings(request):
    s = PayrollSettings.current()
    form = PayrollSettingsForm(request.POST or None, request.FILES or None, instance=s)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Payroll settings saved.')
        return redirect('em:settings')
    return render(request, T + 'settings.html', {'form': form, 'rules': StatutoryRule.objects.all()})


def rule_form(request, pk=None):
    obj = get_object_or_404(StatutoryRule, pk=pk) if pk else None
    form = StatutoryRuleForm(request.POST or None, instance=obj,
                             initial=None if obj else {'effective_from': date.today().replace(day=1)})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Statutory rule saved. Please verify rates with your accountant.')
        return redirect('em:settings')
    return _form(request, form, 'Statutory rule', reverse('em:settings'))


# ------------------------------------------------------------------ payroll
def _ym(request):
    s = PayrollSettings.current()
    y, m = s.default_month()
    try:
        y = int(request.GET.get('year', y))
        m = int(request.GET.get('month', m))
        date(y, m, 1)
    except (ValueError, TypeError):
        pass
    return y, m


def _bulk_form(period):
    from .forms import BulkSalaryPaymentForm
    if not period:
        return None
    return BulkSalaryPaymentForm(initial={'payment_date': date.today()})


def payroll_dashboard(request):
    y, m = _ym(request)
    period = PayrollPeriod.objects.filter(year=y, month=m).first()
    dept = request.GET.get('department', '')
    rows = []
    if period:
        qs = period.payrolls.select_related('employee', 'employee__department').filter(
            Q(is_current=True) | Q(status=EmployeePayroll.DRAFT))
        if dept:
            qs = qs.filter(employee__department_id=dept)
        rows = list(qs.order_by('employee__employee_id', 'revision'))
        from . import salary_payments as sal
        for r in rows:
            r.salary = sal.status_for(r)
    cur = [r for r in rows if r.is_current]
    totals = {k: sum((getattr(r, f) for r in cur), ZERO) for k, f in
              (('gross', 'gross_earnings'), ('ded', 'total_deductions'), ('employer', 'employer_contributions'),
               ('net', 'net_salary'))}
    tmp = period or PayrollPeriod(year=y, month=m)
    eligible = engine.eligible_employees(tmp)
    done = {r.employee_id for r in rows}
    return render(request, T + 'payroll_dashboard.html', {
        'y': y, 'm': m, 'period': period, 'rows': rows, 'totals': totals, 'label': tmp.label,
        'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
        'years': range(date.today().year - 5, date.today().year + 2),
        'pending': [e for e in eligible if e.pk not in done], 'departments': Department.objects.all(), 'dept': dept,
        'period_form': PeriodForm(instance=period) if period else None,
        'bulk_pay_form': _bulk_form(period),
        'has_draft': any(r.status == 'draft' for r in rows), 'divisors': PayrollSettings.DIVISORS,
        'default_divisor': PayrollSettings.current().default_divisor,
    })


@require_POST
def payroll_generate(request):
    try:
        y, m = int(request.POST['year']), int(request.POST['month'])
        date(y, m, 1)
    except (KeyError, ValueError):
        messages.error(request, 'Choose a valid month and year.')
        return redirect('em:payroll')
    period = engine.get_or_create_period(y, m)
    div = request.POST.get('divisor')
    if div in dict(PayrollSettings.DIVISORS) and not period.payrolls.exists():
        period.divisor = div
        period.save()
    created, skipped = engine.generate(period)
    messages.success(request, f'{len(created)} draft payroll record(s) created for {period.label}.')
    for e, why in skipped:
        if 'already' not in why:
            messages.warning(request, f'{e.employee_id}: {why}')
    return redirect(f"{reverse('em:payroll')}?year={y}&month={m}")


@require_POST
def period_update(request, pid):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    form = PeriodForm(request.POST, instance=period)
    if form.is_valid():
        divisor_changed = 'divisor' in form.changed_data
        if divisor_changed and period.payrolls.filter(status='finalized').exists():
            messages.error(request, 'Divisor cannot change after payroll was finalized for this month.')
        else:
            form.save()
            if divisor_changed:
                for p in period.payrolls.filter(status='draft'):
                    engine.recalculate(p, {f: getattr(p, f) for f in EmployeePayroll.INPUT_FIELDS
                                           if f not in ('working_days', 'payable_days')})
            messages.success(request, 'Payroll month updated.')
    return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")


def payroll_edit(request, rid):
    p = get_object_or_404(EmployeePayroll.objects.select_related('employee', 'period'), pk=rid)
    back = f"{reverse('em:payroll')}?year={p.period.year}&month={p.period.month}"
    if p.is_finalized:
        return redirect('em:payroll_detail', rid)
    form = PayrollInputForm(request.POST or None, initial={f: getattr(p, f) for f in EmployeePayroll.INPUT_FIELDS})
    preview = None
    if request.method == 'POST' and form.is_valid():
        inputs = form.cleaned_data
        saved, calc = engine.recalculate(p, inputs)
        if saved is not None and saved.attendance_snapshot:
            saved.input_source = engine.source_for(saved, inputs)
            saved.save()
        if saved is None:
            for err in calc.errors:
                form.add_error(None, err)
        else:
            messages.success(request, f'{p.code} recalculated.')
            return redirect('em:payroll_detail', rid)
        preview = calc
    return render(request, T + 'payroll_edit.html', {'p': p, 'form': form, 'back_url': back, 'preview': preview,
                  'attendance': json.loads(p.attendance_snapshot) if p.attendance_snapshot else None})


@require_POST
def payroll_refresh_attendance(request, rid):
    p = get_object_or_404(EmployeePayroll, pk=rid)
    try:
        saved, calc = engine.refresh_from_attendance(p)
    except ValueError as ex:
        messages.error(request, str(ex))
        return redirect('em:payroll_detail', rid)
    if saved is None and calc is None:
        messages.warning(request, 'No attendance recorded for this month; inputs stay manual.')
    elif saved is None:
        messages.error(request, '; '.join(calc.errors))
    else:
        messages.success(request, f'{p.code} refreshed from attendance.')
    return redirect('em:payroll_detail', rid)


def _salary_ctx(p):
    from .forms import SalaryPaymentForm
    from .models import SalaryPayment
    from . import salary_payments as sal
    st = sal.status_for(p)
    return {'status': st, 'payments': SalaryPayment.objects.filter(
                payroll__period_id=p.period_id, payroll__employee_id=p.employee_id).select_related('bank_account', 'payroll'),
            'form': SalaryPaymentForm(initial={'payment_date': date.today(), 'amount': st['balance'] if st['balance'] > 0 else None}),
            'can_pay': p.is_finalized and p.is_current and st['balance'] > 0}


def payroll_detail(request, rid):
    p = get_object_or_404(EmployeePayroll.objects.select_related('employee', 'period', 'revises'), pk=rid)
    ded = list(p.deductions.all())
    return render(request, T + 'payroll_detail.html', {
        'p': p, 'earnings': p.earnings.all(), 'deductions': [d for d in ded if d.side == 'employee'],
        'employer': [d for d in ded if d.side == 'employer'],
        'history': EmployeePayroll.objects.filter(period=p.period, employee=p.employee).order_by('revision'),
        'revision_form': RevisionForm(),
        'salary': _salary_ctx(p),
        'attendance': json.loads(p.attendance_snapshot) if p.attendance_snapshot else None,
        'back_url': f"{reverse('em:payroll')}?year={p.period.year}&month={p.period.month}"})


@require_POST
def payroll_finalize(request, pid):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    ids = request.POST.getlist('ids')
    qs = period.payrolls.filter(status='draft')
    if ids:
        qs = qs.filter(pk__in=ids)
    rows = list(qs)
    missing = [r.code for r in rows if not r.snap_account_number]
    try:
        done = engine.finalize(rows)
    except ValidationError as ex:
        messages.error(request, 'Not finalized - ledger posting failed: ' + '; '.join(ex.messages))
        return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")
    messages.success(request, f'{len(done)} payroll record(s) finalized for {period.label}. They are now read-only.')
    if missing:
        messages.warning(request, 'No bank account on file for: ' + ', '.join(missing))
    return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")


@require_POST
def payroll_finalize_one(request, rid):
    p = get_object_or_404(EmployeePayroll, pk=rid)
    try:
        engine.finalize([p])
    except ValidationError as ex:
        messages.error(request, 'Not finalized - ledger posting failed: ' + '; '.join(ex.messages))
        return redirect('em:payroll_detail', rid)
    messages.success(request, f'{p.code} finalized.')
    return redirect('em:payroll_detail', rid)


def _msg_errors(request, ex):
    for m in getattr(ex, 'messages', [str(ex)]):
        messages.error(request, m)


@require_POST
def salary_payment_add(request, rid):
    from .forms import SalaryPaymentForm
    from . import salary_payments as sal
    p = get_object_or_404(EmployeePayroll, pk=rid)
    form = SalaryPaymentForm(request.POST)
    if form.is_valid():
        try:
            sp = sal.record_payment(p, **form.cleaned_data)
            messages.success(request, f'Salary payment of {sp.amount:,.2f} recorded for {p.code}.')
        except ValidationError as ex:
            _msg_errors(request, ex)
    else:
        for errs in form.errors.values():
            _msg_errors(request, ValidationError(errs))
    return redirect('em:payroll_detail', rid)


@require_POST
def salary_payment_cancel(request, spid):
    from .models import SalaryPayment
    from . import salary_payments as sal
    sp = get_object_or_404(SalaryPayment, pk=spid)
    try:
        sal.cancel_payment(sp, request.POST.get('reason'))
        messages.success(request, 'Salary payment cancelled and its ledger entry reversed.')
    except ValidationError as ex:
        _msg_errors(request, ex)
    return redirect('em:payroll_detail', sp.payroll_id)


@require_POST
def salary_pay_all(request, pid):
    from .forms import BulkSalaryPaymentForm
    from . import salary_payments as sal
    period = get_object_or_404(PayrollPeriod, pk=pid)
    form = BulkSalaryPaymentForm(request.POST)
    if form.is_valid():
        try:
            made = sal.pay_all_unpaid(period, **form.cleaned_data)
            messages.success(request, f'{len(made)} salary payment(s) recorded for {period.label} '
                                      f'(total {sum((s.amount for s in made), Decimal("0")):,.2f}).')
        except ValidationError as ex:
            _msg_errors(request, ex)
    else:
        for errs in form.errors.values():
            _msg_errors(request, ValidationError(errs))
    return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")


@require_POST
def payroll_delete_draft(request, rid):
    p = get_object_or_404(EmployeePayroll, pk=rid)
    period = p.period
    try:
        p.delete()
        messages.success(request, 'Draft payroll removed.')
    except FinalizedRecordError as ex:
        messages.error(request, str(ex))
    return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")


@require_POST
def payroll_revise(request, rid):
    p = get_object_or_404(EmployeePayroll, pk=rid)
    form = RevisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter a reason for the revision.')
        return redirect('em:payroll_detail', rid)
    try:
        r = engine.start_revision(p, form.cleaned_data['reason'])
    except ValueError as ex:
        messages.error(request, str(ex))
        return redirect('em:payroll_detail', rid)
    messages.success(request, f'Revision {r.revision} created as a draft. The original stays unchanged until '
                              'the revision is finalized.')
    return redirect('em:payroll_edit', r.pk)


def payslip_pdf(request, rid):
    p = get_object_or_404(EmployeePayroll, pk=rid)
    pdf, name = payslip_for(p)
    resp = HttpResponse(pdf, content_type='application/pdf')
    disp = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disp}; filename="{name}"'
    return resp


def _period_rows(period, request, finalized_only=False):
    qs = period.payrolls.filter(is_current=True).select_related('employee', 'period').order_by('snap_employee_code')
    if finalized_only:
        qs = qs.filter(status='finalized')
    d = request.GET.get('department')
    if d:
        qs = qs.filter(employee__department_id=d)
    return list(qs.prefetch_related('earnings', 'deductions'))


def payslip_zip(request, pid):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    rows = _period_rows(period, request)
    if not rows:
        messages.error(request, 'No payroll records to export.')
        return redirect(f"{reverse('em:payroll')}?year={period.year}&month={period.month}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in rows:
            pdf, name = payslip_for(p)
            z.writestr(name, pdf)
    m, y = period.label.split(' ')
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    resp['Content-Disposition'] = f'attachment; filename="Payslips_{m}_{y}.zip"'
    return resp


def export_register(request, pid):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    data = salary_register_xlsx(period, _period_rows(period, request))
    m, y = period.label.split(' ')
    resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="Salary_Register_{m}_{y}.xlsx"'
    return resp


def export_register_csv(request, pid):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    data = salary_register_csv(period, _period_rows(period, request))
    m, y = period.label.split(' ')
    resp = HttpResponse(data, content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="Salary_Register_{m}_{y}.csv"'
    return resp


def export_bank(request, pid, fmt):
    period = get_object_or_404(PayrollPeriod, pk=pid)
    rows = [r for r in _period_rows(period, request, finalized_only=True) if r.snap_account_number]
    pay = engine.expected_payment_date(period)
    m, y = period.label.split(' ')
    full = request.GET.get('full') == '1'  # masked to last 4 unless explicitly asked for (bank upload)
    name = f'CONFIDENTIAL_Bank_Transfer_{m}_{y}' + ('' if full else '_masked')
    if fmt == 'csv':
        resp = HttpResponse(bank_csv(period, rows, pay, full), content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="{name}.csv"'
    else:
        resp = HttpResponse(bank_xlsx(period, rows, pay, full),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{name}.xlsx"'
    return resp


# ------------------------------------------------------------------ reports
def reports(request):
    y, m = _ym(request)
    kind = request.GET.get('report', 'monthly')
    period = PayrollPeriod.objects.filter(year=y, month=m).first()
    ctx = {'y': y, 'm': m, 'kind': kind, 'period': period,
           'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
           'years': range(date.today().year - 5, date.today().year + 2),
           'employees': Employee.objects.order_by('employee_id'), 'label': f'{calendar.month_name[m]} {y}'}
    headers, rows, money = [], [], []
    if kind == 'monthly' and period:
        cur = period.payrolls.filter(is_current=True)
        headers = ['Employee ID', 'Name', 'Department', 'Status', 'Payable Days', 'Gross', 'Deductions',
                   'Employer Contrib.', 'Net']
        rows = [[p.code, p.snap_name, p.snap_department, p.get_status_display(), p.payable_days, p.gross_earnings,
                 p.total_deductions, p.employer_contributions, p.net_salary] for p in cur.order_by('snap_employee_code')]
        money = [5, 6, 7, 8]
    elif kind == 'department' and period:
        agg = (period.payrolls.filter(is_current=True).values('snap_department')
               .annotate(n=Count('id'), g=Sum('gross_earnings'), d=Sum('total_deductions'),
                         er=Sum('employer_contributions'), net=Sum('net_salary')).order_by('snap_department'))
        headers = ['Department', 'Employees', 'Gross', 'Deductions', 'Employer Contrib.', 'Net']
        rows = [[a['snap_department'], a['n'], a['g'], a['d'], a['er'], a['net']] for a in agg]
        money = [2, 3, 4, 5]
    elif kind == 'history':
        eid = request.GET.get('employee')
        ctx['eid'] = eid
        if eid:
            qs = EmployeePayroll.objects.filter(employee_id=eid).select_related('period').order_by(
                '-period__year', '-period__month', '-revision')
            headers = ['Month', 'Revision', 'Status', 'Current', 'Payable Days', 'Gross', 'Deductions', 'Net']
            rows = [[p.period.label, p.revision, p.get_status_display(), 'Yes' if p.is_current else 'No',
                     p.payable_days, p.gross_earnings, p.total_deductions, p.net_salary] for p in qs]
            money = [5, 6, 7]
    elif kind == 'joining':
        start = date(y, 1, 1)
        end = date(y, 12, 31)
        headers = ['Employee ID', 'Name', 'Department', 'Event', 'Date', 'Status']
        for e in Employee.objects.filter(date_of_joining__range=(start, end)).select_related('department'):
            rows.append([e.employee_id, e.full_name, e.department.name, 'Joined', e.date_of_joining,
                         e.get_status_display()])
        for e in Employee.objects.filter(date_of_leaving__range=(start, end)).select_related('department'):
            rows.append([e.employee_id, e.full_name, e.department.name, 'Left', e.date_of_leaving,
                         e.get_status_display()])
        rows.sort(key=lambda r: r[4])
    ctx.update(headers=headers, rows=rows, money=money)
    if request.GET.get('export') == 'xlsx' and headers:
        conv = [[float(c) if isinstance(c, Decimal) else c for c in r] for r in rows]
        data = generic_xlsx(kind, headers, conv, [i + 1 for i in money])
        resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="Report_{kind}_{y}_{m:02d}.xlsx"'
        return resp
    return render(request, T + 'reports.html', ctx)
