"""Write a sample FY workbook and a sample PDF report from the current database (read-only)."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from reporting import exports, periods as P, reports


class Command(BaseCommand):
    help = 'Write test_output/sample_fy_report.xlsx and test_output/sample_report.pdf (read-only).'

    def add_arguments(self, parser):
        parser.add_argument('--fy', type=int, help='FY start year (default: current FY)')
        parser.add_argument('--report', default='pnl', help='Report key for the PDF (default pnl)')
        parser.add_argument('--out', default=str(Path(settings.BASE_DIR) / 'test_output'))

    def handle(self, *a, **o):
        y = o['fy'] or P.fy_start_year(P.today())
        out = Path(o['out'])
        out.mkdir(parents=True, exist_ok=True)
        (out / 'sample_fy_report.xlsx').write_bytes(exports.fy_workbook(y))
        rep = reports.build(o['report'], {'period': 'fy', 'fy': str(y)})
        (out / 'sample_report.pdf').write_bytes(exports.report_pdf(rep))
        self.stdout.write(self.style.SUCCESS(f'Wrote {out / "sample_fy_report.xlsx"} and {out / "sample_report.pdf"} '
                                             f'for {P.fy_label(y)}'))
