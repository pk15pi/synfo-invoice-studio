from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.context import audit_context
from backup.services import verify_zip
from bank_management.models import ReconciliationMatch
from bank_management.services import unmatch as unmatch_bank_row
from expense_management import ledger
from expense_management.models import (CompanyExpense, CompanyExpensePayment, EmployeeExpenseClaim,
                                       EmployeeReimbursement)


CONFIRMATION = 'RESET EXPENSE PAYMENTS AND DRAFT ALL EXPENSES'
RESET_REASON = 'Expense book reset to editable drafts; payment evidence retained in verified pre-reset backup.'


class Command(BaseCommand):
    help = 'Reverse expense book payment postings, unmatch bank rows, and return all expenses to editable drafts.'

    def add_arguments(self, parser):
        parser.add_argument('--backup', required=True, help='Path to the verified pre-reset ZIP backup.')
        parser.add_argument('--confirm', default='', help=f'Required exact phrase: {CONFIRMATION}')

    def handle(self, *args, backup, confirm, **options):
        if confirm != CONFIRMATION:
            raise CommandError(f'Confirmation required: --confirm "{CONFIRMATION}"')
        backup_path = Path(backup)
        ok, problems, _manifest = verify_zip(backup_path)
        if not ok:
            raise CommandError('Backup did not verify: ' + '; '.join(problems))

        expenses = list(CompanyExpense.objects.order_by('pk'))
        payments = list(CompanyExpensePayment.objects.select_related('expense').order_by('pk'))
        claims = list(EmployeeExpenseClaim.objects.order_by('pk'))
        reimbursements = list(EmployeeReimbursement.objects.select_related('claim').order_by('pk'))
        matches = list(ReconciliationMatch.objects.filter(
            active=True, journal_line__entry__source_type__in=('expense_payment', 'reimbursement')
        ).select_related('transaction', 'journal_line__entry').order_by('pk'))
        reason = RESET_REASON

        with audit_context(source='command', path='manage.py reset_expense_book_to_drafts', reason=reason):
            with transaction.atomic():
                for match in matches:
                    unmatch_bank_row(match, reason)

                for payment in payments:
                    ledger.unpost(ledger.payment_key(payment), reason)
                for reimbursement in reimbursements:
                    ledger.unpost(ledger.reimbursement_key(reimbursement), reason)
                for expense in expenses:
                    ledger.unpost(ledger.expense_key(expense), reason)
                for claim in claims:
                    ledger.unpost(ledger.claim_key(claim), reason)

                # QuerySet deletion is intentional here: the ordinary model delete is disabled for payment history.
                # The verified full backup preserves the original rows; ledger reversals and audit events are kept.
                CompanyExpensePayment.objects.all().delete()
                EmployeeReimbursement.objects.all().delete()

                for expense in expenses:
                    expense.payment_status = CompanyExpense.UNPAID
                    expense.is_draft = True
                    expense.save(update_fields=('payment_status', 'is_draft', 'updated_at'))
                for claim in claims:
                    if claim.status != EmployeeExpenseClaim.DRAFT:
                        claim.returned_for_correction = True
                    claim.status = EmployeeExpenseClaim.DRAFT
                    claim.approved_amount = None
                    claim.save(update_fields=('status', 'approved_amount', 'returned_for_correction', 'updated_at'))

        self.stdout.write(self.style.SUCCESS(
            f'Reset complete: {len(expenses)} company expenses and {len(claims)} employee claims are drafts; '
            f'{len(payments)} vendor payments and {len(reimbursements)} reimbursements were removed; '
            f'{len(matches)} bank matches were released. Imported bank statement rows and posted-journal history remain.'
        ))
