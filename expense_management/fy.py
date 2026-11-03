"""Indian financial year helpers (1 April - 31 March, labelled 'FY 2026-27')."""
import calendar
from datetime import date, timedelta

from django.utils import timezone


def today():
    return timezone.localdate()


def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def fy_range(start_year):
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def fy_label(start_year):
    return f'FY {start_year}-{str(start_year + 1)[-2:]}'


def fy_of(d):
    return fy_label(fy_start_year(d))


def month_range(y, m):
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def quarter_range(d):
    q0 = (d.month - 1) // 3 * 3 + 1  # Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar are also calendar quarters
    return date(d.year, q0, 1), month_range(d.year, q0 + 2)[1]


def fy_choices(around=None, back=6, forward=1):
    cur = fy_start_year(around or today())
    return [(y, fy_label(y)) for y in range(cur + forward, cur - back - 1, -1)]


PERIODS = [('this_month', 'This month'), ('prev_month', 'Previous month'), ('this_quarter', 'This quarter'),
           ('current_fy', 'Current financial year'), ('prev_fy', 'Previous financial year'),
           ('custom', 'Custom date range')]


def parse_date(s):
    try:
        return date.fromisoformat((s or '').strip())
    except ValueError:
        return None


def resolve_period(params, default='current_fy'):
    """Return (start, end, label, key) from GET params: period / fy / month (YYYY-MM) / date_from / date_to."""
    t = today()
    key = params.get('period') or ''
    fy = (params.get('fy') or '').strip()
    month = (params.get('month') or '').strip()
    dfrom, dto = parse_date(params.get('date_from')), parse_date(params.get('date_to'))
    if not key:
        if dfrom or dto:
            key = 'custom'
        elif month:
            key = 'month'
        elif fy:
            key = 'fy'
        else:
            key = default
    if key == 'this_month':
        s, e = month_range(t.year, t.month)
        return s, e, t.strftime('%B %Y'), key
    if key == 'prev_month':
        p = t.replace(day=1) - timedelta(days=1)
        s, e = month_range(p.year, p.month)
        return s, e, p.strftime('%B %Y'), key
    if key == 'this_quarter':
        s, e = quarter_range(t)
        return s, e, f'Quarter {s:%b}-{e:%b %Y}', key
    if key == 'prev_fy':
        y = fy_start_year(t) - 1
        return (*fy_range(y), fy_label(y), key)
    if key == 'month':
        try:
            y, m = (int(x) for x in month.split('-')[:2])
            s, e = month_range(y, m)
            return s, e, s.strftime('%B %Y'), key
        except (ValueError, TypeError):
            pass
    if key == 'fy' and fy.isdigit():
        y = int(fy)
        return (*fy_range(y), fy_label(y), key)
    if key == 'custom' and (dfrom or dto):
        s = dfrom or date(2000, 1, 1)
        e = dto or date(2100, 12, 31)
        if e < s:
            s, e = e, s
        return s, e, f'{s:%d-%m-%Y} to {e:%d-%m-%Y}', 'custom'
    y = fy_start_year(t)
    return (*fy_range(y), fy_label(y), 'current_fy')


def months_between(s, e):
    out, y, m = [], s.year, s.month
    while (y, m) <= (e.year, e.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out
