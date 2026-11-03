"""Shared money rounding: Decimal, ROUND_HALF_UP, 2 decimal places. New code should use this.
(Existing apps keep their own helpers for now; they are not refactored in this stage.)"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TWO = Decimal('0.01')
ZERO = Decimal('0.00')


def D(x):
    if x is None or x == '':
        return Decimal('0')
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        x = repr(x)
    try:
        return Decimal(str(x).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'Not a valid amount: {x!r}')


def q2(x):
    return D(x).quantize(TWO, rounding=ROUND_HALF_UP)


def money_sum(values):
    return q2(sum((D(v) for v in values), Decimal('0')))
