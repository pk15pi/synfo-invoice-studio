"""Post pre-Stage-4 invoices, their receipts and credit notes to the ledger.

DRY-RUN BY DEFAULT: prints what would be posted and changes nothing. ``--apply`` posts (idempotent: already
posted documents are skipped). ``--as-of YYYY-MM-DD`` limits it to invoices / receipts / credit notes dated on
or before that date. Cancelled invoices are never posted.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting import postings
from accounting.money import ZERO
from invoice_app.models import CreditNote, Invoice


def _tot(lines):
    lines = postings._norm(lines)
    return sum((l['debit'] for l in lines), ZERO)


class Command(BaseCommand):
    help = 'Post existing finalized invoices and their receipts to the ledger (dry-run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually post (default: dry-run).')
        parser.add_argument('--as-of', help='Only documents dated on or before YYYY-MM-DD.')

    def handle(self, *args, **o):
        as_of = None
        if o.get('as_of'):
            try:
                as_of = date.fromisoformat(o['as_of'])
            except ValueError:
                raise CommandError('--as-of must be YYYY-MM-DD')
        apply = o['apply']
        w = self.stdout.write
        w(f"{'APPLY' if apply else 'DRY-RUN (nothing is written; use --apply to post)'}"
          f"{f' as of {as_of}' if as_of else ''}")
        qs = Invoice.objects.filter(status=Invoice.FINALIZED).select_related('client').order_by('invoice_date', 'id')
        if as_of:
            qs = qs.filter(invoice_date__lte=as_of)
        n = {'invoice': 0, 'receipt': 0, 'credit_note': 0, 'skipped': 0, 'already': 0}
        for inv in qs:
            try:
                lines = postings.invoice_lines(inv)
            except ValidationError as e:
                n['skipped'] += 1
                w(f'  SKIP {inv.invoice_number}: {"; ".join(e.messages)}')
                continue
            docs = []
            if postings.is_posted(postings.invoice_key(inv)):
                n['already'] += 1
            else:
                docs.append(('invoice', inv, _tot(lines)))
            for p in inv.payments.order_by('receipt_date', 'id'):
                if (as_of is None or p.receipt_date <= as_of) and not postings.is_posted(postings.receipt_key(p)):
                    docs.append(('receipt', p, _tot(postings.receipt_lines(p))))
            for c in inv.credit_notes.filter(status=CreditNote.ACTIVE).order_by('date', 'id'):
                if (as_of is None or c.date <= as_of) and not postings.is_posted(postings.credit_note_key(c)):
                    docs.append(('credit_note', c, _tot(postings.credit_note_lines(c))))
            for kind, obj, amt in docs:
                n[kind] += 1
                when = getattr(obj, 'invoice_date', None) or getattr(obj, 'receipt_date', None) or obj.date
                w(f'  {kind:<11} {inv.invoice_number:<14} {when}  {amt:>14,.2f}')
            if apply and docs:
                try:
                    with transaction.atomic():
                        postings.sync(postings.invoice_key(inv), inv.invoice_date, f'Invoice {inv.invoice_number} - {inv.client}',
                                      lines, 'invoice', inv.pk)
                        for kind, obj, amt in docs:
                            if kind == 'receipt':
                                postings.post_receipt(obj)
                            elif kind == 'credit_note':
                                postings.post_credit_note(obj)
                except ValidationError as e:
                    w(f'  FAILED {inv.invoice_number}: {"; ".join(e.messages)}')
        w(f"Invoices to post: {n['invoice']}, receipts: {n['receipt']}, credit notes: {n['credit_note']}, "
          f"already posted invoices: {n['already']}, skipped: {n['skipped']}"
          f"{' (posted)' if apply else ' (dry-run, nothing posted)'}")
