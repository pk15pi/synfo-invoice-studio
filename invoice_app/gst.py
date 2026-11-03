"""GST (Indian Goods and Services Tax) for tax invoices. Pure Decimal functions + one invoice helper.

POLICY (documented in README):
* Tax treatment per invoice (defaults from the customer): ``export`` (no tax, the historic behaviour),
  ``domestic_gst`` (GST charged) or ``export_lut`` (export of services under a Letter of Undertaking:
  no tax, the PDF adds an LUT note).
* Domestic GST: taxable value of a line = its amount. Place of supply in the business's own state
  (first 2 digits of the BusinessProfile GSTIN, e.g. 06 = Haryana) -> CGST = SGST = rate/2 each;
  any other state -> IGST = rate. Tax is summed per head over the lines using the exact products and
  rounded ONCE per head to paise with ROUND_HALF_UP (so CGST == SGST always).
* grand_total = taxable value + total tax. There is NO round-off to the whole rupee (the existing code
  never rounded the grand total, so none is added).
* The result is written into the existing Invoice fields (taxable_value, tax_type, tax_rate,
  cgst/sgst/igst_amount, total_tax, grand_total), which remain the single source of truth for the
  ledger. tax_rate = the line rate when every line has the same rate, otherwise the effective rate
  (total tax / taxable x 100, rounded to 2 decimals).
"""
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal('0.01')
ZERO = Decimal('0.00')

EXPORT, DOMESTIC_GST, EXPORT_LUT = 'export', 'domestic_gst', 'export_lut'
TREATMENTS = [
    (EXPORT, 'Export / No tax'),
    (DOMESTIC_GST, 'Domestic GST (tax invoice)'),
    (EXPORT_LUT, 'Export under LUT (no tax, LUT note on the PDF)'),
]
TREATMENT_KEYS = {k for k, _ in TREATMENTS}

DEFAULT_SAC = '998314'
DEFAULT_RATE = Decimal('18.00')
DEFAULT_BUSINESS_STATE = '06'
LUT_NOTE = 'Supply meant for export under LUT without payment of IGST'

STATES = {
    '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
    '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
    '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
    '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
    '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
    '25': 'Daman and Diu', '26': 'Dadra and Nagar Haveli and Daman and Diu', '27': 'Maharashtra',
    '28': 'Andhra Pradesh (old)', '29': 'Karnataka', '30': 'Goa', '31': 'Lakshadweep', '32': 'Kerala',
    '33': 'Tamil Nadu', '34': 'Puducherry', '35': 'Andaman and Nicobar Islands', '36': 'Telangana',
    '37': 'Andhra Pradesh', '38': 'Ladakh', '97': 'Other Territory', '99': 'Centre Jurisdiction',
}
STATE_CHOICES = [(k, f'{k} - {v}') for k, v in STATES.items()]

GSTIN_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def gstin_checksum(first14):
    total = 0
    for i, ch in enumerate(first14):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return _CHARS[(36 - total % 36) % 36]


def gstin_error(value):
    """None if ``value`` is a valid GSTIN, else a human-readable reason."""
    g = (value or '').strip().upper()
    if len(g) != 15:
        return 'A GSTIN has exactly 15 characters.'
    if not GSTIN_RE.match(g):
        return 'GSTIN format is 2-digit state code + 10-character PAN + entity code + Z + check character.'
    if g[:2] not in STATES:
        return f'GSTIN starts with an unknown state code ({g[:2]}).'
    if gstin_checksum(g[:14]) != g[14]:
        return 'GSTIN check character does not match (typo?).'
    return None


def validate_gstin(value):
    from django.core.exceptions import ValidationError
    err = gstin_error(value)
    if err:
        raise ValidationError(err)


def state_from_gstin(gstin):
    g = (gstin or '').strip()
    return g[:2] if len(g) >= 2 and g[:2] in STATES else ''


def business_state(profile):
    """The supplier's state code: first 2 digits of the BusinessProfile GSTIN (QuantBud: 06 Haryana)."""
    return state_from_gstin(getattr(profile, 'gstin', '')) or DEFAULT_BUSINESS_STATE


def state_label(code):
    return f'{STATES[code]} ({code})' if code in STATES else (code or '')


def q2(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_rate(rate):
    """Decimal('9.00') -> '9', Decimal('2.50') -> '2.5'."""
    s = f'{Decimal(rate):f}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


@dataclass
class GstResult:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    tax_type: str
    rate: Decimal
    intra_state: bool
    uniform: bool

    @property
    def total_tax(self):
        return self.cgst + self.sgst + self.igst

    @property
    def grand_total(self):
        return self.taxable + self.total_tax

    @property
    def half_rate(self):
        return q2(self.rate / 2)


def compute(lines, place_of_supply, supplier_state):
    """``lines`` = iterable of (amount, gst_rate %). Returns a GstResult (all values Decimal, 2 dp)."""
    lines = [(Decimal(a or 0), Decimal(r or 0)) for a, r in lines]
    taxable = q2(sum((a for a, _ in lines), Decimal('0')))
    intra = (place_of_supply or '') == (supplier_state or '')
    exact = sum((a * r for a, r in lines), Decimal('0'))  # sum of amount x rate (percent units)
    if intra:
        cgst = sgst = q2(exact / 200)
        igst = ZERO
    else:
        cgst = sgst = ZERO
        igst = q2(exact / 100)
    rates = {r for _, r in lines}
    uniform = len(rates) <= 1
    if uniform:
        rate = q2(next(iter(rates)) if rates else 0)
    else:
        rate = q2((cgst + sgst + igst) * 100 / taxable) if taxable else ZERO
    return GstResult(taxable, cgst, sgst, igst, 'cgst_sgst' if intra else 'igst', rate, intra, uniform)


def apply_to_invoice(invoice, lines, profile):
    """Write the GST of ``lines`` ((amount, rate) pairs) into the invoice's existing tax fields.
    Does not save. Callers only use it while the invoice is a draft (a finalized invoice's locked fields
    are protected by Invoice._guard_lifecycle, so a different result is refused, never stored)."""
    r = compute(lines, invoice.place_of_supply, business_state(profile))
    invoice.currency = 'INR'
    invoice.taxable_value = r.taxable
    invoice.tax_type, invoice.tax_rate = r.tax_type, r.rate
    invoice.cgst_amount, invoice.sgst_amount, invoice.igst_amount = r.cgst, r.sgst, r.igst
    invoice.total_tax = r.total_tax
    invoice.grand_total = r.grand_total
    return r


# ---- Amount in words (Indian numbering: thousand, lakh, crore) ----------------------------
_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve',
         'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two(n):
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (' ' + _ONES[n % 10] if n % 10 else ''))


def _three(n):
    h, rest = divmod(n, 100)
    parts = ([_ONES[h] + ' Hundred'] if h else []) + ([_two(rest)] if rest else [])
    return ' '.join(parts)


def int_words_indian(n):
    if n == 0:
        return 'Zero'
    crore, n = divmod(n, 10 ** 7)
    lakh, n = divmod(n, 10 ** 5)
    thousand, n = divmod(n, 1000)
    parts = []
    if crore:
        parts.append(int_words_indian(crore) + ' Crore')
    if lakh:
        parts.append(_two(lakh) + ' Lakh')
    if thousand:
        parts.append(_two(thousand) + ' Thousand')
    if n:
        parts.append(_three(n))
    return ' '.join(parts)


def amount_in_words(value):
    """Decimal('118000.50') -> 'Indian Rupees One Lakh Eighteen Thousand and Fifty Paise Only'."""
    v = q2(value)
    rupees = int(v)
    paise = int((v - rupees) * 100)
    s = 'Indian Rupees ' + int_words_indian(rupees)
    if paise:
        s += ' and ' + _two(paise) + ' Paise'
    return s + ' Only'