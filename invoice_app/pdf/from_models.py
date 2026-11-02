"""Adapter: Django models -> InvoiceData (keeps the renderer free of ORM code)."""
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


def build_invoice_data(invoice, profile, lines=None, number=None):
    client = invoice.client
    if lines is None:
        lines = list(invoice.lines.all())
    return InvoiceData(
        number=number or invoice.display_number,
        invoice_date=invoice.invoice_date, terms_days=invoice.terms_days, due_date=invoice.due_date,
        currency=invoice.currency or 'INR', total=invoice.total,
        business_name=profile.name, business_address_lines=(profile.address or '').splitlines(),
        phone=profile.phone, email=profile.email, gstin=profile.gstin,
        bank=dict(bank_name=profile.bank_name, account_holder=profile.account_holder,
                  account_number=profile.account_number, bank_country=profile.bank_country, ifsc=profile.ifsc),
        client_name=client.name, client_address_lines=(client.address or '').splitlines(),
        lines=[LineData(l.project_name, l.description, l.quantity_days, l.unit_price, l.amount) for l in lines],
        signature_path=signature_path(profile), signature_name=profile.signature_text or profile.name,
        footer_text=profile.footer_text, colors=profile_colors(profile),
    )
