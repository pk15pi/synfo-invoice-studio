"""OPTIONAL: load clearly-labelled EXAMPLE rules. They are INACTIVE and UNVERIFIED by default.

The numbers below are illustrations only and may be wrong or out of date. Nothing in the app uses an
inactive rule; activate a rule only after you or your CA have verified it."""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from compliance.models import DueDateEntry, StatutoryRule

LABEL = 'EXAMPLE - NOT VERIFIED. Verify with your CA before activating.'
EXAMPLE_RULES = [
    (StatutoryRule.GST, '998314', {'rate': '18'}),
    (StatutoryRule.TDS, '194J', {'rate': '10', 'threshold_single': '30000'}),
    (StatutoryRule.TDS, '194C', {'rate': '2', 'threshold_single': '30000'}),
    (StatutoryRule.PF, '', {'employee_pct': '12', 'employer_pct': '12', 'wage_ceiling': '15000'}),
    (StatutoryRule.ESI, '', {'employee_pct': '0.75', 'employer_pct': '3.25', 'wage_ceiling': '21000'}),
    (StatutoryRule.PT, 'Maharashtra', {'slabs': [{'from': '0', 'to': '7500', 'amount': '0'},
                                                 {'from': '7500.01', 'to': '10000', 'amount': '175'},
                                                 {'from': '10000.01', 'to': None, 'amount': '200'}]}),
]
EXAMPLE_DUE = [('GST payment (example)', 'gst_return', 20, 1), ('TDS deposit (example)', 'tds_vendor', 7, 1),
               ('PF deposit (example)', 'pf', 15, 1), ('ESI deposit (example)', 'esi', 15, 1)]


class Command(BaseCommand):
    help = 'Load EXAMPLE (unverified, inactive) compliance rules and due dates. Optional; never authoritative.'

    def add_arguments(self, parser):
        parser.add_argument('--effective-from', default='2026-04-01')

    @transaction.atomic
    def handle(self, *args, **o):
        eff = date.fromisoformat(o['effective_from'])
        made = 0
        for kind, key, params in EXAMPLE_RULES:
            nk = StatutoryRule.norm_key(kind, key)
            if StatutoryRule.objects.filter(kind=kind, key=nk, is_example=True).exists():
                continue
            StatutoryRule(kind=kind, key=nk, effective_from=eff, params=params, active=False, is_example=True,
                          source_note=LABEL).save()
            made += 1
        for name, kind, day, off in EXAMPLE_DUE:
            if not DueDateEntry.objects.filter(name=name).exists():
                DueDateEntry.objects.create(name=name, kind=kind, day_of_month=day, month_offset=off, active=False,
                                            is_example=True, notes=LABEL)
                made += 1
        self.stdout.write(self.style.WARNING(f'Loaded {made} EXAMPLE item(s), all INACTIVE and NOT VERIFIED. '
                                             'Verify with your CA before activating any of them.'))
