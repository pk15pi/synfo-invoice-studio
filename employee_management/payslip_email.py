"""Send finalized payslip PDFs to employee email addresses via Microsoft 365 SMTP."""
from datetime import date

from django.core.mail import EmailMessage, get_connection
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone

from reporting import periods as report_periods

from . import email_config as EC
from .models import EmployeePayroll, PayslipEmailLog
from .pdf.payslip import payslip_for

PERIOD_PARAM_KEYS = (
    'period_mode', 'calendar_year', 'calendar_month', 'fy', 'period_type',
    'period_value', 'date_from', 'date_to',
)


def employee_email(employee):
    return (employee.work_email or employee.personal_email or '').strip()


def has_period_params(params):
    return any((params.get(key) or '').strip() for key in PERIOD_PARAM_KEYS)


def previous_month_params(ref=None):
    """Query dict for the shared picker: Yearly + previous calendar month (Asia/Calcutta)."""
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


def slips_for_period(period, search=''):
    """Finalized current payslips whose salary month falls in ``period``."""
    months = period.months()
    if not months:
        return EmployeePayroll.objects.none()
    month_q = Q()
    for year, month in months:
        month_q |= Q(period__year=year, period__month=month)
    qs = (
        EmployeePayroll.objects.filter(month_q, status=EmployeePayroll.FINALIZED, is_current=True)
        .select_related('period', 'employee')
        .order_by('snap_employee_code', 'employee__employee_id', 'period__year', 'period__month')
    )
    search = (search or '').strip()
    if search:
        qs = qs.filter(
            Q(snap_employee_code__icontains=search)
            | Q(snap_name__icontains=search)
            | Q(employee__employee_id__icontains=search)
            | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
            | Q(employee__work_email__icontains=search)
            | Q(employee__personal_email__icontains=search)
        )
    latest_ok = (
        PayslipEmailLog.objects.filter(payroll_id=OuterRef('pk'), success=True)
        .order_by('-sent_at')
        .values('sent_at')[:1]
    )
    any_attempt = PayslipEmailLog.objects.filter(payroll_id=OuterRef('pk'))
    return qs.annotate(
        last_sent_at=Subquery(latest_ok),
        email_attempted=Exists(any_attempt),
    )


def bulk_queue(slips):
    """Split slips into pending send ids vs skip reasons (across the full filtered set)."""
    pending, skipped_sent, skipped_email = [], [], []
    for slip in slips:
        email = employee_email(slip.employee)
        if slip.last_sent_at:
            skipped_sent.append(slip.pk)
        elif not email:
            skipped_email.append(slip.pk)
        else:
            pending.append(slip.pk)
    return {
        'pending_ids': pending,
        'skipped_already_sent': len(skipped_sent),
        'skipped_no_email': len(skipped_email),
        'total': len(pending) + len(skipped_sent) + len(skipped_email),
    }


def _company_name():
    try:
        from invoice_app.models import BusinessProfile
        profile = BusinessProfile.objects.filter(pk=1).first()
        if profile:
            return (profile.brand_name or profile.name or '').strip() or 'QuantBud'
    except Exception:
        pass
    return 'QuantBud'


def send_payslip(payroll, *, connection=None):
    """Attach the existing payslip PDF and email it. Always writes a PayslipEmailLog row.

    Returns the log row. Never raises for SMTP/config failures — those are stored on the log.
    """
    employee = payroll.employee
    recipient = employee_email(employee)
    month_label = payroll.period.label
    if not recipient:
        return PayslipEmailLog.objects.create(
            payroll=payroll,
            employee=employee,
            recipient_email='',
            success=False,
            error='Employee has no work or personal email address on file.',
            period_year=payroll.period.year,
            period_month=payroll.period.month,
        )
    cfg = None
    try:
        cfg = EC.load_config()
        mapped = EC.require_ready(cfg)
        pdf_bytes, filename = payslip_for(payroll)
        company = _company_name()
        subject = f'Salary slip — {month_label}'
        body = (
            f'Dear {payroll.snap_name or employee.full_name},\n\n'
            f'Please find attached your salary slip for {month_label}.\n\n'
            f'This message was sent by {company}. '
            f'If you have questions about this payslip, reply to this email or contact HR.\n\n'
            f'Regards,\n{company}\n'
        )
        conn = connection or get_connection(
            host=mapped['HOST'],
            port=mapped['PORT'],
            username=mapped['HOST_USER'],
            password=mapped['HOST_PASSWORD'],
            use_tls=mapped['USE_TLS'],
            use_ssl=mapped['USE_SSL'],
            fail_silently=False,
        )
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=mapped['FROM'],
            to=[recipient],
            connection=conn,
        )
        message.attach(filename, pdf_bytes, 'application/pdf')
        message.send(fail_silently=False)
        return PayslipEmailLog.objects.create(
            payroll=payroll,
            employee=employee,
            recipient_email=recipient,
            success=True,
            error='',
            period_year=payroll.period.year,
            period_month=payroll.period.month,
        )
    except Exception as exc:  # noqa: BLE001 - always record the outcome
        return PayslipEmailLog.objects.create(
            payroll=payroll,
            employee=employee,
            recipient_email=recipient,
            success=False,
            error=EC._clean(exc, cfg)[:500],
            period_year=payroll.period.year,
            period_month=payroll.period.month,
        )


def resolve_page_period(params):
    return report_periods.resolve_standard(params, default='current_fy')
