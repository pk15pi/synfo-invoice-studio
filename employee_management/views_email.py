"""Email salary slips page and SMTP settings helpers used by payroll settings."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from . import email_config as EC
from . import payslip_email as PE
from .forms import EmailSettingsForm
from .models import EmployeePayroll, PayslipEmailLog


def email_slips(request):
    if not PE.has_period_params(request.GET):
        params = PE.previous_month_params()
        for key in ('q', 'page_size'):
            if request.GET.get(key):
                params[key] = request.GET.get(key)
        return redirect(f"{reverse('em:payslip_email')}?{urlencode(params)}")
    period = PE.resolve_page_period(request.GET)
    search = (request.GET.get('q') or '').strip()
    rows = list(PE.slips_for_period(period, search=search))
    queue = PE.bulk_queue(rows)
    pub = EC.public_config()
    return render(request, 'employee_management/payslip_email.html', {
        'period': period,
        'rows': rows,
        'search': search,
        'smtp_ready': pub['password_set'],
        'send_gap_seconds': pub['send_gap_seconds'],
        'bulk_pending_ids': queue['pending_ids'],
        'bulk_skipped_sent': queue['skipped_already_sent'],
        'bulk_skipped_email': queue['skipped_no_email'],
        'bulk_total': queue['total'],
        'send_json_url_template': reverse('em:payslip_email_send_json', args=[0]).replace('/0/', '/__ID__/'),
    })


@require_POST
def email_slip_send(request, rid):
    payroll = get_object_or_404(
        EmployeePayroll.objects.select_related('employee', 'period'),
        pk=rid, status=EmployeePayroll.FINALIZED, is_current=True,
    )
    log = PE.send_payslip(payroll)
    if log.success:
        messages.success(
            request,
            f'Sent {payroll.period.label} payslip for {payroll.code} to {log.recipient_email}.',
        )
        try:
            from audit.recorder import log_manual
            log_manual(
                'employee_management', 'PayslipEmailLog', str(log.pk),
                f'{payroll.code} {payroll.period.label}', 'create',
                f'Payslip emailed to {log.recipient_email}',
                {'success': [None, True]},
            )
        except Exception:
            pass
    else:
        messages.error(
            request,
            f'Could not send {payroll.period.label} payslip for {payroll.code}: {log.error}',
        )
    nxt = request.POST.get('next') or reverse('em:payslip_email')
    if not nxt.startswith('/'):
        nxt = reverse('em:payslip_email')
    return redirect(nxt)


@require_POST
def email_slip_send_json(request, rid):
    """JSON send used by Send to all / Bulk send (sequential client POSTs)."""
    payroll = get_object_or_404(
        EmployeePayroll.objects.select_related('employee', 'period'),
        pk=rid, status=EmployeePayroll.FINALIZED, is_current=True,
    )
    skip_if_sent = request.POST.get('skip_if_sent') == '1'
    if skip_if_sent and PayslipEmailLog.objects.filter(payroll=payroll, success=True).exists():
        return JsonResponse({
            'ok': True, 'status': 'skipped', 'reason': 'already_sent',
            'code': payroll.code, 'month': payroll.period.label,
        })
    if not PE.employee_email(payroll.employee):
        log = PE.send_payslip(payroll)  # records the no-email failure consistently
        return JsonResponse({
            'ok': False, 'status': 'skipped', 'reason': 'no_email',
            'code': payroll.code, 'month': payroll.period.label, 'error': log.error,
        })
    log = PE.send_payslip(payroll)
    if log.success:
        try:
            from audit.recorder import log_manual
            log_manual(
                'employee_management', 'PayslipEmailLog', str(log.pk),
                f'{payroll.code} {payroll.period.label}', 'create',
                f'Payslip emailed to {log.recipient_email}',
                {'success': [None, True]},
            )
        except Exception:
            pass
        return JsonResponse({
            'ok': True, 'status': 'sent',
            'code': payroll.code, 'month': payroll.period.label,
            'email': log.recipient_email,
        })
    return JsonResponse({
        'ok': False, 'status': 'failed',
        'code': payroll.code, 'month': payroll.period.label, 'error': log.error,
    })


def email_settings_context(request):
    pub = EC.public_config()
    initial = {
        'host': pub['host'],
        'port': pub['port'],
        'user': pub['user'],
        'from_email': pub['from_email'],
        'use_tls': pub['use_tls'],
        'use_ssl': pub['use_ssl'],
        'send_gap_seconds': pub['send_gap_seconds'],
    }
    return {
        'email_form': EmailSettingsForm(initial=initial),
        'email_pub': pub,
    }


def handle_email_settings_post(request):
    form = EmailSettingsForm(request.POST)
    pub = EC.public_config()
    if not form.is_valid():
        return False, form, pub
    try:
        EC.save_config(form.cleaned_data)
        messages.success(request, 'Outgoing email settings saved (password not shown).')
        return True, EmailSettingsForm(initial={
            'host': form.cleaned_data['host'],
            'port': form.cleaned_data['port'],
            'user': form.cleaned_data['user'],
            'from_email': form.cleaned_data['from_email'],
            'use_tls': form.cleaned_data['use_tls'],
            'use_ssl': form.cleaned_data['use_ssl'],
            'send_gap_seconds': form.cleaned_data['send_gap_seconds'],
        }), EC.public_config()
    except OSError as exc:
        messages.error(request, f'Could not save email settings: {exc.strerror or exc.__class__.__name__}')
        return False, form, pub
