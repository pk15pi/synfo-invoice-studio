"""Section hub pages for the grouped navigation (read-only overviews across employees)."""
import os
import calendar
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from reporting import periods as report_periods

from .models import (Employee, EmployeeBankAccount, EmployeeDocument, EmployeePayroll, EmployeeSalaryStructure,
                     PayrollPeriod)

PERIOD_PARAM_KEYS = (
    'period_mode', 'calendar_year', 'calendar_month', 'fy', 'period_type',
    'period_value', 'date_from', 'date_to',
)


def _has_period_params(params):
    return any((params.get(key) or '').strip() for key in PERIOD_PARAM_KEYS)


def _previous_month_params(ref=None):
    today = ref or timezone.localdate()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1
    return {
        'period_mode': 'calendar',
        'calendar_year': str(year),
        'calendar_month': str(month),
    }


def _export_fy_start(period, params):
    """FY used by ZIP / Q1-Q4 export buttons for the current picker selection."""
    mode = (params.get('period_mode') or '').strip()
    raw_fy = (params.get('fy') or '').strip()
    if mode == 'financial' or (raw_fy and mode not in ('calendar', 'custom')):
        try:
            fy = int(raw_fy)
            if 1900 <= fy <= 9998:
                return fy
        except (TypeError, ValueError):
            pass
    # Calendar year / custom range: use the FY that contains the period end (Apr-Mar).
    return report_periods.fy_start_year(period.end)

HUB = 'employee_management/hub_table.html'


def _c(text, url=None, *, blank=False, icon=None, html=False):
    return {'t': '' if text is None else text, 'u': url, 'blank': blank, 'icon': icon, 'html': html}


def _emp(e):
    return _c(e.employee_id, reverse('em:employee_detail', args=[e.pk]))


def _hub(request, section, title, intro, headers, rows, actions=(), **extra):
    ctx = {'section': section, 'title': title, 'intro': intro, 'headers': headers,
           'rows': rows, 'actions': actions}
    ctx.update(extra)
    return render(request, HUB, ctx)


def personal_details(request):
    rows = [[_emp(e), _c(e.full_name), _c(e.get_gender_display()), _c(e.date_of_birth and e.date_of_birth.strftime('%d-%m-%Y')),
             _c(e.mobile), _c(e.personal_email), _c(e.city), _c(e.get_status_display()),
             _c('Edit', reverse('em:employee_edit', args=[e.pk]))]
            for e in Employee.objects.order_by('employee_id')]
    return _hub(request, 'Employee Management', 'Personal details', 'Personal and contact details of every employee, including former staff.',
                ['ID', 'Name', 'Gender', 'Date of birth', 'Mobile', 'Email', 'City', 'Status', ''], rows)



def documents(request):
    """One row per employed employee; one pill per current document (A / P / Misc)."""
    from django.db.models import Prefetch
    from django.utils.html import format_html
    from django.utils.safestring import mark_safe

    from . import observations as obs
    from .models import PayrollSettings

    # Only Aadhaar and PAN get letter pills. Experience Certificate stays Misc (own button each).
    FIXED_ORDER = ('aadhaar', 'pan')
    FIXED_LETTER = {'aadhaar': 'A', 'pan': 'P'}
    TYPE_ALIASES = {
        'aadhar': 'aadhaar',
        'adhar': 'aadhaar',
        'aadhaar_card': 'aadhaar',
        'aadhar_card': 'aadhaar',
        'pan_card': 'pan',
    }

    def canonical_type(raw):
        key = (raw or '').strip().lower().replace(' ', '_').replace('-', '_')
        return TYPE_ALIASES.get(key, key)

    observation = obs.normalize_obs(request.GET.get('obs'), obs.DOCUMENT_OBS)
    settings = PayrollSettings.current()
    alert_days = settings.expiry_alert_days

    employees = (
        Employee.objects.filter(status__in=Employee.EMPLOYED)
        .select_related('department')
        .order_by('employee_id')
    )
    if observation == 'expiring':
        emp_ids = obs.expiring_documents_qs().values_list('employee_id', flat=True).distinct()
        employees = employees.filter(pk__in=emp_ids)
    elif observation == 'expired':
        emp_ids = obs.expired_documents_qs().values_list('employee_id', flat=True).distinct()
        employees = employees.filter(pk__in=emp_ids)

    employees = employees.prefetch_related(
        Prefetch(
            'documents',
            queryset=EmployeeDocument.objects.filter(is_current=True).order_by(
                'document_type', 'document_name', 'id'),
            to_attr='current_docs',
        )
    )

    def _doc_caption(doc):
        label = doc.get_document_type_display()
        title = (doc.document_name or '').strip()
        state = doc.expiry_state(alert_days)
        bits = [label]
        if title and title.lower() != label.lower():
            bits.append(title)
        if doc.expiry_date:
            bits.append(f"expires {doc.expiry_date.strftime('%d-%m-%Y')}")
            if state == 'expired':
                bits.append('expired')
            elif state == 'expiring':
                bits.append('expiring soon')
        return ' · '.join(bits)

    def _pill(doc, letter, *, misc=False):
        url = reverse('em:document_view', args=[doc.employee_id, doc.pk])
        caption = _doc_caption(doc)
        state = doc.expiry_state(alert_days)
        classes = ['doc-pill', 'doc-pill-misc' if misc else 'doc-pill-round']
        if state == 'expired':
            classes.append('doc-pill-expired')
        elif state == 'expiring':
            classes.append('doc-pill-expiring')
        return format_html(
            '<a class="{}" href="{}" target="_blank" rel="noopener" title="{}" aria-label="{}">{}</a>',
            ' '.join(classes), url, caption, caption, letter,
        )

    def _docs_cell(employee):
        docs = list(getattr(employee, 'current_docs', []))
        buckets = {key: [] for key in FIXED_ORDER}
        misc = []
        for doc in docs:
            key = canonical_type(doc.document_type)
            if key in buckets:
                buckets[key].append(doc)
            else:
                misc.append(doc)
        parts = []
        for key in FIXED_ORDER:
            letter = FIXED_LETTER[key]
            for doc in buckets[key]:
                parts.append(_pill(doc, letter))
        for doc in misc:
            parts.append(_pill(doc, 'Misc', misc=True))
        if not parts:
            return _c(mark_safe('<span class="muted doc-pills-empty">—</span>'), html=True)
        return _c(
            format_html('<span class="doc-pills">{}</span>', mark_safe(''.join(parts))),
            html=True,
        )

    rows = []
    for employee in employees:
        dept = employee.department.name if employee.department_id else '—'
        rows.append([
            _emp(employee),
            _c(employee.full_name),
            _c(dept),
            _c(employee.get_status_display()),
            _docs_cell(employee),
        ])

    return _hub(
        request, 'Employee Management', 'Documents',
        'One row per employed employee. Each current document is its own button: Aadhaar (A), PAN (P), '
        'and Misc for every other file (including Experience Certificate). Open a button to view the file in a new tab.',
        ['ID', 'Name', 'Department', 'Status', 'Documents'], rows,
        obs=observation,
        obs_label=obs.obs_label(observation),
        obs_clear_url=obs.obs_clear_url(request),
    )




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
    """Payslips hub: standard period picker filters the list by salary month."""
    if not _has_period_params(request.GET):
        params = _previous_month_params()
        if request.GET.get('page_size'):
            params['page_size'] = request.GET.get('page_size')
        return redirect(f"{reverse('em:payslips')}?{urlencode(params)}")

    period = report_periods.resolve_standard(request.GET, default='current_fy')
    months = period.months()
    month_q = Q()
    for year, month in months:
        month_q |= Q(period__year=year, period__month=month)
    qs = (
        EmployeePayroll.objects.filter(month_q, status=EmployeePayroll.FINALIZED)
        .select_related('period', 'employee')
        .order_by('-period__year', '-period__month', 'snap_employee_code', '-revision')
    )
    rows = [
        [_c(p.code, reverse('em:employee_detail', args=[p.employee_id])), _c(p.snap_name), _c(p.period.label),
         _c(p.revision), _c('Current' if p.is_current else 'Superseded'), _c(f'{p.net_salary:.2f}'),
         _c('View PDF', reverse('em:payslip', args=[p.pk]), blank=True, icon='pdf')]
        for p in qs
    ]

    today = timezone.localdate()
    current_fy = report_periods.fy_start_year(today)
    fy_start = _export_fy_start(period, request.GET)
    fy_end = fy_start + 1
    fy_label = report_periods.fy_label(fy_start)

    periods = {
        (p.year, p.month): p for p in PayrollPeriod.objects.filter(
            (Q(year=fy_start, month__gte=4) | Q(year=fy_end, month__lte=3)),
            payrolls__status=EmployeePayroll.FINALIZED, payrolls__is_current=True,
        ).distinct()
    }
    month_exports = []
    last_month = today.month if fy_start == current_fy else 3
    last_year = today.year if fy_start == current_fy else fy_end
    for offset in range(12):
        year = fy_start + (3 + offset) // 12
        month = (3 + offset) % 12 + 1
        if (year, month) > (last_year, last_month):
            break
        payroll_period = periods.get((year, month))
        month_exports.append({
            'label': f'{calendar.month_name[month]} {year}',
            'url': (
                reverse('em:payslip_zip', args=[payroll_period.pk]) + '?finalized=1'
                if payroll_period else None
            ),
        })

    # Carry standard picker params into Q1-Q4 links so fy stays explicit.
    q_extra = f'?fy={fy_start}'
    context = {
        'section': 'Payroll Management',
        'title': 'Payslips',
        'intro': (
            'Filter finalized payslips by salary month with the shared period picker, '
            'then download monthly or full-year ZIPs and CA salary TDS Excel.'
        ),
        'headers': ['ID', 'Name', 'Month', 'Revision', 'State', 'Net', ''],
        'rows': rows,
        'actions': [],
        'period': period,
        'fy_start': fy_start,
        'fy_label': fy_label,
        'month_exports': month_exports,
        'year_zip_url': reverse('em:payslip_year_zip', args=[fy_start]),
        'quarter_exports': [
            {'label': f'Export Q{q}', 'url': reverse('em:payslip_quarter_tds', args=[q]) + q_extra}
            for q in (1, 2, 3, 4)
        ],
        'use_standard_period_filter': True,
    }
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

