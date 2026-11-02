"""Render the QB020 reference sample to test_output/QB020_test.pdf.

Builds UNSAVED Client/Invoice/InvoiceLine objects, so no invoice row is written and the
real invoice sequence is NOT consumed. It does make sure the singleton BusinessProfile
exists (seeding the QuantBud defaults on a fresh database)."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from invoice_app.models import BusinessProfile, Client, Invoice, InvoiceLine
from invoice_app.pdf import render_invoice_pdf
from invoice_app.pdf.from_models import build_invoice_data

D = Decimal
# Values exactly as supplied for the QB020 test (unit price / amount given, not recomputed).
SAMPLE_LINES = [
    ('Mohammad - John Hopkins', 'Support - October 01 - October 23, 2026', '23', '3225.80', '74193.00'),
    ('Nikunj - State of Utah', 'Support - October 01 - October 31, 2026', '31', '3548.39', '110000.00'),
    ('Propero LLC', 'Support - October 09 - October 31, 2026', '23', '3548.39', '81612.00'),
    ('Vervint', 'Support - September 25 - October 31, 2026', '37', '3870.97', '143225.00'),
]


class Command(BaseCommand):
    help = 'Write test_output/QB020_test.pdf from the QB020 sample data without consuming a number.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default=str(Path(settings.BASE_DIR) / 'test_output' / 'QB020_test.pdf'))
        parser.add_argument('--defaults', action='store_true',
                            help='Use built-in profile defaults instead of the saved Business profile.')

    def handle(self, *args, **opts):
        profile = BusinessProfile() if opts['defaults'] else BusinessProfile.current()
        before = profile.next_invoice_number
        client = Client(name='American Chase LLC', address='3055 NW Yeon Ave # 530\nPortland, Oregon\nUSA - 97210',
                        country='USA')
        lines = [InvoiceLine(project_name=n, description=d, quantity_days=D(q), unit_price=D(u), amount=D(a))
                 for n, d, q, u, a in SAMPLE_LINES]
        invoice = Invoice(client=client, invoice_number='QB020', invoice_date=date(2026, 11, 2), terms_days=31,
                          due_date=date(2026, 12, 5),  # as supplied; 2 Nov + 31 days would be 3 Dec
                          total=sum((l.amount for l in lines), D('0')))
        out = Path(opts['out'])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(render_invoice_pdf(build_invoice_data(invoice, profile, lines=lines)))
        if not opts['defaults']:
            profile.refresh_from_db()
            assert profile.next_invoice_number == before, 'sequence changed!'
        self.stdout.write(self.style.SUCCESS(f'Wrote {out} (total {invoice.total}; sequence untouched at {before})'))
