"""Stage 7: statutory compliance.

Nothing here is authoritative. Every rate, ceiling, slab and due date is entered (and must be verified) by the
user or their CA. When no active rule covers a date the services raise RuleNotConfigured ('rule not configured');
there is never a fallback to a hardcoded number. Existing payroll / invoice calculations do NOT use these rules
yet - they are only compared against them on the read-only compliance check page.
"""
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

VERIFY_WARNING = ('Statutory rates, ceilings, slabs and due dates change and differ by state. Nothing in this app is '
                  'authoritative: every rule must be verified by you or your CA before you rely on it.')


def _dec(v, label, errors, required=True, minimum=Decimal('0'), maximum=None):
    if v in (None, ''):
        if required:
            errors.append(f'{label} is required.')
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        errors.append(f'{label} must be a number.')
        return None
    if not d.is_finite() or d < minimum or (maximum is not None and d > maximum):
        errors.append(f'{label} must be between {minimum} and {maximum if maximum is not None else "any"}.')
        return None
    return d


HUNDRED = Decimal('100')


class StatutoryRule(models.Model):
    GST, TDS, PF, ESI, PT = 'gst_sac', 'tds_section', 'pf', 'esi', 'pt'
    KINDS = [(GST, 'GST rate per SAC'), (TDS, 'TDS section rate / threshold'),
             (PF, 'PF employee / employer % and wage ceiling'), (ESI, 'ESI employee / employer % and wage ceiling'),
             (PT, 'Professional tax slabs by state')]
    KEY_LABEL = {GST: 'SAC code', TDS: 'TDS section (e.g. 194J)', PF: '(leave blank)', ESI: '(leave blank)',
                 PT: 'State (as on the employee record, e.g. Maharashtra)'}
    PARAM_HELP = {
        GST: '{"rate": "18"}  - total GST %; split into CGST+SGST halves or IGST by the invoice tax type',
        TDS: '{"rate": "10", "threshold_single": "30000", "threshold_annual": "100000"}  - thresholds optional',
        PF: '{"employee_pct": "12", "employer_pct": "12", "wage_ceiling": "15000"}  - wage_ceiling optional (no cap)',
        ESI: '{"employee_pct": "0.75", "employer_pct": "3.25", "wage_ceiling": "21000"}  - applies when gross <= ceiling',
        PT: '{"slabs": [{"from": "0", "to": "7500", "amount": "0"}, {"from": "7500.01", "to": null, "amount": "200"}]}',
    }

    kind = models.CharField(max_length=12, choices=KINDS, db_index=True)
    key = models.CharField('Key (SAC / section / state)', max_length=60, blank=True, db_index=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text='Blank = open-ended.')
    params = models.JSONField(default=dict)
    active = models.BooleanField(default=True, db_index=True)
    is_example = models.BooleanField('Example (unverified)', default=False,
                                     help_text='Loaded by load_example_compliance_rules. NOT verified - do not rely on it.')
    source_note = models.CharField('Source / verified by', max_length=240, blank=True,
                                   help_text='e.g. notification number, CA name and date verified.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'key', '-effective_from']

    def __str__(self):
        return f'{self.get_kind_display()} {self.key} from {self.effective_from:%d-%m-%Y}'.strip()

    @staticmethod
    def norm_key(kind, key):
        key = (key or '').strip()
        if kind in (StatutoryRule.PF, StatutoryRule.ESI):
            return ''
        if kind == StatutoryRule.PT:
            return ' '.join(key.split()).title()
        return key.upper().replace(' ', '')

    def clean_params(self):
        p = self.params
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except ValueError:
                raise ValidationError({'params': 'Params must be valid JSON.'})
        if not isinstance(p, dict):
            raise ValidationError({'params': 'Params must be a JSON object.'})
        e, out = [], {}
        k = self.kind
        if k == self.GST:
            r = _dec(p.get('rate'), 'rate', e, maximum=HUNDRED)
            out = {'rate': str(r)} if r is not None else {}
        elif k == self.TDS:
            r = _dec(p.get('rate'), 'rate', e, maximum=HUNDRED)
            ts = _dec(p.get('threshold_single'), 'threshold_single', e, required=False)
            ta = _dec(p.get('threshold_annual'), 'threshold_annual', e, required=False)
            out = {'rate': str(r), 'threshold_single': None if ts is None else str(ts),
                   'threshold_annual': None if ta is None else str(ta)}
        elif k in (self.PF, self.ESI):
            ee = _dec(p.get('employee_pct'), 'employee_pct', e, maximum=HUNDRED)
            er = _dec(p.get('employer_pct'), 'employer_pct', e, maximum=HUNDRED)
            c = _dec(p.get('wage_ceiling'), 'wage_ceiling', e, required=(k == self.ESI))
            out = {'employee_pct': str(ee), 'employer_pct': str(er), 'wage_ceiling': None if c is None else str(c)}
        elif k == self.PT:
            slabs = p.get('slabs')
            if not isinstance(slabs, list) or not slabs:
                e.append('slabs must be a non-empty list of {"from", "to", "amount"}.')
                slabs = []
            rows = []
            for i, s in enumerate(slabs, 1):
                if not isinstance(s, dict):
                    e.append(f'slab {i} must be an object.')
                    continue
                f = _dec(s.get('from'), f'slab {i} from', e)
                t = _dec(s.get('to'), f'slab {i} to', e, required=False)
                a = _dec(s.get('amount'), f'slab {i} amount', e)
                if f is not None and t is not None and t < f:
                    e.append(f'slab {i}: to is below from.')
                rows.append((f, t, a))
            rows = [r for r in rows if r[0] is not None and r[2] is not None]
            rows.sort(key=lambda r: r[0])
            for (f1, t1, _), (f2, _, _) in zip(rows, rows[1:]):
                if t1 is None or t1 >= f2:
                    e.append('PT slabs overlap; each slab must end before the next one starts.')
                    break
            out = {'slabs': [{'from': str(f), 'to': None if t is None else str(t), 'amount': str(a)} for f, t, a in rows]}
        if e:
            raise ValidationError({'params': e})
        return out

    def clean(self):
        errors = {}
        self.key = self.norm_key(self.kind, self.key)
        if self.kind in (self.GST, self.TDS, self.PT) and not self.key:
            errors['key'] = f'{self.KEY_LABEL.get(self.kind, "Key")} is required for this kind.'
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            errors['effective_to'] = 'Effective to is before effective from.'
        try:
            self.params = self.clean_params()
        except ValidationError as ex:
            errors.update(ex.message_dict)
        if not errors and self.active:
            clash = StatutoryRule.objects.filter(kind=self.kind, key=self.key, active=True).exclude(pk=self.pk).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
            if self.effective_to:
                clash = clash.filter(effective_from__lte=self.effective_to)
            c = clash.first()
            if c:
                errors['effective_from'] = (f'Overlaps an existing active rule ({c.effective_from:%d-%m-%Y} to '
                                            f'{c.effective_to.strftime("%d-%m-%Y") if c.effective_to else "open"}). '
                                            'End the earlier rule first.')
        if errors:
            raise ValidationError(errors)

    def save(self, *a, **kw):
        self.full_clean()
        super().save(*a, **kw)

    def P(self, name):
        v = (self.params or {}).get(name)
        return None if v in (None, '') else Decimal(str(v))

    @property
    def params_text(self):
        return json.dumps(self.params, indent=None)


class GstInputClaim(models.Model):
    """Eligibility of the GST on one company expense. Default: not eligible / under review. Only a manual
    mark-eligible (with a reason) reclassifies the GST out of the expense into input GST."""
    REVIEW, ELIGIBLE = 'review', 'eligible'
    STATUSES = [(REVIEW, 'Not eligible / under review'), (ELIGIBLE, 'Eligible (claimed)')]
    expense = models.OneToOneField('expense_management.CompanyExpense', on_delete=models.PROTECT,
                                   related_name='gst_input_claim')
    status = models.CharField(max_length=10, choices=STATUSES, default=REVIEW, db_index=True)
    reason = models.CharField(max_length=300, blank=True)
    changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.expense.expense_id} {self.get_status_display()}'


class GstInputEvent(models.Model):
    claim = models.ForeignKey(GstInputClaim, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=10, choices=[('mark', 'Marked eligible'), ('unmark', 'Unmarked')])
    reason = models.CharField(max_length=300)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-at', '-id']


PAYMENT_KINDS = [('gst_cgst', 'GST - CGST'), ('gst_sgst', 'GST - SGST'), ('gst_igst', 'GST - IGST'),
                 ('tds_vendor', 'TDS - vendor payments'), ('tds_salary', 'TDS - salary'),
                 ('pf', 'Provident Fund'), ('esi', 'ESI'), ('pt', 'Professional tax')]
# Payable side of each statutory payment, by AccountMapping key (no hardcoded accounts).
PAYABLE_KEYS = {'gst_cgst': 'gst.output.cgst', 'gst_sgst': 'gst.output.sgst', 'gst_igst': 'gst.output.igst',
                'tds_vendor': 'expense.tds_payable', 'tds_salary': 'payroll.tds_payable',
                'pf': 'payroll.pf_payable', 'esi': 'payroll.esi_payable', 'pt': 'payroll.pt_payable'}


class StatutoryPayment(models.Model):
    kind = models.CharField(max_length=12, choices=PAYMENT_KINDS, db_index=True)
    period_year = models.PositiveIntegerField()
    period_month = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField('Payment date')
    bank_account = models.ForeignKey('bank_management.BankAccount', null=True, blank=True, on_delete=models.PROTECT,
                                     related_name='statutory_payments', help_text='Blank = default bank account.')
    challan_ref = models.CharField('Challan / CIN / reference', max_length=80)
    remarks = models.CharField(max_length=300, blank=True)
    cancelled = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.get_kind_display()} {self.period_month:02d}/{self.period_year} {self.amount}'

    def clean(self):
        e = {}
        if self.amount is not None and self.amount <= 0:
            e['amount'] = 'Amount must be greater than zero.'
        if self.period_month and not 1 <= self.period_month <= 12:
            e['period_month'] = 'Month must be 1-12.'
        if not (self.challan_ref or '').strip():
            e['challan_ref'] = 'Challan reference is required.'
        if e:
            raise ValidationError(e)

    @property
    def period_label(self):
        return f'{self.period_month:02d}/{self.period_year}'


class DueDateEntry(models.Model):
    """A user-configured filing / payment due date (no auto-filing, no external calls)."""
    KINDS = PAYMENT_KINDS + [('gst_return', 'GST return'), ('tds_return', 'TDS return'), ('other', 'Other')]
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=KINDS, default='other')
    day_of_month = models.PositiveSmallIntegerField(help_text='Day the item is due (capped at month end).')
    month_offset = models.PositiveSmallIntegerField(default=1, help_text='Months after the period month (1 = following month).')
    months = models.CharField('Applies to period months', max_length=40, blank=True,
                              help_text='Comma-separated months (e.g. 3,6,9,12 for quarterly). Blank = every month.')
    active = models.BooleanField(default=True)
    is_example = models.BooleanField('Example (unverified)', default=False)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ['day_of_month', 'name']
        verbose_name_plural = 'due date entries'

    def __str__(self):
        return self.name

    def month_list(self):
        return [int(x) for x in self.months.replace(' ', '').split(',') if x.isdigit()]

    def clean(self):
        if not 1 <= (self.day_of_month or 0) <= 31:
            raise ValidationError({'day_of_month': 'Day must be 1-31.'})
        bad = [x for x in self.months.replace(' ', '').split(',') if x and (not x.isdigit() or not 1 <= int(x) <= 12)]
        if bad:
            raise ValidationError({'months': 'Months must be numbers 1-12 separated by commas.'})
