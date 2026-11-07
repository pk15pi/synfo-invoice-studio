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
    WORKFLOW_STATUSES = [
        ('uploaded', 'Uploaded'), ('extracted', 'Extracted'), ('draft_ready', 'Draft ready'),
        ('under_review', 'Under review'), ('partially_approved', 'Partially approved'),
        ('approved', 'Approved for import'), ('importing', 'Importing'), ('imported', 'Imported'),
        ('verification_failed', 'Verification failed'), ('completed', 'Completed'),
        ('cancelled', 'Cancelled'), (PENDING, 'Pending review'),
    ]
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='imports')
    source_batch = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='versions', help_text='Original upload batch for a regenerated draft.')
    version_number = models.PositiveIntegerField(default=1)
    file_name = models.CharField(max_length=200)
    status = models.CharField(max_length=24, choices=WORKFLOW_STATUSES, default=PENDING)
    raw_rows = models.JSONField(default=list, blank=True)
    mapping = models.JSONField(default=dict, blank=True)
    source_kind = models.CharField(max_length=8, choices=[('csv', 'CSV'), ('xlsx', 'Excel'), ('pdf', 'PDF')], blank=True)
    original_file = models.FileField(upload_to='bank_statements/original/%Y/%m/', blank=True)
    generated_csv = models.FileField(upload_to='bank_statements/extracted/%Y/%m/', blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    bank_name = models.CharField(max_length=100, blank=True)
    masked_account = models.CharField(max_length=40, blank=True)
    detected_start_date = models.DateField(null=True, blank=True)
    detected_end_date = models.DateField(null=True, blank=True)
    confirmed_start_date = models.DateField(null=True, blank=True)
    confirmed_end_date = models.DateField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    statement_warnings = models.JSONField(default=list, blank=True)
    extraction_metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='bank_import_batches')
    accounting_migration_status = models.CharField(max_length=16, default='deferred')
    cancelled_by = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='cancelled_bank_import_batches')
    cancelled_by_label = models.CharField(max_length=150, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_from_status = models.CharField(max_length=24, blank=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)
    regeneration_summary = models.JSONField(default=dict, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    extracted_transactions = models.PositiveIntegerField(default=0)
    matched = models.PositiveIntegerField(default=0)
    new_transactions = models.PositiveIntegerField(default=0)
    possible_duplicates = models.PositiveIntegerField(default=0)
    proposed_modifications = models.PositiveIntegerField(default=0)
    failed_extractions = models.PositiveIntegerField(default=0)
    imported = models.PositiveIntegerField(default=0)
    modified = models.PositiveIntegerField(default=0)
    verified = models.PositiveIntegerField(default=0)
    duplicates = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    error_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['bank_account', 'file_sha256'],
                                    condition=~models.Q(file_sha256='') & models.Q(source_batch__isnull=True),
                                    name='bank_import_file_digest_unique'),
            models.UniqueConstraint(fields=['source_batch', 'version_number'],
                                    condition=models.Q(source_batch__isnull=False),
                                    name='bank_import_source_version_unique'),
        ]


class ImportDraftRow(models.Model):
    """A reviewable source row kept separate from the live bank transaction register."""
    EXACT = 'exact_match'
    NEW = 'new_transaction'
    POSSIBLE = 'possible_duplicate'
    MODIFY = 'modification_required'
    ACCOUNTING = 'accounting_link'
    UNCERTAIN = 'uncertain'
    NON_TRANSACTION = 'non_transaction'
    CLASSIFICATIONS = [
        (EXACT, 'Already exists - exact match'), (NEW, 'New transaction - entry required'),
        (POSSIBLE, 'Possible duplicate - review'), (MODIFY, 'Modification proposed'),
        (ACCOUNTING, 'Existing accounting record - link review'), (UNCERTAIN, 'Extraction uncertain'),
        (NON_TRANSACTION, 'Non-transaction row'),
    ]
    PENDING, APPROVED, EXCLUDED, IMPORTED, VERIFIED, FAILED = (
        'pending', 'approved', 'excluded', 'imported', 'verified', 'failed')
    REVIEW_STATUSES = [(s, s.replace('_', ' ').title()) for s in (PENDING, APPROVED, EXCLUDED, IMPORTED, VERIFIED, FAILED)]
    VERIFY_STATUSES = [('pending', 'Pending'), ('verified', 'Verified'), ('failed', 'Failed')]
    MIGRATION_CATEGORIES = [
        ('expense', 'Company expense'), ('vendor_payment', 'Vendor payment'), ('salary', 'Employee salary'),
        ('reimbursement', 'Employee reimbursement'), ('customer_receipt', 'Customer receipt'), ('tax', 'Tax payment'),
        ('bank_transfer', 'Bank transfer'), ('journal', 'Journal settlement'), ('asset', 'Asset purchase'),
        ('loan', 'Loan transaction'), ('capital', 'Capital contribution/drawing'), ('other', 'Other existing module'),
        ('review', 'Manual review required'),
    ]

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='draft_rows')
    row_number = models.PositiveIntegerField()
    fingerprint_occurrence = models.PositiveIntegerField(default=0)
    source_page = models.PositiveIntegerField(null=True, blank=True)
    transaction_date = models.DateField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=500, blank=True)
    normalized_description = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    raw_source = models.JSONField(default=dict, blank=True)
    extraction_error = models.CharField(max_length=500, blank=True)
    regeneration_change = models.CharField(max_length=12, blank=True,
                                           choices=[('new', 'New in regenerated draft'),
                                                    ('changed', 'Changed since previous draft'),
                                                    ('unchanged', 'Unchanged from previous draft')])
    classification = models.CharField(max_length=24, choices=CLASSIFICATIONS, default=UNCERTAIN)
    justification = models.CharField(max_length=500, blank=True)
    existing_transaction = models.ForeignKey('BankTransaction', null=True, blank=True, on_delete=models.PROTECT, related_name='import_drafts')
    proposed_changes = models.JSONField(default=dict, blank=True)
    review_status = models.CharField(max_length=12, choices=REVIEW_STATUSES, default=PENDING)
    decision = models.CharField(max_length=12, blank=True)
    actual_transaction = models.ForeignKey('BankTransaction', null=True, blank=True, on_delete=models.PROTECT, related_name='import_sources')
    verification_status = models.CharField(max_length=10, choices=VERIFY_STATUSES, default='pending')
    verification_note = models.CharField(max_length=500, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    migration_classification = models.CharField(max_length=24, choices=MIGRATION_CATEGORIES, blank=True)
    migration_status = models.CharField(max_length=12, default='deferred')
    migration_justification = models.CharField(max_length=500, blank=True)
    before_values = models.JSONField(default=dict, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['row_number', 'id']
        constraints = [models.UniqueConstraint(fields=['batch', 'row_number'], name='bank_import_draft_row_unique')]
        indexes = [models.Index(fields=['batch', 'classification', 'review_status'], name='bank_draft_review_idx')]


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
