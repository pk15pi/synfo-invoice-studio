"""Indian-system number to words (lakh / crore)."""
from decimal import ROUND_HALF_UP, Decimal

ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve',
        'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two(n):
    return ONES[n] if n < 20 else (TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else ''))


def _three(n):
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(ONES[h] + ' Hundred')
    if r:
        parts.append(_two(r))
    return ' '.join(parts)


def int_words(n):
    if n == 0:
        return 'Zero'
    parts = []
    crore, n = divmod(n, 10_000_000)
    if crore:
        parts.append(int_words(crore) + ' Crore')
    lakh, n = divmod(n, 100_000)
    if lakh:
        parts.append(_two(lakh) + ' Lakh')
    th, n = divmod(n, 1000)
    if th:
        parts.append(_two(th) + ' Thousand')
    if n:
        parts.append(_three(n))
    return ' '.join(parts)


def rupees_in_words(amount):
    amount = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    neg = amount < 0
    amount = abs(amount)
    r = int(amount)
    p = int((amount - r) * 100)
    s = 'Rupees ' + int_words(r)
    if p:
        s += ' and ' + _two(p) + ' Paise'
    s += ' Only'
    return ('Minus ' if neg else '') + s
