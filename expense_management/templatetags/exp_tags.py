from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()
SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£'}


def indian_group(n):
    s = f'{abs(n):.2f}'
    whole, frac = s.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts + [tail])
    return ('-' if n < 0 else '') + whole + '.' + frac


@register.filter
def money(value, currency='INR'):
    if value is None or value == '':
        return ''
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    cur = currency or 'INR'
    if cur == 'INR':
        return '₹' + indian_group(d)
    return f'{SYMBOLS.get(cur, cur + " ")}{d:,.2f}'


@register.filter
def cell(value, is_money=False):
    import datetime
    if isinstance(value, datetime.date):
        return value.strftime('%d-%m-%Y')
    if isinstance(value, Decimal):
        return money(value) if is_money else f'{value:.2f}'
    return value


@register.filter
def contains(container, item):
    return item in container
