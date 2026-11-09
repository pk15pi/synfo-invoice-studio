"""Shared reporting period selector (Indian FY: 1 April - 31 March).

Every report resolves its date range through ``resolve(params)`` so all reports use the same
interpretation.  Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar; H1 Apr-Sep, H2 Oct-Mar."""
import calendar
from dataclasses import dataclass, field
import re
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

OPTIONS = [
    ('today', 'Today'), ('yesterday', 'Yesterday'), ('this_week', 'This Week'), ('last_week', 'Last Week'),
    ('this_month', 'This Month'), ('prev_month', 'Previous Month'), ('this_quarter', 'Current Quarter'),
    ('prev_quarter', 'Previous Quarter'), ('h1', 'First Half of Financial Year (Apr-Sep)'),
    ('h2', 'Second Half of Financial Year (Oct-Mar)'), ('current_fy', 'Current Financial Year'),
    ('prev_fy', 'Previous Financial Year'), ('cal_year', 'Calendar Year'), ('prev_cal_year', 'Previous Calendar Year'),
    ('last_3m', 'Last 3 Months'), ('last_6m', 'Last 6 Months'), ('last_12m', 'Last 12 Months'),
    ('fy', 'Financial Year (choose)'), ('month', 'Month (choose)'), ('custom', 'Custom Date Range'),
]
FY_PARTS = [('', 'Full year'), ('Q1', 'Q1 Apr-Jun'), ('Q2', 'Q2 Jul-Sep'), ('Q3', 'Q3 Oct-Dec'), ('Q4', 'Q4 Jan-Mar'),
            ('H1', 'H1 Apr-Sep'), ('H2', 'H2 Oct-Mar')]
LABELS = dict(OPTIONS)


def today():
    return timezone.localdate()


def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def fy_label(y):
    return f'FY {y}-{str(y + 1)[-2:]}'


def fy_range(y):
    return date(y, 4, 1), date(y + 1, 3, 31)


def month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def add_months(y, m, n):
    t = y * 12 + (m - 1) + n
    return t // 12, t % 12 + 1


def fy_part_range(y, part):
    """Q1..Q4 / H1 / H2 of FY starting in April of ``y``."""
    starts = {'Q1': (y, 4, 3), 'Q2': (y, 7, 3), 'Q3': (y, 10, 3), 'Q4': (y + 1, 1, 3), 'H1': (y, 4, 6), 'H2': (y, 10, 6)}
    if part not in starts:
        return fy_range(y)
    sy, sm, n = starts[part]
    ey, em = add_months(sy, sm, n - 1)
    return date(sy, sm, 1), month_end(ey, em)


def quarter_of(d):
    q0 = (d.month - 1) // 3 * 3 + 1
    return date(d.year, q0, 1), month_end(d.year, q0 + 2)


def fy_quarter_name(d):
    return {4: 'Q1', 7: 'Q2', 10: 'Q3', 1: 'Q4'}[quarter_of(d)[0].month]


def parse_date(s):
    try:
        return date.fromisoformat((s or '').strip())
    except ValueError:
        return None


@dataclass
class Period:
    start: date
    end: date
    label: str
    key: str
    kind: str = 'days'  # day/week/month/quarter/half/fy/year/rolling/days - drives the previous-period rule
    params: dict = field(default_factory=dict)

    @property
    def range_text(self):
        return f'{self.start:%d %b %Y} to {self.end:%d %b %Y}'

    def months(self):
        out, (y, m) = [], (self.start.year, self.start.month)
        while (y, m) <= (self.end.year, self.end.month):
            out.append((y, m))
            y, m = add_months(y, m, 1)
        return out

    def previous(self):
        """The previous equivalent period: Nov 2026 -> Oct 2026, Q3 -> Q2, FY -> previous FY, etc.
        Ranges that are whole calendar months shift by the same number of months; others by the same number of days."""
        s, e = self.start, self.end
        if self.kind in ('day', 'week', 'days') or not (s.day == 1 and e == month_end(e.year, e.month)):
            n = (e - s).days + 1
            ps, pe = s - timedelta(days=n), s - timedelta(days=1)
            return Period(ps, pe, f'{ps:%d %b %Y} to {pe:%d %b %Y}', 'custom', self.kind)
        n = len(self.months())
        py, pm = add_months(s.year, s.month, -n)
        ey, em = add_months(e.year, e.month, -n)
        ps, pe = date(py, pm, 1), month_end(ey, em)
        if self.kind == 'fy':
            lab = fy_label(fy_start_year(ps))
        elif n == 1:
            lab = ps.strftime('%B %Y')
        elif self.kind == 'quarter':
            lab = f'{fy_quarter_name(ps)} {fy_label(fy_start_year(ps))} ({ps:%b}-{pe:%b %Y})'
        elif self.kind == 'half':
            lab = f"{'H1' if ps.month == 4 else 'H2'} {fy_label(fy_start_year(ps))} ({ps:%b %Y}-{pe:%b %Y})"
        elif self.kind == 'year':
            lab = f'Calendar year {ps.year}'
        else:
            lab = f'{ps:%b %Y} to {pe:%b %Y}'
        return Period(ps, pe, lab, 'custom', self.kind)


def resolve(params, default='current_fy', ref=None):
    if (params.get('period_mode') or '').strip() in ('calendar', 'financial', 'custom', 'weekly'):
        return resolve_standard(params, default=default, ref=ref)
    t = ref or today()
    key = (params.get('period') or '').strip()
    fy = (params.get('fy') or '').strip()
    part = (params.get('fy_part') or '').strip().upper()
    month = (params.get('month') or '').strip()
    dfrom, dto = parse_date(params.get('date_from')), parse_date(params.get('date_to'))
    if not key:
        key = 'custom' if (dfrom or dto) else 'month' if month else 'fy' if fy else default
    keep = {k: params.get(k) for k in ('period', 'fy', 'fy_part', 'month', 'date_from', 'date_to') if params.get(k)}
    keep['period'] = key
    if str(params.get('prior') or '').strip().lower() in ('exclude', 'only'):
        keep['prior'] = str(params.get('prior')).strip().lower()

    def P(s, e, label, kind):
        return Period(s, e, label, key, kind, keep)

    if key == 'today':
        return P(t, t, f'Today ({t:%d %b %Y})', 'day')
    if key == 'yesterday':
        y = t - timedelta(days=1)
        return P(y, y, f'Yesterday ({y:%d %b %Y})', 'day')
    if key in ('this_week', 'last_week'):
        s = t - timedelta(days=t.weekday()) - timedelta(days=7 if key == 'last_week' else 0)
        e = s + timedelta(days=6)
        return P(s, e, f"{'Last' if key == 'last_week' else 'This'} week ({s:%d %b} - {e:%d %b %Y})", 'week')
    if key in ('this_month', 'prev_month'):
        y, m = add_months(t.year, t.month, -1 if key == 'prev_month' else 0)
        return P(date(y, m, 1), month_end(y, m), date(y, m, 1).strftime('%B %Y'), 'month')
    if key in ('this_quarter', 'prev_quarter'):
        s, e = quarter_of(t)
        if key == 'prev_quarter':
            s, e = quarter_of(s - timedelta(days=1))
        return P(s, e, f'{fy_quarter_name(s)} {fy_label(fy_start_year(s))} ({s:%b}-{e:%b %Y})', 'quarter')
    if key in ('h1', 'h2'):
        y = fy_start_year(t)
        s, e = fy_part_range(y, key.upper())
        return P(s, e, f'{key.upper()} {fy_label(y)} ({s:%b %Y}-{e:%b %Y})', 'half')
    if key in ('current_fy', 'prev_fy'):
        y = fy_start_year(t) - (1 if key == 'prev_fy' else 0)
        return P(*fy_range(y), fy_label(y), 'fy')
    if key in ('cal_year', 'prev_cal_year'):
        y = t.year - (1 if key == 'prev_cal_year' else 0)
        return P(date(y, 1, 1), date(y, 12, 31), f'Calendar year {y}', 'year')
    if key in ('last_3m', 'last_6m', 'last_12m'):
        n = int(key[5:-1])
        y, m = add_months(t.year, t.month, -(n - 1))
        s = date(y, m, 1)
        return P(s, t, f'Last {n} months ({s:%b %Y} - {t:%d %b %Y})', 'rolling')
    if key == 'month' and month:
        try:
            y, m = (int(x) for x in month.split('-')[:2])
            return P(date(y, m, 1), month_end(y, m), date(y, m, 1).strftime('%B %Y'), 'month')
        except (ValueError, TypeError):
            pass
    if key == 'fy' and fy.isdigit():
        y = int(fy)
        s, e = fy_part_range(y, part)
        if part in ('Q1', 'Q2', 'Q3', 'Q4'):
            return P(s, e, f'{part} {fy_label(y)} ({s:%b}-{e:%b %Y})', 'quarter')
        if part in ('H1', 'H2'):
            return P(s, e, f'{part} {fy_label(y)} ({s:%b %Y}-{e:%b %Y})', 'half')
        return P(s, e, fy_label(y), 'fy')
    if key == 'custom' and (dfrom or dto):
        s, e = dfrom or dto, dto or dfrom
        if e < s:
            s, e = e, s
        return P(s, e, f'{s:%d %b %Y} to {e:%d %b %Y}', 'days')
    y = fy_start_year(t)
    p = Period(*fy_range(y), fy_label(y), 'current_fy', 'fy', {k: v for k, v in keep.items() if k == 'prior'} | {'period': 'current_fy'})
    return p


def parse_iso_week(value, ref=None):
    """Monday of an ISO week from an HTML week input (YYYY-Www); falls back to the current week."""
    t = ref or today()
    raw = (value or '').strip().upper()
    match = re.fullmatch(r'(\d{4})-W(\d{1,2})', raw)
    if match:
        year, week = int(match.group(1)), int(match.group(2))
        try:
            return date.fromisocalendar(year, week, 1)
        except ValueError:
            pass
    return t - timedelta(days=t.weekday())


def resolve_standard(params, default='current_fy', ref=None):
    """Resolve the shared calendar/FY filter controls, retaining legacy query support."""
    t = ref or today()
    mode = (params.get('period_mode') or 'financial').strip()
    keep = {k: params.get(k) for k in (
        'period_mode', 'calendar_year', 'calendar_month', 'fy', 'period_type',
        'period_value', 'date_from', 'date_to', 'week', 'prior') if params.get(k)}
    if mode == 'weekly':
        start = parse_iso_week(params.get('week'), t)
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        keep['week'] = f'{iso.year}-W{iso.week:02d}'
        return Period(
            start, end,
            f'Week of {start:%d %b %Y} – {end:%d %b %Y}',
            'week', 'week', keep,
        )
    if mode == 'calendar':
        try:
            year = int(params.get('calendar_year') or t.year)
            if not 1900 <= year <= 9998:
                raise ValueError
        except (TypeError, ValueError):
            year = t.year
        month = (params.get('calendar_month') or '').strip()
        if month:
            try:
                m = int(month)
                if not 1 <= m <= 12:
                    raise ValueError
                s, e = date(year, m, 1), month_end(year, m)
                return Period(s, e, s.strftime('%B %Y'), 'month', 'month', keep)
            except (TypeError, ValueError):
                pass
        return Period(date(year, 1, 1), date(year, 12, 31), f'Calendar year {year}', 'cal_year', 'year', keep)

    if mode == 'custom':
        start, end = parse_date(params.get('date_from')), parse_date(params.get('date_to'))
        if start or end:
            start, end = start or end, end or start
            if end < start:
                start, end = end, start
            return Period(start, end, f'{start:%d %b %Y} to {end:%d %b %Y}', 'custom', 'days', keep)
        mode = 'financial'

    try:
        fy = int(params.get('fy') or fy_start_year(t))
        if not 1900 <= fy <= 9998:
            raise ValueError
    except (TypeError, ValueError):
        fy = fy_start_year(t)
    kind = (params.get('period_type') or 'full_fy').strip().lower()
    value = (params.get('period_value') or '').strip().upper()
    if kind == 'monthly':
        try:
            month = int(value)
            if not 1 <= month <= 12:
                raise ValueError
        except (TypeError, ValueError):
            month = t.month
        year = fy if month >= 4 else fy + 1
        start, end = date(year, month, 1), month_end(year, month)
        return Period(start, end, start.strftime('%B %Y'), 'fy', 'month', keep)
    if kind == 'quarterly':
        part = value if value in ('Q1', 'Q2', 'Q3', 'Q4') else 'Q1'
    elif kind == 'half_yearly':
        part = value if value in ('H1', 'H2') else 'H1'
    else:
        part = ''
    start, end = fy_part_range(fy, part)
    label = f'{part} {fy_label(fy)}' if part else fy_label(fy)
    if part:
        label += f' ({start:%b %Y} - {end:%b %Y})'
    return Period(start, end, label, 'fy', 'quarter' if part.startswith('Q') else 'half' if part else 'fy', keep)


def calendar_year_choices(back=8, forward=1):
    year = today().year
    return range(year + forward, year - back - 1, -1)


MONTH_CHOICES = [(n, calendar.month_name[n]) for n in range(1, 13)]


def fy_choices(back=8, forward=1):
    cur = fy_start_year(today())
    return [(y, fy_label(y)) for y in range(cur + forward, cur - back - 1, -1)]


def pct_change(cur, prev):
    """Returns (Decimal or None, text). prev == 0 is handled: both zero -> 'n/a', otherwise 'new'."""
    cur, prev = Decimal(cur or 0), Decimal(prev or 0)
    if prev == 0:
        return None, ('n/a' if cur == 0 else 'new')
    v = ((cur - prev) / abs(prev) * 100).quantize(Decimal('0.1'))
    return v, f'{v:+}%'
