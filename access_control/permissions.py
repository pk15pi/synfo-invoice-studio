"""Role-to-module access rules for the whole application.

Roles are intentionally coarse: one role grants a set of business modules.
Module boundaries follow URL prefixes so enforcement applies to every view
without adding decorators across the existing apps.
"""
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from .models import UserProfile

Role = UserProfile.Role

INVOICING = 'invoicing'
RECRUITMENT = 'recruitment'
HR = 'hr'
PAYROLL = 'payroll'
ATTENDANCE = 'attendance'
PERFORMANCE = 'performance'
EXPENSES = 'expenses'
REPORTS = 'reports'
ACCOUNTING = 'accounting'
BANK = 'bank'
COMPLIANCE = 'compliance'
ASSETS = 'assets'
FINANCIAL_REPORTS = 'financial_reports'
DOCUMENTS = 'documents'
AUDIT = 'audit'
BACKUPS = 'backups'
USERS = 'users'
SHARED = 'shared'

MODULE_LABELS = {
    INVOICING: 'Invoice Management',
    RECRUITMENT: 'Recruitment',
    HR: 'Employee Management',
    PAYROLL: 'Payroll Management',
    ATTENDANCE: 'Attendance & Leave',
    PERFORMANCE: 'Performance Review',
    EXPENSES: 'Expense Management',
    REPORTS: 'Reports & Analytics',
    ACCOUNTING: 'Accounting',
    BANK: 'Bank',
    COMPLIANCE: 'Compliance',
    ASSETS: 'Asset Management',
    FINANCIAL_REPORTS: 'Financial Reports & Audit Centre',
    DOCUMENTS: 'Document & Agreement Management',
    AUDIT: 'Audit Log',
    BACKUPS: 'Backups',
    USERS: 'Users & roles',
    SHARED: 'Shared',
}

ALL_MODULES = frozenset(MODULE_LABELS)

# Longest prefixes first so nested URLs resolve to the intended module.
PREFIX_MODULES = (
    ('/financial-reports/', FINANCIAL_REPORTS),
    ('/description-templates/', INVOICING),
    ('/format-templates/', INVOICING),
    ('/accounting/', ACCOUNTING),
    ('/attendance/', ATTENDANCE),
    ('/compliance/', COMPLIANCE),
    ('/recruitment/', RECRUITMENT),
    ('/receipts/', INVOICING),
    ('/backups/', BACKUPS),
    ('/documents/', DOCUMENTS),
    ('/doc-index/', DOCUMENTS),
    ('/employees/', HR),
    ('/personal-details/', HR),
    ('/employee-documents/', HR),
    ('/bank-details/', HR),
    ('/departments/', HR),
    ('/performance/', PERFORMANCE),
    ('/payroll/', PAYROLL),
    ('/expenses/', EXPENSES),
    ('/invoices/', INVOICING),
    ('/invoice/', INVOICING),
    ('/invoicing/', INVOICING),
    ('/projects/', INVOICING),
    ('/manpower/', INVOICING),
    ('/customers/', INVOICING),
    ('/audit/', AUDIT),
    ('/assets/', ASSETS),
    ('/reports/', REPORTS),
    ('/bank/', BANK),
    ('/shared/', SHARED),
    ('/business/', SHARED),
    ('/geo/', SHARED),
    ('/storage/', HR),
    ('/users/', USERS),
    ('/hr/', HR),
)

SHARED_ONLY = frozenset({SHARED})

ROLE_MODULES = {
    Role.ADMIN: ALL_MODULES,
    Role.DIRECTOR: ALL_MODULES,
    Role.CA: frozenset({INVOICING, PAYROLL, EXPENSES, REPORTS, ACCOUNTING, BANK, COMPLIANCE,
                        FINANCIAL_REPORTS, DOCUMENTS, AUDIT, SHARED}),
    Role.HEAD_ACCOUNTANT: frozenset({INVOICING, PAYROLL, EXPENSES, REPORTS, ACCOUNTING, BANK,
                                     COMPLIANCE, FINANCIAL_REPORTS, ASSETS, DOCUMENTS, SHARED}),
    Role.ACCOUNTANT: frozenset({INVOICING, EXPENSES, REPORTS, ACCOUNTING, BANK, COMPLIANCE,
                                FINANCIAL_REPORTS, ASSETS, DOCUMENTS, SHARED}),
    Role.HEAD_HR: frozenset({HR, PAYROLL, ATTENDANCE, PERFORMANCE, RECRUITMENT, EXPENSES,
                             ASSETS, DOCUMENTS, SHARED}),
    Role.HR: frozenset({HR, PAYROLL, ATTENDANCE, PERFORMANCE, RECRUITMENT, EXPENSES,
                        ASSETS, DOCUMENTS, SHARED}),
    Role.SENIOR_RECRUITER: frozenset({HR, ATTENDANCE, RECRUITMENT, PERFORMANCE, DOCUMENTS, SHARED}),
    Role.RECRUITER: frozenset({HR, ATTENDANCE, RECRUITMENT, PERFORMANCE, DOCUMENTS, SHARED}),
    Role.PROJECT_MANAGER: frozenset({INVOICING, RECRUITMENT, EXPENSES, REPORTS, DOCUMENTS, SHARED}),
    Role.AUDITOR: frozenset({REPORTS, FINANCIAL_REPORTS, AUDIT, DOCUMENTS, SHARED}),
}

READ_ONLY_ROLES = frozenset({Role.AUDITOR})

# Login itself, asset serving and Django admin have their own handling.
EXEMPT_PREFIXES = ('/login/', '/logout/', '/admin/', '/static/', '/media/', '/favicon.ico', '/business/logo/')


def is_exempt_path(path):
    return path.startswith(EXEMPT_PREFIXES)


def role_for(user):
    if not getattr(user, 'is_authenticated', False):
        return ''
    if user.is_superuser:
        return Role.ADMIN
    try:
        profile = user.access_profile
    except ObjectDoesNotExist:
        return ''
    return profile.role if profile.role in Role.values else ''


def allowed_modules(user):
    return ROLE_MODULES.get(role_for(user), SHARED_ONLY)


def can_access(user, module):
    return module in allowed_modules(user)


def module_for_path(path):
    if path in ('', '/'):
        return SHARED
    for prefix, module in PREFIX_MODULES:
        if path.startswith(prefix):
            return module
    return None


def role_required(*roles):
    """Defense-in-depth check for views that also sit behind the middleware."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if role_for(request.user) not in roles:
                raise PermissionDenied('Your role does not allow this action.')
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
