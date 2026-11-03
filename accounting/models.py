"""Accounting foundation: financial years/periods, chart of accounts, double-entry journal, account mappings.

Posted journal entries are immutable: save()/delete() refuse changes (and a pre_delete signal also covers
queryset deletes). Corrections are made with services.reverse(). Bulk ``QuerySet.update()`` bypasses model
code by Django design - only accounting.services uses it, for the posted -> reversed status flag."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

MONEY = dict(max_digits=14, decimal_places=2, default=0)


class ImmutableError(ValidationError):
    pass


class EntryQuerySet(models.QuerySet):
    def delete(self):
        if self.exclude(status='draft').exists():
            raise ImmutableError('Posted journal entries cannot be deleted. Reverse them instead.')
        return super().delete()


class LineQuerySet(models.QuerySet):
    def delete(self):
        if self.exclude(entry__status='draft').exists():
            raise ImmutableError('Lines of a posted journal entry cannot be deleted.')
        return super().delete()


class FinancialYear(models.Model):
    start_year = models.PositiveIntegerField(unique=True, help_text='FY starts 1 April of this year')
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ['-start_year']

    @property
    def name(self):
        return f'FY {self.start_year}-{str(self.start_year + 1)[-2:]}'

    @property
    def code(self):
        return f'{self.start_year}-{str(self.start_year + 1)[-2:]}'

    def __str__(self):
        return self.name


class FinancialPeriod(models.Model):
    OPEN, CLOSED = 'open', 'closed'
    STATUSES = [(OPEN, 'Open'), (CLOSED, 'Closed')]
    financial_year = models.ForeignKey(FinancialYear, on_delete=models.PROTECT, related_name='periods')
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=6, choices=STATUSES, default=OPEN, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=300, blank=True)
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopen_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['year', 'month']
        constraints = [models.UniqueConstraint(fields=['year', 'month'], name='acc_period_year_month_uniq')]
        indexes = [models.Index(fields=['start_date', 'end_date'], name='acc_period_range_idx')]

    @property
    def label(self):
        return self.start_date.strftime('%B %Y')

    @property
    def is_open(self):
        return self.status == self.OPEN

    def __str__(self):
        return f'{self.label} ({self.get_status_display()})'


class PeriodEvent(models.Model):
    """History of every close / reopen with its reason (actor is not identified: the app has no login)."""
    period = models.ForeignKey(FinancialPeriod, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=6, choices=[('close', 'Closed'), ('reopen', 'Reopened')])
    reason = models.CharField(max_length=300)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-at', '-id']


class Account(models.Model):
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = 'asset', 'liability', 'equity', 'income', 'expense'
    TYPES = [(ASSET, 'Asset'), (LIABILITY, 'Liability'), (EQUITY, 'Equity'), (INCOME, 'Income'), (EXPENSE, 'Expense')]
    DEBIT_NORMAL = (ASSET, EXPENSE)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    account_type = models.CharField('Type', max_length=10, choices=TYPES, db_index=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children',
                               limit_choices_to={'is_group': True})
    is_group = models.BooleanField('Group (no postings)', default=False)
    active = models.BooleanField(default=True, db_index=True)
    is_system = models.BooleanField('System / locked', default=False,
                                    help_text='System accounts cannot change code, type or group, or be deactivated.')
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'

    @property
    def debit_normal(self):
        return self.account_type in self.DEBIT_NORMAL

    @property
    def is_used(self):
        return self.pk is not None and self.lines.exists()

    def clean(self):
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({'parent': 'An account cannot be its own group.'})
            if self.parent.account_type != self.account_type:
                raise ValidationError({'parent': 'Group must have the same type.'})
        if self.pk:
            old = Account.objects.get(pk=self.pk)
            if old.is_system and (old.code != self.code or old.account_type != self.account_type
                                  or old.is_group != self.is_group or not self.active):
                raise ValidationError('System account: code, type and group flag are locked and it cannot be deactivated.')
            if old.is_used and (old.account_type != self.account_type or (self.is_group and not old.is_group)):
                raise ValidationError('Account already has postings: its type cannot change and it cannot become a group.')
            if self.is_group and not old.is_group and self.lines.exists():
                raise ValidationError('Account has postings.')

    def delete(self, *a, **kw):
        if self.is_used or self.is_system:
            raise ImmutableError('This account is used or is a system account; deactivate it instead of deleting.')
        return super().delete(*a, **kw)


class Sequence(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    next_value = models.PositiveIntegerField(default=1)


class JournalEntry(models.Model):
    DRAFT, POSTED, REVERSED = 'draft', 'posted', 'reversed'
    STATUSES = [(DRAFT, 'Draft'), (POSTED, 'Posted'), (REVERSED, 'Reversed')]
    number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    date = models.DateField(db_index=True)
    narration = models.CharField(max_length=500)
    status = models.CharField(max_length=8, choices=STATUSES, default=DRAFT, db_index=True)
    source_type = models.CharField(max_length=40, default='manual', db_index=True)
    source_id = models.CharField(max_length=40, blank=True)
    source_key = models.CharField(max_length=120, unique=True, null=True, blank=True,
                                  help_text='Idempotency key: one entry per source event.')
    reversal_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_by')
    reversal_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    objects = EntryQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-id']
        indexes = [models.Index(fields=['source_type', 'source_id'], name='acc_je_source_idx')]
        verbose_name_plural = 'journal entries'

    def __str__(self):
        return self.number or f'Draft #{self.pk}'

    def _stored_status(self):
        if not self.pk:
            return None
        return JournalEntry.objects.filter(pk=self.pk).values_list('status', flat=True).first()

    def save(self, *a, **kw):
        stored = self._stored_status()
        if stored and stored != self.DRAFT and not getattr(self, '_posting', False):
            raise ImmutableError(f'Journal entry {self} is {stored} and cannot be changed. Reverse it instead.')
        return super().save(*a, **kw)

    def delete(self, *a, **kw):
        if self._stored_status() not in (None, self.DRAFT):
            raise ImmutableError(f'Journal entry {self} is posted and cannot be deleted. Reverse it instead.')
        return super().delete(*a, **kw)

    @property
    def total_debit(self):
        return sum((l.debit for l in self.lines.all()), 0)

    @property
    def total_credit(self):
        return sum((l.credit for l in self.lines.all()), 0)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='lines')
    debit = models.DecimalField(**MONEY)
    credit = models.DecimalField(**MONEY)
    description = models.CharField(max_length=300, blank=True)
    # optional sub-ledger tag (e.g. customer:12, vendor:3, employee:7) for per-party balances
    party_type = models.CharField(max_length=20, blank=True, default='', db_index=True)
    party_id = models.CharField(max_length=40, blank=True, default='')

    objects = LineQuerySet.as_manager()

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['account', 'entry'], name='acc_line_account_idx')]

    def _locked(self):
        st = JournalEntry.objects.filter(pk=self.entry_id).values_list('status', flat=True).first()
        return st not in (None, JournalEntry.DRAFT)

    def save(self, *a, **kw):
        if self._locked() and not getattr(self, '_posting', False):
            raise ImmutableError('Lines of a posted journal entry cannot be changed.')
        return super().save(*a, **kw)

    def delete(self, *a, **kw):
        if self._locked():
            raise ImmutableError('Lines of a posted journal entry cannot be deleted.')
        return super().delete(*a, **kw)


class AccountMapping(models.Model):
    key = models.CharField(max_length=60, unique=True)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name='mappings')
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['key']

    def __str__(self):
        return self.key
