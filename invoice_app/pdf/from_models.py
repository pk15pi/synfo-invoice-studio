"""Adapter: Django models -> InvoiceData (keeps the renderer free of ORM code)."""
from decimal import Decimal

from .. import gst
from .render import InvoiceData, LineData, DEFAULT_SIGNATURE


def profile_colors(profile):
    return {
        'primary': profile.primary_color, 'alt_row': profile.alternate_row_color,
        'white_row': profile.white_row_color, 'border': profile.border_color,
        'label': profile.label_color,
    }


def signature_path(profile):
    if profile.signature_image:
        try:
            return profile.signature_image.path
        except Exception:
            pass
    return str(DEFAULT_SIGNATURE) if DEFAULT_SIGNATURE.is_file() else None


def gst_extras(invoice, profile, lines):
    """Optional InvoiceData kwargs for LUT / Domestic GST invoices ({} for plain export)."""
    client = invoice.client
    treatment = getattr(invoice, 'tax_treatment', gst.EXPORT) or gst.EXPORT
    if treatment == gst.EXPORT_LUT:
        lut = (getattr(client, 'lut_number', '') or '').strip()
        return {'lut_note': gst.LUT_NOTE + (f' (LUT No. {lut})' if lut else '')}
    if treatment != gst.DOMESTIC_GST:
        return {}
    rates = {Decimal(getattr(l, 'gst_rate', 0) or 0) for l in lines}
    uniform = len(rates) == 1
    rate = rates.pop() if uniform else None
    rows = [('Taxable Value', invoice.taxable_value or Decimal('0.00'))]
    if invoice.tax_type == 'igst':
        rows.append((f'IGST @ {gst.fmt_rate(rate)}%' if uniform else 'IGST', invoice.igst_amount))
    else:
        half = f' @ {gst.fmt_rate(rate / 2)}%' if uniform else ''
        rows += [(f'CGST{half}', invoice.cgst_amount), (f'SGST{half}', invoice.sgst_amount)]
    rows.append(('Total Tax', invoice.total_tax))
    return dict(
        tax_invoice=True,
        customer_gstin=(client.gstin or '').upper(),
        customer_state=gst.state_label(client.state_code) if client.state_code else '',
        place_of_supply=gst.state_label(invoice.place_of_supply) if invoice.place_of_supply else '',
        tax_rows=rows, grand_total=invoice.grand_total,
        amount_words=gst.amount_in_words(invoice.grand_total),
    )


def build_invoice_data(invoice, profile, lines=None, number=None):
    client = invoice.client
    if lines is None:
        lines = list(invoice.lines.all())
    return InvoiceData(**gst_extras(invoice, profile, lines),
        number=number or invoice.invoice_number,
        invoice_date=invoice.invoice_date, terms_days=invoice.terms_display, due_date=invoice.due_date,
        currency=invoice.currency or 'INR', total=invoice.total,
        business_name=profile.name, business_address_lines=(profile.address or '').splitlines(),
        phone=profile.phone, email=profile.email, gstin=profile.gstin,
        bank=dict(bank_name=profile.bank_name, account_holder=profile.account_holder,
                  account_number=profile.account_number, bank_country=profile.bank_country, ifsc=profile.ifsc),
        client_name=client.name, client_address_lines=(client.address or '').splitlines(),
        lines=[LineData(l.project_name, l.description, l.quantity_days, l.unit_price, l.amount,
                        getattr(l, 'sac_code', '') or '') for l in lines],
        signature_path=signature_path(profile), signature_name=profile.signature_text or profile.name,
        footer_text=profile.footer_text, colors=profile_colors(profile),
    )
