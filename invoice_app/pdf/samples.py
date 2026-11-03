"""Unsaved dummy invoices for template previews, sample PNGs and tests.

Nothing here touches the database or the invoice sequence: every object is an
unsaved model instance, and the invoice number is a fixed placeholder."""
from datetime import date
from decimal import Decimal as D

from .. import gst
from ..models import Client, Invoice, InvoiceLine

SAMPLE_NUMBER = 'SAMPLE-001'
KINDS = ('export', 'lut', 'gst_intra', 'gst_inter')


def _gstin(state, pan='AAACI1234A'):
    first14 = f'{state}{pan}1Z'
    return first14 + gst.gstin_checksum(first14)


def sample_invoice(kind='gst_intra', number=SAMPLE_NUMBER):
    """(invoice, lines) for kind in KINDS. Amounts are fixed, already-computed figures."""
    if kind in ('export', 'lut'):
        c = Client(name='Acme Analytics Inc.', address='1200 Market Street, Suite 400\nSan Francisco, CA\nUSA - 94103',
                   country='USA', tax_treatment=gst.EXPORT_LUT if kind == 'lut' else gst.EXPORT,
                   lut_number='AD060326000001' if kind == 'lut' else '')
    else:
        state = '06' if kind == 'gst_intra' else '29'
        c = Client(name='Northwind Technologies Pvt Ltd', address='Plot 14, Sector 44\nGurugram, Haryana\nIndia - 122003'
                   if state == '06' else '5th Floor, MG Road\nBengaluru, Karnataka\nIndia - 560001',
                   country='India', tax_treatment=gst.DOMESTIC_GST, gstin=_gstin(state), state_code=state)
    c.vendor_code = 'V-20931'
    lines = [
        InvoiceLine(project_name='Ravi Kumar - Data Platform', description='Data engineering services, October 2026',
                    quantity_days=D('23.00'), from_date=date(2026, 10, 1), to_date=date(2026, 10, 31),
                    unit_price=D('3225.80'), amount=D('74193.00'), sac_code='998314', gst_rate=D('18')),
        InvoiceLine(project_name='Priya Sharma - Analytics', description='BI dashboard support',
                    quantity_days=D('10.00'), from_date=date(2026, 10, 6), to_date=date(2026, 10, 17),
                    unit_price=D('4000.00'), amount=D('40000.00'), sac_code='998314', gst_rate=D('18')),
    ]
    total = D('114193.00')
    inv = Invoice(client=c, invoice_number=number, invoice_date=date(2026, 11, 2), terms_mode='custom',
                  terms_text='31', terms_days=31, due_date=date(2026, 12, 3), billing_month=date(2026, 10, 1),
                  currency='INR', total=total, taxable_value=total, tax_treatment=c.tax_treatment)
    inv.po_number = 'PO-4500012345'
    if kind == 'gst_intra':
        inv.place_of_supply, inv.tax_type, inv.tax_rate = '06', Invoice.TAX_CGST_SGST, D('18')
        inv.cgst_amount = inv.sgst_amount = D('10277.37')
    elif kind == 'gst_inter':
        inv.place_of_supply, inv.tax_type, inv.tax_rate = '29', Invoice.TAX_IGST, D('18')
        inv.igst_amount = D('20554.74')
    inv.total_tax = (inv.cgst_amount or 0) + (inv.sgst_amount or 0) + (inv.igst_amount or 0)
    inv.grand_total = inv.taxable_value + inv.total_tax
    return inv, lines
