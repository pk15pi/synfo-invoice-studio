"""Company bank and cash accounts, statement transactions, imports, reconciliation matches, transfers."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from accounting.models import Account, JournalEntry, JournalLine

MONEY = dict(max_digits=14, decimal_places=2, default=0)


def mask_number(n):
    n = (n or '').replace(' ', '')
    return f'XXXX{n[-4:]}' if n else ''


class BankAccount(models.Model):
    BANK, CASH = 'bank', 'cash'
    KINDS = [(BANK, 'Bank'), (CASH, 'Cash')]
    kind = models.CharField(max_length=4, choices=KINDS, default=BANK)
    name = models.CharField('Display name', max_length=100, unique=True)
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=34, blank=True, help_text='Stored in full; shown masked except on the detail page.')
    ifsc = models.CharField('IFSC', max_length=11, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    ledger_account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name='bank_account')
    opening_balance = models.DecimalField(**MONEY)
    opening_date = models.DateField(null=True, blank=True)
    opening_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    active = models.BooleanField(default=True)
    is_default = models.BooleanField('Default account', default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', 'kind', 'name']

    @property
    def masked_number(self):
        return mask_number(self.account_number)

    def __str__(self):
        return f'{self.name} ({self.masked_number})' if self.account_number else self.name

    def clean(self):
        if self.ifsc:
            self.ifsc = self.ifsc.strip().upper()
            import re
            if not re.fullmatch(r'[A-Z]{4}0[A-Z0-9]{6}', self.ifsc):
                raise ValidationError({'ifsc': 'IFSC must look like ABCD0123456.'})
        if self.account_number and not self.account_number.replace(' ', '').isalnum():
            raise ValidationError({'account_number': 'Letters and digits only.'})
        if self.opening_balance and not self.opening_date:
            raise ValidationError({'opening_date': 'Opening balance needs a date.'})

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.is_default:
            BankAccount.objects.filter(kind=self.kind, is_default=True).exclude(pk=self.pk).update(is_default=False)


class ImportBatch(models.Model):
    PENDING, DONE = 'pending', 'imported'
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='imports')
    file_name = models.CharField(max_length=200)
    status = models.CharField(max_length=10, default=PENDING)
    raw_rows = models.JSONField(default=list, blank=True)
    mapping = models.JSONField(default=dict, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    imported = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    error_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']


class BankTransaction(models.Model):
    UNRECONCILED, PARTIAL, MATCHED, IGNORED = 'unreconciled', 'partial', 'matched', 'ignored'
    STATUSES = [(UNRECONCILED, 'Unreconciled'), (PARTIAL, 'Partially matched'), (MATCHED, 'Matched'), (IGNORED, 'Ignored')]
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='transactions')
    date = models.DateField(db_index=True)
    value_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    debit = models.DecimalField('Withdrawal (debit)', **MONEY)
    credit = models.DecimalField('Deposit (credit)', **MONEY)
    balance = models.DecimalField('Statement balance', max_digits=14, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=6, choices=[('manual', 'Manual'), ('import', 'Import')], default='manual')
    batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='transactions')
    row_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=UNRECONCILED, db_index=True)
    ignore_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['bank_account', 'date'], name='bank_txn_acct_date_idx')]

    @property
    def amount(self):
        """Signed: deposits positive, withdrawals negative (same sign as the bank ledger debit - credit)."""
        return self.credit - self.debit

    def __str__(self):
        return f'{self.date:%d-%m-%Y} {self.description[:40]} {self.amount}'


class ReconciliationMatch(models.Model):
    transaction = models.ForeignKey(BankTransaction, on_delete=models.PROTECT, related_name='matches')
    journal_line = models.ForeignKey(JournalLine, on_delete=models.PROTECT, related_name='bank_matches')
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text='Absolute amount matched')
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    unmatched_at = models.DateTimeField(null=True, blank=True)
    unmatch_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']


class BankTransfer(models.Model):
    date = models.DateField()
    from_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='transfers_out')
    to_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='transfers_in')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    narration = models.CharField(max_length=300, blank=True)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name='bank_transfer')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
