"""Billing calculations. Pure functions, Decimal only (no floats).

ROUNDING POLICY (single place, documented in README):
* Unit price shown on the invoice = rate rounded to 2 decimals with ROUND_HALF_UP.
* Line amount is computed from the *exact* fraction (monthly x days / divisor),
  never from the rounded unit price, then rounded once according to
  ``BusinessProfile.amount_rounding``:
    - ``rupee_down`` (default): truncate to whole rupees (ROUND_DOWN to 1.00).
      This is the only rule that reproduces all four QB020 sample amounts:
      100000x23/31=74193.55->74,193.00; 110000x31/31=110,000.00;
      110000x23/31=81612.90->81,612.00; 120000x37/31=143225.81->143,225.00.
    - ``paise``: round to 2 decimals ROUND_HALF_UP.
* Invoice total = sum of the (already rounded) line amounts.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

CENT = Decimal('0.01')
RUPEE = Decimal('1')

FIXED = 'fixed'
VARIABLE = 'variable'
CALENDAR = 'calendar'
THIRTY = '30'


def to_decimal(value, default='0'):
    if value is None or value == '':
        return Decimal(default)
    if isinstance(value, float):
        value = repr(value)
    return Decimal(str(value).replace(',', '').strip())


def q2(value):
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def round_amount(value, mode='rupee_down'):
    value = to_decimal(value)
    if mode == 'rupee_down':
        return value.quantize(RUPEE, rounding=ROUND_DOWN).quantize(CENT)
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def inclusive_days(start, end):
    if not start or not end or end < start:
        return None
    return (end - start).days + 1


def divisor_for(basis, reference_date):
    """Divisor for Option B. Calendar basis uses the month of the period END date
    (the sample's 25 Sep - 31 Oct line is divided by 31, October's length)."""
    if basis == THIRTY:
        return Decimal(30)
    return Decimal(days_in_month(reference_date))


@dataclass
class LineCalc:
    quantity: Decimal
    unit_price: Decimal | None
    amount: Decimal
    divisor: Decimal | None = None


def calculate_line(pricing_type, days, *, monthly_amount=0, daily_rate=0, basis=CALENDAR,
                   full_month=False, period_end=None, fallback_date=None, rounding='rupee_down'):
    days = q2(days)
    monthly = to_decimal(monthly_amount)
    if pricing_type == VARIABLE:
        rate = to_decimal(daily_rate)
        return LineCalc(days, q2(rate), round_amount(rate * days, rounding))
    ref = period_end or fallback_date or date.today()
    divisor = divisor_for(basis, ref)
    if full_month:
        unit = q2(monthly / days) if days else q2(monthly)
        return LineCalc(days, unit, q2(monthly), divisor)
    exact = monthly * days / divisor
    return LineCalc(days, q2(monthly / divisor), round_amount(exact, rounding), divisor)


MONTHLY_PRORATED, PER_DAY, MONTHLY_FIXED = 'monthly_prorated', 'per_day', 'monthly_fixed'


def calculate_by_billing_type(billing_type, days, *, monthly_amount=0, daily_rate=0, period_end=None,
                              fallback_date=None, rounding='rupee_down'):
    """The customer's billing type is the single source of truth.

    1 monthly_prorated: unit = q2(monthly / days in month of period end);
                        amount = round(monthly x days / days in month)  -> a full month = monthly
    2 per_day:          unit = daily rate; amount = round(unit x days)
    3 monthly_fixed:    unit = None (printed blank); amount = monthly, whatever the days
    """
    if billing_type == PER_DAY:
        return calculate_line(VARIABLE, days, daily_rate=daily_rate, rounding=rounding)
    if billing_type == MONTHLY_FIXED:
        return LineCalc(q2(days), None, q2(to_decimal(monthly_amount)))
    return calculate_line('fixed', days, monthly_amount=monthly_amount, basis=CALENDAR,
                          period_end=period_end, fallback_date=fallback_date, rounding=rounding)


def calculate_for_project(project, days, period_end=None, fallback_date=None, rounding='rupee_down'):
    return calculate_by_billing_type(
        project.client.billing_type, days, monthly_amount=project.monthly_amount, daily_rate=project.daily_rate,
        period_end=period_end, fallback_date=fallback_date, rounding=rounding)


def days_in_month(d):
    import calendar
    return calendar.monthrange(d.year, d.month)[1]


def resolve_terms(mode, text, invoice_date):
    """Return (text printed under Terms, integer days or None).
    Type 1 (month_days): calendar days of the invoice date's month.
    Type 2 (custom): the text as typed; days only when it is a whole number."""
    if mode == 'month_days':
        n = days_in_month(invoice_date)
        return str(n), n
    t = (text or '').strip()
    return t, (int(t) if t.isdigit() else None)


def due_date_for(invoice_date, terms_days):
    return invoice_date + timedelta(days=int(terms_days or 0))


def build_description(base, start, end):
    """'Support' + 1 Oct..23 Oct 2026 -> 'Support - October 01 - October 23, 2026'."""
    base = (base or '').strip()
    if not (start and end):
        return base
    if start.year != end.year:
        period = f"{start:%B %d, %Y} - {end:%B %d, %Y}"
    else:
        period = f"{start:%B %d} - {end:%B %d, %Y}"
    return f'{base} - {period}' if base else period


def format_western(value):
    """100000 -> '100,000.00' (row number style in the sample)."""
    return f'{q2(value):,.2f}'


def format_indian(value):
    """409030 -> '4,09,030.00' (Indian digit grouping, used for the total)."""
    v = q2(value)
    sign = '-' if v < 0 else ''
    whole, frac = f'{abs(v):.2f}'.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups + [tail])
    return f'{sign}{whole}.{frac}'
