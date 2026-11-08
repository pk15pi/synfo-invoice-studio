"""Shared helpers for list/report filter bars: Apply + Clear buttons, the applied-filters summary
and the "More filters" disclosure. Display only: never changes how a view filters its data."""
from django import template
from django.http import QueryDict
from django.utils import timezone

register = template.Library()

IGNORE = {'sort', 'direction', 'page', 'export', 'download', 'format', 'revise', 'csrfmiddlewaretoken'}
PERIOD_KEYS = {'period_mode', 'calendar_year', 'calendar_month', 'fy', 'period_type', 'period_value',
               'date_from', 'date_to'}
DEFAULT_VALUES = {'', 'all'}
LABELS = {
    'q': 'Search', 'client': 'Customer', 'c': 'Customer', 'status': 'Status', 'employee': 'Employee',
    'department': 'Department', 'category': 'Category', 'vendor': 'Vendor', 'account': 'Account',
    'month': 'Month', 'year': 'Year', 'date': 'Date', 'app': 'Area', 'model': 'Record type',
    'object_id': 'Record ID', 'action': 'Action', 'source': 'Source', 'type': 'Type',
    'payment_status': 'Payment status', 'claim_status': 'Claim status', 'invoice_status': 'Invoice/payment state',
    'kind': 'Kind', 'mode': 'Mode', 'report': 'Report', 'sensitivity': 'Sensitivity', 'state': 'State',
    'prior': 'Prior period rows', 'candidate': 'Candidate', 'designation': 'Designation', 'method': 'Billing method',
    'group_by': 'Subtotal by', 'by': 'Trend by', 'dataset': 'Dataset', 'cols': 'Columns', 'project': 'Project',
    'consultant': 'Consultant', 'recruiter': 'Recruiter', 'recruiter_search': 'Recruiter search',
    'jd_search': 'JD number', 'page_size': 'Rows per page', 'which': 'Checks',
    'claim_date_from': 'Claim date from', 'claim_date_to': 'Claim date to',
    'expense_date_from': 'Expense date from', 'expense_date_to': 'Expense date to',
    'joining_from': 'Joining from', 'joining_to': 'Joining to',
    'recruitment_from': 'Recruitment from', 'recruitment_to': 'Recruitment to', 'fy_a': 'FY A', 'fy_b': 'FY B',
    'entity': 'Related record', 'asset': 'Related asset', 'template_version': 'Template',
}
LEGACY_PERIODS = {'last3': 'Last 3 months', 'previous': 'Previous month', 'last6': 'Last 6 months'}


def label_for(key):
    return LABELS.get(key, key.replace('_', ' ').capitalize())


def _period_is_default(context, g):
    if not any(g.get(k) for k in PERIOD_KEYS):
        return True
    if (g.get('period_mode') or 'financial') != 'financial':
        return False
    default_fy = context.get('standard_filter_default_fy') or context.get('standard_current_fy')
    if g.get('fy') and str(g.get('fy')) != str(default_fy):
        return False
    default_type = 'monthly' if context.get('standard_filter_default_type') == 'monthly' else 'full_fy'
    ptype = g.get('period_type') or default_type
    if ptype != default_type:
        return False
    if ptype == 'monthly' and g.get('period_value') and \
            str(g.get('period_value')) != str(context.get('standard_filter_default_value') or ''):
        return False
    return True


def _period_label(g):
    try:
        from reporting import periods
        return periods.resolve(g).label
    except Exception:  # never let a summary break a page
        return 'Custom period'


def _legacy_period_label(g, key):
    import calendar
    from reporting import periods
    try:
        if key == 'year_month':
            year, month = int(g.get('year')), g.get('month')
            return f'{calendar.month_name[int(month)]} {year}' if month else f'Calendar year {year}'
        if key == 'financial_year':
            return periods.fy_label(int(g.get('financial_year')))
    except (TypeError, ValueError, IndexError):
        pass
    return LEGACY_PERIODS.get(key, key.replace('_', ' '))


def applied_filters(context):
    """[(key, label, value)] for every non-default filter in the current URL."""
    request = context.get('request')
    if request is None:
        return []
    g = QueryDict(request.META.get('QUERY_STRING', ''))
    items = []
    if not _period_is_default(context, g):
        items.append(('period', 'Period', _period_label(g)))
    today = timezone.localdate()
    current_month = g.get('month') in ('', None, str(today.month)) and g.get('year') in ('', None, str(today.year))
    for key in g.keys():
        if key in IGNORE or key in PERIOD_KEYS:
            continue
        values = [v for v in g.getlist(key) if v.strip() not in DEFAULT_VALUES]
        if not values:
            continue
        if key == 'prior' and values == ['include']:
            continue
        if key in ('month', 'year') and current_month:
            continue
        if key == 'period':
            items.append(('period', 'Period', _legacy_period_label(g, values[0])))
            continue
        if key in ('year', 'financial_year') and g.get('period') in ('year_month', 'financial_year'):
            continue
        if key == 'month' and g.get('period') == 'year_month':
            continue
        items.append((key, label_for(key), ', '.join(values)))
    return items


@register.inclusion_tag('invoice_app/_filter_actions.html', takes_context=True)
def filter_actions(context, clear_url=None, apply=True):
    request = context.get('request')
    return {'clear_url': clear_url or (request.path if request else '?'), 'apply': apply,
            'items': applied_filters(context)}


@register.simple_tag(takes_context=True)
def more_filters_open(context, names):
    """'open' when any of the comma-separated params has a non-default value (keeps the disclosure open)."""
    request = context.get('request')
    if request is None:
        return ''
    for name in names.split(','):
        if any(v.strip() not in DEFAULT_VALUES for v in QueryDict(request.META.get('QUERY_STRING', '')).getlist(name.strip())):
            return 'open'
    return ''