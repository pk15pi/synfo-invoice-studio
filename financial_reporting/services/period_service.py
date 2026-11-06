from datetime import date

from accounting.models import FinancialYear
from reporting import periods as shared_periods

from financial_reporting.models import FinancialPeriod


def resolve_period(params=None):
    return shared_periods.resolve(params or {}, default='this_quarter')


def persisted_period_type(period):
    return {
        'month': 'month',
        'quarter': 'quarter',
        'half': 'half_year',
        'year': 'annual',
        'fy': 'financial_year',
    }.get(period.kind, 'custom')


def get_or_create_period(period):
    start_year = shared_periods.fy_start_year(period.start)
    fy_start, fy_end = shared_periods.fy_range(start_year)
    financial_year = None
    if period.start >= fy_start and period.end <= fy_end:
        financial_year = FinancialYear.objects.filter(start_year=start_year).first()
    obj, _ = FinancialPeriod.objects.get_or_create(
        period_type=persisted_period_type(period),
        start_date=period.start,
        end_date=period.end,
        defaults={'name': period.label[:120], 'financial_year': financial_year},
    )
    return obj


def financial_year_dates(start_year):
    if not isinstance(start_year, int) or not 1900 <= start_year <= 9998:
        raise ValueError('Financial year start must be an integer from 1900 through 9998.')
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)
