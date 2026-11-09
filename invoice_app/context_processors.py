from .models import BusinessProfile
from .page_help import PAGE_GUIDES
from reporting import periods as report_periods


def brand_name(request):
    from .branding import logo_url
    profile = BusinessProfile.objects.filter(pk=1).first()
    name = profile.name if profile else ''
    return {'brand_name': name or 'QuantBud',
            'business_brand': (profile.brand_name if profile else '') or '',
            'business_logo_url': logo_url(profile) if profile else ''}


def page_help(request):
    """Provide the guide matching the current named route to the shared shell."""
    match = getattr(request, 'resolver_match', None)
    route_name = getattr(match, 'view_name', '') or ''
    guide = PAGE_GUIDES.get(route_name)
    if guide is None and route_name.startswith('admin:'):
        guide = _admin_page_guide(route_name)
    return {
        'page_help_key': route_name,
        'page_help_guide': guide,
        'page_help_missing': bool(route_name and guide is None),
    }


def _window(current, back=5, forward=1, selected=None):
    """Newest first: next `forward` years, the current one and the previous `back`; plus an
    out-of-window year already chosen in the URL so the picker never loses the user's selection."""
    years = list(range(current + forward, current - back - 1, -1))
    try:
        sel = int(selected) if selected else None
    except (TypeError, ValueError):
        sel = None
    if sel and 1900 <= sel <= 9998 and sel not in years:
        years = sorted(years + [sel], reverse=True)
    return years


FY_MONTH_ORDER = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
FY_QUARTERS = [('Q1', 'Q1 (Apr\u2013Jun)'), ('Q2', 'Q2 (Jul\u2013Sep)'), ('Q3', 'Q3 (Oct\u2013Dec)'), ('Q4', 'Q4 (Jan\u2013Mar)')]
FY_HALVES = [('H1', 'H1 (Apr\u2013Sep)'), ('H2', 'H2 (Oct\u2013Mar)')]


def standard_period_filter(request):
    """Shared choices for the application's calendar and Indian FY filters."""
    t = report_periods.today()
    g = getattr(request, 'GET', {})
    cur_fy = report_periods.fy_start_year(t)
    months = dict(report_periods.MONTH_CHOICES)
    return {
        'standard_calendar_year_choices': _window(t.year, selected=g.get('calendar_year')),
        'standard_month_choices': report_periods.MONTH_CHOICES,
        'standard_fy_month_choices': [(m, months[m]) for m in FY_MONTH_ORDER],
        'standard_fy_quarter_choices': FY_QUARTERS,
        'standard_fy_half_choices': FY_HALVES,
        'standard_fy_choices': [(y, report_periods.fy_label(y)) for y in _window(cur_fy, selected=g.get('fy'))],
        'standard_current_calendar_year': t.year,
        'standard_current_fy': cur_fy,
        'standard_current_month': t.month,
        'standard_current_iso_week': f"{t.isocalendar().year}-W{t.isocalendar().week:02d}",
    }


def _admin_page_guide(route_name):
    """Build a model-specific guide for Django Admin's generated CRUD screens."""
    from django.apps import apps

    key = route_name.removeprefix('admin:')
    actions = ('_changelist', '_add', '_change', '_delete', '_history', '_password_change')
    action = next((item[1:] for item in actions if key.endswith(item)), '')
    model = None
    model_key_length = 0
    for candidate in apps.get_models():
        model_key = f'{candidate._meta.app_label}_{candidate._meta.model_name}'
        if key.startswith(model_key + '_') and len(model_key) > model_key_length:
            model = candidate
            model_key_length = len(model_key)
    if model:
        model_name = str(model._meta.verbose_name).capitalize()
        plural_name = str(model._meta.verbose_name_plural).capitalize()
    else:
        model_name = 'administration record'
        plural_name = 'administration records'

    pages = {
        'index': ('Administration home', 'Open the available administration sections and manage records you are authorized to maintain.', ['Choose the relevant section.', 'Use the list to search for a record or add one when needed.']),
        'login': ('Administrator sign-in', 'Sign in with an authorized administrator account to access staff-only tools.', ['Enter the account credentials provided by your administrator.', 'Sign out when you have finished on a shared device.']),
        'logout': ('Administrator signed out', 'The administrator session has ended.', ['Sign in again only if you need to continue administrator work.']),
        'changelist': (plural_name, f'Browse and maintain {plural_name.lower()} using the filters and search available on this list.', ['Search or filter the list.', 'Open a record to review or edit it.', 'Use Add only when a new record is supported and verified.']),
        'add': (f'Add {model_name.lower()}', f'Create a new {model_name.lower()} record.', ['Complete the required fields.', 'Review related records and values before saving.']),
        'change': (f'Edit {model_name.lower()}', f'Review or update an existing {model_name.lower()} record.', ['Confirm you opened the intended record.', 'Change only values supported by verified information.', 'Save and review the resulting record.']),
        'delete': (f'Delete {model_name.lower()}', f'Review the request to delete this {model_name.lower()} record.', ['Check the record identity and any related objects listed.', 'Continue only if deletion is intended and permitted.']),
        'history': (f'{model_name} history', f'Review recorded changes to this {model_name.lower()} record.', ['Check timestamps, user and changed values.', 'Use the current record page for any supported correction.']),
        'password_change': ('Change administrator password', 'Change the password for the selected administrator account.', ['Follow your organization’s credential process.', 'Use a unique password and store it only in the approved password manager.']),
    }
    if route_name == 'admin:auth_user_password_change':
        action = 'password_change'
    if route_name == 'admin:login':
        action = 'login'
    if route_name == 'admin:logout':
        action = 'logout'
    if route_name == 'admin:index':
        action = 'index'
    title, overview, steps = pages.get(action, (f'{model_name} administration', f'Use this administrator page to review {model_name.lower()} information.', ['Review the page title and visible fields.', 'Use only the actions shown for your account.']))
    return {
        'title': title,
        'overview': overview,
        'purpose': f'This page is part of the restricted administration area for {plural_name.lower()}.',
        'steps': steps,
        'rules': ['Administrator permissions depend on the signed-in account.', 'Changes to financial or employee records can affect reports and should be supported by verified source documents.'],
        'related': [f'{plural_name} records and their linked application workflows.'],
        'notes': ['The visible field list in this manual is read from the current page.'],
    }
