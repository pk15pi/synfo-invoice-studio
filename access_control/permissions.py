"""Role-to-module access rules for the whole application.

Roles are intentionally coarse: one role grants a set of business modules.
Module boundaries follow URL prefixes so enforcement applies to every view
without adding decorators across the existing apps.
"""
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from .models import RoleDefinition, UserProfile

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
SELF_SERVICE = 'self_service'

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
    SELF_SERVICE: 'My Workspace',
}

# My Workspace is personal, so it is granted only to the Employee Self-Service role
# (and to nobody else, including Admin/Director).
ALL_MODULES = frozenset(MODULE_LABELS) - {SELF_SERVICE}

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
    ('/roles/', USERS),
    ('/hr/', HR),
    ('/me/', SELF_SERVICE),
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
    Role.IT_ADMIN: frozenset({USERS, BACKUPS, AUDIT, SHARED}),
    Role.PAYROLL_EXECUTIVE: frozenset({PAYROLL, ATTENDANCE, DOCUMENTS, SHARED}),
    Role.BILLING: frozenset({INVOICING, REPORTS, BANK, DOCUMENTS, SHARED}),
    Role.COMPLIANCE_OFFICER: frozenset({COMPLIANCE, REPORTS, AUDIT, DOCUMENTS, SHARED}),
    Role.DOCUMENT_CONTROLLER: frozenset({DOCUMENTS, SHARED}),
    Role.ASSET_MANAGER: frozenset({ASSETS, EXPENSES, REPORTS, DOCUMENTS, SHARED}),
    Role.MANAGEMENT: frozenset({REPORTS, FINANCIAL_REPORTS, DOCUMENTS, AUDIT, SHARED}),
    Role.EMPLOYEE: frozenset({SELF_SERVICE, SHARED}),
}

READ_ONLY_ROLES = frozenset({Role.AUDITOR, Role.MANAGEMENT, Role.EMPLOYEE})

# Roles allowed into the Users & roles screens.
USER_MANAGEMENT_ROLES = (Role.ADMIN, Role.DIRECTOR, Role.IT_ADMIN)

# Login itself, asset serving and Django admin have their own handling.
EXEMPT_PREFIXES = ('/login/', '/logout/', '/password-reset/', '/admin/', '/static/', '/media/', '/favicon.ico', '/business/logo/')


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
    return profile.role or ''


def custom_role(role):
    """Return the RoleDefinition for a custom role code, or None for built-in roles."""
    if not role or role in Role.values:
        return None
    return RoleDefinition.objects.filter(code=role).first()


def role_label(role):
    if not role:
        return 'No role'
    if role in ROLE_MODULES:
        return dict(Role.choices).get(role, role)
    definition = custom_role(role)
    return definition.name if definition else 'No role'


def is_read_only_role(role):
    if role in READ_ONLY_ROLES:
        return True
    definition = custom_role(role)
    return bool(definition and definition.read_only)


def allowed_modules(user):
    role = role_for(user)
    if role in ROLE_MODULES:
        return ROLE_MODULES[role]
    definition = custom_role(role)
    if definition:
        return frozenset(m for m in definition.modules if m in MODULE_LABELS) | {SHARED}
    return SHARED_ONLY


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
