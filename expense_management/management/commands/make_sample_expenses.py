"""Create demo expense data tagged "[sample]" in remarks. Reuses existing employees, departments, clients, projects.

    python manage.py make_sample_expenses            # add sample data (skips if already present)
    python manage.py make_sample_expenses --remove   # delete only records tagged [sample]
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from employee_management.models import Employee
from invoice_app.models import Client, ProjectTemplate

from expense_management import services
from expense_management.fy import today
from expense_management.models import (CompanyExpense, CompanyExpensePayment, EmployeeClaimReview,
                                       EmployeeExpenseClaim as C, EmployeeReimbursement, ExpenseCategory,
                                       ExpenseSubcategory, Vendor)

TAG = '[sample]'


def sub(cat, name):
    return ExpenseSubcategory.objects.filter(category=cat, name=name).first()


class Command(BaseCommand):
    help = 'Create sample company expenses, vendors and employee claims tagged [sample].'

    def add_arguments(self, p):
        p.add_argument('--remove', action='store_true', help='Remove records tagged [sample] and exit.')

    def handle(self, *a, remove=False, **kw):
        if remove:
            return self._remove()
        if CompanyExpense.objects.filter(remarks__contains=TAG).exists():
            self.stdout.write('Sample expenses already exist; use --remove first to recreate.')
            return
        cats = {c.name: c for c in ExpenseCategory.objects.all()}
        if not cats:
            raise CommandError('No expense categories. Run "python manage.py migrate" first.')
        emps = list(Employee.objects.filter(status__in=Employee.EMPLOYED).select_related('department')[:4])
        client = Client.objects.first()
        project = ProjectTemplate.objects.filter(client=client).first() if client else None
        t = today()
        made = dict(vendors=0, expenses=0, payments=0, claims=0, reimbursements=0)
        with transaction.atomic():
            vend = {}
            for name, typ, gstin, city, state in [
                ('Sample Cloud Hosting Pvt Ltd', 'hosting', '29ABCDE1234F1Z5', 'Bengaluru', 'Karnataka'),
                ('Sample Office Supplies', 'office', '', 'Mumbai', 'Maharashtra'),
                ('Sample Chartered Accountants', 'professional', '27ABCDE1234F1Z5', 'Pune', 'Maharashtra'),
                ('Sample Fibre Internet', 'internet', '', 'Mumbai', 'Maharashtra')]:
                vend[typ] = Vendor.objects.create(name=name, vendor_type=typ, gstin=gstin, city=city, state=state,
                                                  remarks=TAG)
                made['vendors'] += 1
            tech, office, prof = cats.get('Technology'), cats.get('Office'), cats.get('Professional Services')
            specs = [  # (days ago, vendor, cat, sub, desc, before tax, igst, cgst/sgst each, pay)
                (5, 'hosting', tech, 'Hosting', 'Monthly VPS hosting', '10000', '1800', None, 'full'),
                (40, 'hosting', tech, 'Cloud services', 'Object storage', '4000', '720', None, 'full'),
                (12, 'office', office, 'Office supplies', 'Printer paper and stationery', '2500', None, '225', 'none'),
                (20, 'professional', prof, 'Accounting', 'Quarterly bookkeeping', '20000', None, '1800', 'half'),
                (70, 'internet', office, 'Internet', 'Broadband - 3 months', '3000', None, '270', 'full'),
            ]
            for days, v, cat, s, desc, base, igst, half, pay in specs:
                base = Decimal(base)
                cg = sg = Decimal(half or 0)
                ig = Decimal(igst or 0)
                gst = ig + cg + sg
                e = CompanyExpense(expense_date=t - timedelta(days=days), vendor=vend[v], category=cat,
                                   subcategory=sub(cat, s), description=desc, invoice_number=f'S-{1000 + days}',
                                   invoice_date=t - timedelta(days=days), amount_before_tax=base, gst_amount=gst,
                                   cgst_amount=cg, sgst_amount=sg, igst_amount=ig, total_amount=base + gst,
                                   remarks=TAG, department=emps[0].department if emps else None,
                                   client=client if v == 'hosting' else None,
                                   project=project if v == 'hosting' else None)
                e.full_clean()
                e.save()
                made['expenses'] += 1
                if pay != 'none':
                    amt = e.total_amount if pay == 'full' else (e.total_amount / 2).quantize(Decimal('0.01'))
                    services.record_payment(e, payment_date=e.expense_date + timedelta(days=1), method='bank_transfer',
                                            amount=amt, reference=f'UTR{e.pk:06d}', remarks=TAG)
                    made['payments'] += 1
            travel, empx = cats.get('Travel'), cats.get('Employee Expenses')
            flows = ['reimbursed', 'partial', 'submitted', 'under_review', 'rejected', 'draft']
            for i, flow in enumerate(flows if emps else []):
                emp = emps[i % len(emps)]
                cat = travel if i % 2 == 0 else empx
                c = C(employee=emp, claim_date=t - timedelta(days=3 + i), expense_date=t - timedelta(days=6 + i),
                      category=cat, subcategory=sub(cat, 'Taxi' if cat == travel else 'Internet reimbursement'),
                      description=f'Sample claim {i + 1}', amount_claimed=Decimal('5000') if flow == 'partial'
                      else Decimal(1200 + i * 350), business_purpose='Client visit' if cat == travel else '',
                      client=client if cat == travel else None, remarks=TAG,
                      travel_from='Mumbai' if cat == travel else '', travel_to='Pune' if cat == travel else '')
                c.full_clean()
                c.save()
                made['claims'] += 1
                if flow == 'draft':
                    continue
                services.submit_claim(c, TAG)
                if flow == 'submitted':
                    continue
                services.start_review(c, TAG)
                if flow == 'under_review':
                    continue
                if flow == 'rejected':
                    services.reject_claim(c, 'Missing receipt (sample)')
                    continue
                if flow == 'partial':
                    services.approve_claim(c, Decimal('4500'), 'Capped per policy (sample)')
                    services.record_reimbursement(c, reimbursement_date=t, method='bank_transfer',
                                                  amount=Decimal('3000'), reference='SAMPLE-R1', remarks=TAG)
                else:
                    services.approve_claim(c, None, TAG)
                    services.record_reimbursement(c, reimbursement_date=t, method='upi', amount=c.approved_amount,
                                                  reference='SAMPLE-R2', remarks=TAG)
                made['reimbursements'] += 1
        self.stdout.write(self.style.SUCCESS('Created ' + ', '.join(f'{v} {k}' for k, v in made.items()) +
                                             '. All tagged [sample] in remarks.'))
        if not emps:
            self.stdout.write('No employed employees found, so no claims were created.')

    def _remove(self):
        with transaction.atomic():
            exps = CompanyExpense.objects.filter(remarks__contains=TAG)
            claims = C.objects.filter(remarks__contains=TAG)
            for att_parent in list(exps) + list(claims):
                for att in att_parent.attachments.all():
                    services.delete_attachment(att)
            n1 = CompanyExpensePayment.objects.filter(expense__in=exps)._raw_delete(using='default')
            n2 = EmployeeReimbursement.objects.filter(claim__in=claims)._raw_delete(using='default')
            EmployeeClaimReview.objects.filter(claim__in=claims).delete()
            ne, nc = exps.count(), claims.count()
            exps.delete()
            claims.delete()
            nv = Vendor.objects.filter(remarks__contains=TAG, expenses__isnull=True).delete()[0]
        self.stdout.write(self.style.SUCCESS(f'Removed {ne} expenses, {nc} claims, {nv} vendors, {n1} payments, '
                                             f'{n2} reimbursements tagged [sample].'))
