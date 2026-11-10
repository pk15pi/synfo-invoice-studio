"""Self-service password reset for active employees.

Eligibility is deliberately narrow: the account must be active and linked by
email to an Employee whose status is Active, Probation or Confirmed. Codes are
six digits, stored hashed, valid for 10 minutes, single use, and limited by a
resend cooldown and an attempt cap.
"""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import EmailMessage, get_connection
from django.db.models import Q
from django.utils import timezone

from employee_management import email_config as EC
from employee_management.models import Employee

from .models import PasswordResetOTP

User = get_user_model()

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5
SESSION_USER = 'pwreset_user_id'
SESSION_IDENTIFIER = 'pwreset_identifier'
SESSION_CHANNEL = 'pwreset_channel'

GENERIC_ERROR = ('No active employee record matches that username or email. Ask HR or your '
                 'administrator to check your employee record.')


def mask_email(value):
    local, _, domain = (value or '').strip().partition('@')
    if not domain:
        return '***'
    return f'{local[:1] or ""}***@{domain}'


def employee_for_reset(user, identifier=''):
    emails = {(getattr(user, 'email', '') or '').strip()}
    if '@' in (identifier or ''):
        emails.add(identifier.strip())
    emails = {email for email in emails if email}
    if not emails:
        return None
    query = Q()
    for email in emails:
        query |= Q(work_email__iexact=email) | Q(personal_email__iexact=email)
    return Employee.objects.filter(query).order_by('employee_id').first()


def active_employee_account(identifier):
    """Return (user, employee) only when exactly one active employed account matches.

    The identifier may be a username, the account email, or the work/personal
    email stored on the employee record.
    """
    identifier = (identifier or '').strip()
    if not identifier:
        return None, None
    matches = list(User.objects.filter(
        Q(username__iexact=identifier) | Q(email__iexact=identifier))[:2])
    if len(matches) > 1:
        return None, None
    if matches:
        user = matches[0]
        employee = employee_for_reset(user, identifier)
    else:
        user = None
        employee = None
        if '@' in identifier:
            employee = (Employee.objects
                        .filter(Q(work_email__iexact=identifier) | Q(personal_email__iexact=identifier))
                        .order_by('employee_id').first())
            if employee is not None:
                user = _user_for_employee(employee)
    if user is None or not user.is_active:
        return None, None
    if employee is None or employee.status not in Employee.EMPLOYED:
        return None, None
    return user, employee


def _user_for_employee(employee):
    emails = {email for email in (employee.work_email, employee.personal_email) if email}
    if not emails:
        return None
    query = Q()
    for email in emails:
        query |= Q(email__iexact=email)
    users = list(User.objects.filter(query)[:2])
    return users[0] if len(users) == 1 else None


def session_account(request):
    """Rebuild the flow's account from the session, re-checking eligibility."""
    user_id = request.session.get(SESSION_USER)
    if not user_id:
        return None, None
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None, None
    employee = employee_for_reset(user, request.session.get(SESSION_IDENTIFIER, ''))
    if employee is None or employee.status not in Employee.EMPLOYED:
        return None, None
    return user, employee


def reset_channels(employee):
    channels = []
    if employee.work_email:
        channels.append({
            'code': PasswordResetOTP.WORK,
            'label': PasswordResetOTP.CHANNELS[0][1],
            'email': employee.work_email,
            'masked': mask_email(employee.work_email),
        })
    if employee.personal_email:
        channels.append({
            'code': PasswordResetOTP.PERSONAL,
            'label': PasswordResetOTP.CHANNELS[1][1],
            'email': employee.personal_email,
            'masked': mask_email(employee.personal_email),
        })
    return channels


def issue_code(user, employee, channel, site_url=''):
    """Create and email a code. Returns (otp, error_message)."""
    options = {item['code']: item for item in reset_channels(employee)}
    if channel not in options:
        return None, 'Choose which email address should receive the code.'
    now = timezone.now()
    recent = (PasswordResetOTP.objects.filter(user=user, created_at__gte=now - RESEND_COOLDOWN)
              .order_by('-created_at').first())
    if recent:
        wait = int((recent.created_at + RESEND_COOLDOWN - now).total_seconds()) + 1
        return None, f'Please wait {wait} more seconds before requesting another code.'
    if (PasswordResetOTP.objects.filter(user=user, created_at__gte=now - timedelta(hours=1))
            .count() >= MAX_CODES_PER_HOUR):
        return None, 'Too many codes were requested in the last hour. Try again later.'
    raw_code = f'{secrets.randbelow(1_000_000):06d}'
    otp = PasswordResetOTP.objects.create(
        user=user, channel=channel, email=options[channel]['email'],
        code_hash=make_password(raw_code), expires_at=now + CODE_TTL)
    try:
        send_code_email(user, otp, raw_code, site_url=site_url)
    except Exception as exc:  # noqa: BLE001 - surfaced to the employee as a friendly message
        otp.delete()
        return None, f'The code could not be emailed: {EC._clean(exc)}'
    (PasswordResetOTP.objects.filter(user=user, used_at__isnull=True)
     .exclude(pk=otp.pk).update(used_at=now))
    return otp, ''


def send_code_email(user, otp, raw_code, site_url=''):
    connection, from_email = _mail_connection()
    name = user.get_full_name() or user.get_username()
    verify_url = site_url or 'http://127.0.0.1:8000/password-reset/verify/'
    body = (
        f'Hello {name},\n\n'
        f'A password reset was requested for the QuantBud account "{user.get_username()}".\n\n'
        f'Your verification code is:\n\n    {raw_code}\n\n'
        f'Enter it at {verify_url} within 10 minutes.\n'
        f'If you did not request this, ignore this email; your password will not change.\n\n'
        f'QuantBud Business Management\n'
    )
    EmailMessage(
        subject='QuantBud password reset code',
        body=body,
        from_email=from_email,
        to=[otp.email],
        connection=connection,
    ).send(fail_silently=False)


def _mail_connection():
    mapped = EC.require_ready()
    connection = get_connection(
        host=mapped['HOST'], port=mapped['PORT'], username=mapped['HOST_USER'],
        password=mapped['HOST_PASSWORD'], use_tls=mapped['USE_TLS'], use_ssl=mapped['USE_SSL'],
        fail_silently=False,
    )
    return connection, mapped['FROM']


def verify_code(user, channel, raw_code):
    """Return (otp, status) where status is ok, missing, expired, invalid or locked."""
    otp = (PasswordResetOTP.objects
           .filter(user=user, channel=channel, used_at__isnull=True)
           .order_by('-created_at').first())
    if otp is None:
        return None, 'missing'
    now = timezone.now()
    if otp.expires_at <= now:
        otp.used_at = now
        otp.save(update_fields=['used_at'])
        return None, 'expired'
    if otp.attempts >= MAX_ATTEMPTS:
        otp.used_at = now
        otp.save(update_fields=['used_at'])
        return None, 'locked'
    if not check_password(raw_code, otp.code_hash):
        otp.attempts += 1
        fields = ['attempts']
        if otp.attempts >= MAX_ATTEMPTS:
            otp.used_at = now
            fields.append('used_at')
        otp.save(update_fields=fields)
        return None, 'locked' if otp.used_at else 'invalid'
    return otp, 'ok'


def consume_code(otp):
    otp.used_at = timezone.now()
    otp.save(update_fields=['used_at'])


def clear_session(request):
    for key in (SESSION_USER, SESSION_IDENTIFIER, SESSION_CHANNEL):
        request.session.pop(key, None)
