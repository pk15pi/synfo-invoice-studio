"""Post existing expenses, vendor payments, approved claims and reimbursements to the ledger.

Dry-run by default (prints what would be posted, writes nothing). ``--apply`` posts, each document in its
own transaction, through expense_management.ledger (idempotent: already-posted documents are skipped)."""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.postings import is_posted
from expense_management import ledger
from expense_management.models import CompanyExpense, CompanyExpensePayment, EmployeeExpenseClaim, EmployeeReimbursement


class Command(BaseCommand):
    help = 'Backfill ledger postings for expense_management (dry-run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually post (default: dry-run).')

    def handle(self, *args, apply=False, **kw):
        w = self.stdout.write
        w('APPLY' if apply else 'DRY-RUN (nothing is written; use --apply to post)')
        groups = [
            ('expenses', CompanyExpense.objects.filter(is_draft=False).exclude(payment_status=CompanyExpense.CANCELLED).order_by('id'),
             ledger.expense_key, ledger.post_expense, lambda o: f'{o.expense_id} {o.expense_date} {o.total_inr}'),
            ('vendor payments', CompanyExpensePayment.objects.select_related('expense').order_by('id'),
             ledger.payment_key, ledger.post_payment,
             lambda o: f'{o.expense.expense_id} {o.payment_date} {o.amount} tds {o.tds_amount}'),
            ('claims', EmployeeExpenseClaim.objects.filter(status__in=EmployeeExpenseClaim.APPROVED_STATES)
             .order_by('id'), ledger.claim_key, ledger.post_claim,
             lambda o: f'{o.claim_id} {o.expense_date} {o.approved_amount} {o.currency}'),
            ('reimbursements', EmployeeReimbursement.objects.select_related('claim').order_by('id'),
             ledger.reimbursement_key, ledger.post_reimbursement,
             lambda o: f'{o.claim.claim_id} {o.reimbursement_date} {o.amount}'),
        ]
        summary, errors = [], 0
        for name, qs, key, post, label in groups:
            todo = done = 0
            for o in qs:
                if is_posted(key(o)):
                    done += 1
                    continue
                todo += 1
                w(f'  {name}: {key(o)} {label(o)}')
                if apply:
                    try:
                        with transaction.atomic():
                            post(o)
                    except Exception as e:  # report and continue with the next document
                        errors += 1
                        w(f'    ERROR: {e}')
            summary.append(f'{name} to post: {todo} (already posted: {done})')
        w('; '.join(summary) + (f'; errors: {errors}' if apply else ' (dry-run, nothing posted)'))
