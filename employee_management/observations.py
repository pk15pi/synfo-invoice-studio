"""Shared HR-dashboard observation queries (keep list filters in sync with dashboard)."""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from django.http import QueryDict

from .models import Employee, EmployeeDocument, EmployeeSalaryStructure, PayrollSettings

OBS_LABELS = {
    'expiring': 'Expiring documents',
    'expired': 'Expired documents',
    'missing_bank': 'Missing bank details',
    'missing_salary': 'Missing salary structure',
}

EMPLOYEE_OBS = frozenset({'missing_bank', 'missing_salary'})
DOCUMENT_OBS = frozenset({'expiring', 'expired'})


def normalize_obs(raw, allowed):
    key = (raw or '').strip().lower()
    return key if key in allowed else ''


def obs_label(obs):
    return OBS_LABELS.get(obs, '')


def obs_clear_url(request):
    """Same path/filters with obs (and page) removed."""
    q = request.GET.copy()
    q.pop('obs', None)
    q.pop('page', None)
    # also drop prefixed pagers
    for k in list(q.keys()):
        if k.endswith('_page') and k[:-5].startswith('t'):
            q.pop(k, None)
    encoded = q.urlencode()
    return request.path + (('?' + encoded) if encoded else '')


def expiring_documents_qs(today=None, settings=None):
    """Current docs expiring within alert window (includes already expired). Same as HR dashboard."""
    s = settings or PayrollSettings.current()
    today = today or timezone.localdate()
    soon = today + timedelta(days=s.expiry_alert_days)
    return (
        EmployeeDocument.objects.filter(
            is_current=True,
            expiry_date__isnull=False,
            expiry_date__lte=soon,
        )
        .select_related('employee')
        .order_by('expiry_date', 'employee__employee_id', 'document_type')
    )


def expired_documents_qs(today=None):
    today = today or timezone.localdate()
    return (
        EmployeeDocument.objects.filter(
            is_current=True,
            expiry_date__isnull=False,
            expiry_date__lt=today,
        )
        .select_related('employee')
        .order_by('expiry_date', 'employee__employee_id', 'document_type')
    )


def employees_missing_bank():
    """Employed staff with no current bank account — same as HR dashboard count."""
    return (
        Employee.objects.filter(status__in=Employee.EMPLOYED)
        .exclude(bank_accounts__is_current=True)
        .select_related('department')
        .order_by('employee_id')
    )


def employees_missing_salary():
    """Employed staff with no salary structure — same population as HR dashboard list."""
    return (
        Employee.objects.filter(status__in=Employee.EMPLOYED)
        .annotate(n_structures=Count('salary_structures'))
        .filter(n_structures=0)
        .select_related('department')
        .order_by('employee_id')
    )
