"""Section hub pages for the grouped navigation (read-only overviews across employees)."""
import os
import calendar

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import (Employee, EmployeeBankAccount, EmployeeDocument, EmployeePayroll, EmployeeSalaryStructure,
                     PayrollPeriod)

HUB = 'employee_management/hub_table.html'


def _c(text, url=None):
    return {'t': '' if text is None else text, 'u': url}


def _emp(e):
    return _c(e.employee_id, reverse('em:employee_detail', args=[e.pk]))


def _hub(request, section, title, intro, headers, rows, actions=()):
    return render(request, HUB, {'section': section, 'title': title, 'intro': intro, 'headers': headers,
                                 'rows': rows, 'actions': actions})


def personal_details(request):
    rows = [[_emp(e), _c(e.full_name), _c(e.get_gender_display()), _c(e.date_of_birth and e.date_of_birth.strftime('%d-%m-%Y')),
             _c(e.mobile), _c(e.personal_email), _c(e.city), _c(e.get_status_display()),
             _c('Edit', reverse('em:employee_edit', args=[e.pk]))]
            for e in Employee.objects.order_by('employee_id')]
    return _hub(request, 'Employee Management', 'Personal details', 'Personal and contact details of every employee, including former staff.',
                ['ID', 'Name', 'Gender', 'Date of birth', 'Mobile', 'Email', 'City', 'Status', ''], rows)


def documents(request):
    qs = EmployeeDocument.objects.filter(is_current=True).select_related('employee').order_by('employee__employee_id', 'document_type')
    rows = [[_emp(d.employee), _c(d.employee.full_name), _c(d.get_document_type_display()), _c(d.document_name),
             _c(d.number_masked), _c(d.expiry_date and d.expiry_date.strftime('%d-%m-%Y')),
             _c('View', reverse('em:document_view', args=[d.employee_id, d.pk]))] for d in qs]
    return _hub(request, 'Employee Management', 'Documents', 'Document repository: current version of every employee document. Files are served only through these controlled links; replaced versions stay in each document\'s history.',
                ['ID', 'Name', 'Type', 'Document', 'Number', 'Expiry', ''], rows)


def bank_details(request):
    qs = EmployeeBankAccount.objects.filter(is_current=True).select_related('employee').order_by('employee__employee_id')
    rows = [[_emp(b.employee), _c(b.employee.full_name), _c(b.bank_name), _c(b.account_masked), _c(b.ifsc_code),
             _c(b.get_account_type_display()), _c('Change', reverse('em:bank_new', args=[b.employee_id]))] for b in qs]
    return _hub(request, 'Employee Management', 'Bank details', 'Current salary account per employee (masked). Changing an account keeps the old one as history.',
                ['ID', 'Name', 'Bank', 'Account', 'IFSC', 'Type', ''], rows)


def salary_structures(request):
    qs = EmployeeSalaryStructure.objects.select_related('employee').order_by('employee__employee_id', '-effective_from', '-id')
    rows = []
    for s in qs.prefetch_related('components'):
        rows.append([_emp(s.employee), _c(s.employee.full_name), _c(s.get_structure_type_display()),
                     _c(s.effective_from.strftime('%d-%m-%Y')), _c(s.effective_to and s.effective_to.strftime('%d-%m-%Y') or 'Open'),
                     _c(f'{s.total_earnings:.2f}'), _c(f'{s.net_salary:.2f}'),
                     _c('Revise', reverse('em:salary_revise', args=[s.employee_id, s.pk]))])
    return _hub(request, 'Payroll Management', 'Salary structures', 'Component-based structures. Every revision is preserved; revising creates a new row.',
                ['ID', 'Name', 'Type', 'Effective from', 'Effective to', 'Gross / month', 'Net / month', ''], rows)


def payslips(request):
    today = timezone.localdate()
    current_fy = today.year if today.month >= 4 else today.year - 1
    fy_choices = {current_fy}
    for year, month in PayrollPeriod.objects.filter(
            payrolls__status=EmployeePayroll.FINALIZED, payrolls__is_current=True
    ).values_list('year', 'month').distinct():
        fy_choices.add(year if month >= 4 else year - 1)
    try:
        fy_start = int(request.GET.get('fy', current_fy))
    except (TypeError, ValueError):
        fy_start = current_fy
    if fy_start not in fy_choices:
        fy_start = current_fy
    fy_end = fy_start + 1
    fy_label = f'FY {fy_start}-{str(fy_end)[-2:]}'

    fy_periods = Q(period__year=fy_start, period__month__gte=4) | Q(period__year=fy_end, period__month__lte=3)
    qs = (EmployeePayroll.objects.filter(fy_periods, status=EmployeePayroll.FINALIZED)
          .select_related('period', 'employee')
          .order_by('-period__year', '-period__month', 'snap_employee_code', '-revision'))
    rows = [[_c(p.code, reverse('em:employee_detail', args=[p.employee_id])), _c(p.snap_name), _c(p.period.label),
             _c(p.revision), _c('Current' if p.is_current else 'Superseded'), _c(f'{p.net_salary:.2f}'),
             _c('PDF', reverse('em:payslip', args=[p.pk]))] for p in qs]

    periods = { (p.year, p.month): p for p in PayrollPeriod.objects.filter(
        (Q(year=fy_start, month__gte=4) | Q(year=fy_end, month__lte=3)),
        payrolls__status=EmployeePayroll.FINALIZED, payrolls__is_current=True
    ).distinct() }
    month_exports = []
    last_month = today.month if fy_start == current_fy else 3
    last_year = today.year if fy_start == current_fy else fy_end
    for offset in range(12):
        year = fy_start + (3 + offset) // 12
        month = (3 + offset) % 12 + 1
        if (year, month) > (last_year, last_month):
            break
        period = periods.get((year, month))
        month_exports.append({'label': f'{calendar.month_name[month]} {year}',
                              'url': reverse('em:payslip_zip', args=[period.pk]) + '?finalized=1' if period else None})

    context = {'section': 'Payroll Management', 'title': 'Payslips',
               'intro': 'Download finalized payslips by month or for the full financial year.',
               'headers': ['ID', 'Name', 'Month', 'Revision', 'State', 'Net', ''], 'rows': rows,
               'actions': [], 'fy_start': fy_start, 'fy_label': fy_label,
               'fy_choices': [(year, f'FY {year}-{str(year + 1)[-2:]}')
                              for year in sorted(fy_choices, reverse=True)], 'month_exports': month_exports,
               'year_zip_url': reverse('em:payslip_year_zip', args=[fy_start]),
               'quarter_exports': [
                   {'label': f'Export Q{q}',
                    'url': reverse('em:payslip_quarter_tds', args=[q]) + f'?fy={fy_start}'}
                   for q in (1, 2, 3, 4)]}
    return render(request, HUB, context)


def storage(request):
    root = str(settings.EMPLOYEE_PRIVATE_ROOT)
    size = n = 0
    if os.path.isdir(root):
        for dp, _, files in os.walk(root):
            for f in files:
                n += 1
                try:
                    size += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    agg = EmployeeDocument.objects.aggregate(c=Count('id'), s=Sum('file_size'))
    rows = [[_c('Private employee files folder'), _c(root)],
            [_c('Files on disk'), _c(f'{n} ({size / 1048576:.2f} MB)')],
            [_c('Document records (all versions)'), _c(agg['c'])],
            [_c('Current documents'), _c(EmployeeDocument.objects.filter(is_current=True).count())],
            [_c('Public media folder (invoice logos/signatures)'), _c(str(settings.MEDIA_ROOT))],
            [_c('Database'), _c(str(settings.DATABASES['default']['NAME']))]]
    return _hub(request, 'Shared', 'Document storage', 'Employee files live in a private folder that is never mapped to a URL; they are only served by the controlled document views. Back up this folder together with db.sqlite3 (manage.py backup_data).',
                ['Item', 'Value'], rows, [('Open document repository', reverse('em:documents'))])
def payslip_quarter_tds(request, quarter):
    """Excel download: one row per employee for an Indian FY quarter (CA salary TDS)."""
    from django.http import Http404, HttpResponse
    from .exports import salary_tds_quarter_xlsx

    if quarter not in (1, 2, 3, 4):
        raise Http404
    today = timezone.localdate()
    current_fy = today.year if today.month >= 4 else today.year - 1
    try:
        fy_start = int(request.GET.get('fy', current_fy))
    except (TypeError, ValueError):
        fy_start = current_fy
    if fy_start < 2000 or fy_start > 2100:
        fy_start = current_fy
    data, filename = salary_tds_quarter_xlsx(fy_start, quarter)
    resp = HttpResponse(
        data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp

