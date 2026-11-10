import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employee_management.models import Department, Employee, EmployeePayroll, PayrollPeriod
from expense_management.models import (CompanyExpense, CompanyExpensePayment, EmployeeExpenseClaim as C,
                                       EmployeeReimbursement, ExpenseCategory, ExpenseSubcategory, Vendor)
from invoice_app.models import (BusinessProfile, Client, Invoice, InvoiceLine, InvoicePayment,
                                ManpowerDeployment, ProjectTemplate)

from . import builder, customer_analysis as CA, exports, periods as P, reports as R, services as S
from .core import NT, inr

TODAY = date(2026, 11, 3)


class PeriodTests(TestCase):
    def test_fy_boundaries(self):
        self.assertEqual(P.fy_start_year(date(2026, 3, 31)), 2025)
        self.assertEqual(P.fy_start_year(date(2026, 4, 1)), 2026)
        self.assertEqual(P.fy_range(2025), (date(2025, 4, 1), date(2026, 3, 31)))
        self.assertEqual(P.fy_label(2025), 'FY 2025-26')

    def test_fy_parts(self):
        self.assertEqual(P.fy_part_range(2025, 'Q4'), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(P.fy_part_range(2025, 'H2'), (date(2025, 10, 1), date(2026, 3, 31)))
        self.assertEqual(P.fy_part_range(2025, 'Q1'), (date(2025, 4, 1), date(2025, 6, 30)))

    def test_resolve_options(self):
        r = lambda **k: P.resolve(k, ref=TODAY)
        self.assertEqual((r(period='current_fy').start, r(period='current_fy').end), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(r(period='prev_fy').start, date(2025, 4, 1))
        self.assertEqual((r(period='this_quarter').start, r(period='this_quarter').end), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(r(period='prev_quarter').start, date(2026, 7, 1))
        self.assertEqual(r(period='prev_month').start, date(2026, 10, 1))
        self.assertEqual(r(period='h1').end, date(2026, 9, 30))
        self.assertEqual(r(period='h2').start, date(2026, 10, 1))
        self.assertEqual(r(period='cal_year').start, date(2026, 1, 1))
        self.assertEqual(r(period='last_3m').start, date(2026, 9, 1))
        self.assertEqual(r(month='2026-02').end, date(2026, 2, 28))
        self.assertEqual(r(date_from='2026-05-10', date_to='2026-05-01').start, date(2026, 5, 1))
        self.assertEqual(r(fy='2024', fy_part='Q3').start, date(2024, 10, 1))
        self.assertEqual(r(period='this_week').start.weekday(), 0)
        self.assertEqual(r().label, 'FY 2026-27')

    def test_previous_period(self):
        p = P.resolve({'period': 'fy', 'fy': '2025'})
        self.assertEqual((p.previous().start, p.previous().end), (date(2024, 4, 1), date(2025, 3, 31)))
        q = P.resolve({'period': 'fy', 'fy': '2025', 'fy_part': 'Q1'}).previous()
        self.assertEqual((q.start, q.end), (date(2025, 1, 1), date(2025, 3, 31)))
        c = P.resolve({'date_from': '2026-05-01', 'date_to': '2026-05-10'}).previous()
        self.assertEqual((c.start, c.end), (date(2026, 4, 21), date(2026, 4, 30)))

    def test_pct_change_and_inr(self):
        self.assertEqual(P.pct_change(D(0), D(0))[1], 'n/a')
        self.assertEqual(P.pct_change(D(5), D(0))[1], 'new')
        self.assertEqual(P.pct_change(D(150), D(100))[0], D('50.00'))
        self.assertEqual(inr(D('1234567.8')), '12,34,567.80')
        self.assertEqual(inr(D('-1000')), '-1,000.00')
        self.assertEqual(inr(None), NT)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.dept = Department.objects.create(name='Engineering', code='ENG')
        cls.dept2 = Department.objects.create(name='Sales', code='SAL')
        cls.emp = Employee.objects.create(first_name='Asha', last_name='Rao', department=cls.dept, designation='Dev',
                                          date_of_joining=date(2024, 1, 1), status=Employee.CONFIRMED)
        cls.emp2 = Employee.objects.create(first_name='Ravi', last_name='K', department=cls.dept2, designation='AE',
                                           date_of_joining=date(2025, 6, 1), status=Employee.CONFIRMED)
        cls.client_a = Client.objects.create(name='Alpha Inc')
        cls.client_b = Client.objects.create(name='Beta LLC')
        prof = BusinessProfile.current()
        # FY boundary invoices: 31 Mar 2025 (FY24-25) and 1 Apr 2025 (FY25-26)
        cls.inv1 = cls._inv(prof, cls.client_a, date(2025, 3, 31), D('1000.00'))
        cls.inv2 = cls._inv(prof, cls.client_a, date(2025, 4, 1), D('2000.00'))
        cls.inv3 = cls._inv(prof, cls.client_b, date(2025, 10, 15), D('3000.50'))
        cls.inv4 = cls._inv(prof, cls.client_b, date(2026, 4, 1), D('500.00'))
        # payroll: Mar 2025 (prev FY), Apr 2025 finalized, May 2025 draft (excluded)
        for (y, m, st, g, ded, er) in [(2025, 3, 'finalized', '900', '90', '50'), (2025, 4, 'finalized', '1000', '100', '60'),
                                       (2025, 5, 'draft', '5000', '0', '0')]:
            per = PayrollPeriod.objects.create(year=y, month=m)
            EmployeePayroll.objects.create(period=per, employee=cls.emp, status=st, gross_earnings=D(g),
                                           total_deductions=D(ded), employer_contributions=D(er),
                                           net_salary=D(g) - D(ded), monthly_salary=D(g))
        tech = ExpenseCategory.objects.get(name='Technology')
        cls.hosting = ExpenseSubcategory.objects.get(name='Hosting')
        cls.travel = ExpenseCategory.objects.get(name='Travel')
        cls.vendor = Vendor.objects.create(name='AWS')
        cls.e1 = cls._exp(date(2025, 4, 10), tech, cls.hosting, '1000', '180')    # 1180, paid 500
        cls.e2 = cls._exp(date(2025, 3, 31), tech, cls.hosting, '200', '0')       # previous FY
        cls.e3 = cls._exp(date(2025, 6, 1), tech, cls.hosting, '300', '0')        # cancelled
        CompanyExpense.objects.filter(pk=cls.e3.pk).update(payment_status=CompanyExpense.CANCELLED)
        CompanyExpensePayment.objects.create(expense=cls.e1, payment_date=date(2025, 5, 1), amount=D('500'),
                                             method='bank_transfer', reference='UTR1')
        cls.c_ok = cls._claim(date(2025, 7, 1), '400', C.APPROVED, '400')
        cls.c_part = cls._claim(date(2025, 7, 2), '300', C.PARTIALLY_APPROVED, '250')
        cls.c_rej = cls._claim(date(2025, 7, 3), '999', C.REJECTED, None)
        cls.c_draft = cls._claim(date(2025, 7, 4), '777', C.DRAFT, None)
        EmployeeReimbursement.objects.create(claim=cls.c_ok, reimbursement_date=date(2025, 8, 1), amount=D('400'),
                                             method='bank_transfer', reference='R1')

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _inv(prof, client, d, total):
        i = Invoice.objects.create(client=client, invoice_number=f'RPT{Invoice.objects.count() + 1:04d}', invoice_date=d, due_date=d,
                                   total=total)
        InvoiceLine.objects.create(invoice=i, project_name='Proj X', description='Consulting', quantity_days=D('2'),
                                   amount=total)
        return i

    @classmethod
    def _exp(cls, d, cat, sub, base, gst):
        e = CompanyExpense(expense_date=d, vendor=cls.vendor, category=cat, subcategory=sub, description='x',
                           amount_before_tax=D(base), gst_amount=D(gst), igst_amount=D(gst))
        e.full_clean()
        e.save()
        return e

    @classmethod
    def _claim(cls, d, amt, status, approved):
        c = C(employee=cls.emp, claim_date=d, expense_date=d, category=cls.travel, description='cab',
              amount_claimed=D(amt))
        c.full_clean()
        c.save()
        C.objects.filter(pk=c.pk).update(status=status, approved_amount=D(approved) if approved else None)
        c.refresh_from_db()
        return c

    FY25 = (date(2025, 4, 1), date(2026, 3, 31))


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class ServiceTests(Base):
    def test_revenue_fy_boundary(self):
        self.assertEqual(S.invoice_revenue(*self.FY25), (D('5000.50'), 2))
        self.assertEqual(S.invoice_revenue(*P.fy_range(2024))[0], D('1000.00'))
        self.assertEqual(S.invoice_revenue(*P.fy_range(2026))[0], D('500.00'))

    def test_receipts_zero_without_payments(self):
        self.assertEqual(S.invoice_receipts(*self.FY25), D('0.00'))
        self.assertEqual(S.receivables_outstanding(date(2026, 3, 31)), D('6000.50'))

    def test_payroll_finalized_and_month_basis(self):
        t = S.payroll_totals(S.payrolls(*self.FY25))
        self.assertEqual((t['gross'], t['ded'], t['er'], t['net']), (D('1000.00'), D('100.00'), D('60.00'), D('900.00')))
        self.assertEqual(S.payroll_totals(S.payrolls(date(2025, 3, 1), date(2025, 3, 31)))['gross'], D('900.00'))

    def test_company_expenses_exclude_cancelled_and_fy(self):
        rows = S.company_expenses(*self.FY25)
        self.assertEqual([r['expense_id'] for r in rows], [self.e1.expense_id])
        self.assertEqual(rows[0]['total_inr'], D('1180.00'))
        self.assertEqual(rows[0]['outstanding_inr'], D('680.00'))
        self.assertEqual(len(S.company_expenses(*self.FY25, include_cancelled=True)), 2)

    def test_claim_statuses(self):
        self.assertEqual(S.reimbursement_expense(*self.FY25), D('650.00'))
        rows = {r['claim_id']: r for r in S.claims(*self.FY25)}
        self.assertEqual(rows[self.c_rej.claim_id]['approved_inr'], D('0.00'))
        self.assertEqual(rows[self.c_draft.claim_id]['outstanding_inr'], D('0.00'))

    def test_pnl_reconciles(self):
        x = S.pnl(*self.FY25)
        self.assertEqual(x['revenue'], D('5000.50'))
        self.assertEqual(x['total_expenses'], D('1000') + D('60') + D('650') + D('1180'))
        self.assertEqual(x['profit'], x['revenue'] - x['total_expenses'])
        self.assertEqual(sum(x['groups'].values()), x['company'])
        self.assertEqual(x['groups']['Software and IT'], D('1180.00'))

    def test_monthly_matrix_reconciles_with_pnl(self):
        x = S.pnl(*self.FY25)
        mm = S.monthly_matrix(*self.FY25)
        self.assertEqual(len(mm), 12)
        for k in ('revenue', 'claims', 'company', 'total_expenses', 'profit'):
            self.assertEqual(sum(v[k] for v in mm.values()), x[k], k)

    def test_cash_flow(self):
        cf = S.cash_flow(*self.FY25)
        self.assertEqual((cf['vendor_paid'], cf['reimbursed'], cf['outflow']), (D('500.00'), D('400.00'), D('900.00')))
        self.assertEqual(cf['receipts'], D('0.00'))
        self.assertEqual(cf['net'], -cf['outflow'])
        self.assertEqual(cf['salary_paid'], D('0.00'))

    def test_outstanding_as_at_and_ageing(self):
        vp = S.vendor_payables(date(2025, 4, 30))
        e1 = [r for r in vp if r['expense_id'] == self.e1.expense_id][0]
        self.assertEqual(e1['outstanding_inr'], D('1180.00'))  # payment dated after as-at
        e1 = [r for r in S.vendor_payables(date(2025, 6, 30)) if r['expense_id'] == self.e1.expense_id][0]
        self.assertEqual(e1['outstanding_inr'], D('680.00'))
        rp = {r['claim_id']: r for r in S.reimbursements_payable(date(2025, 9, 1))}
        self.assertNotIn(self.c_ok.claim_id, rp)
        self.assertEqual(rp[self.c_part.claim_id]['outstanding_inr'], D('250.00'))
        self.assertEqual(S.age_bucket(date(2025, 1, 1), date(2025, 1, 1))[0], 'Not Due')
        self.assertEqual(S.age_bucket(date(2025, 1, 1), date(2025, 1, 31))[0], '1-30 Days')
        self.assertEqual(S.age_bucket(date(2025, 1, 1), date(2025, 6, 1))[0], 'More Than 90 Days')


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class ReportTests(Base):
    FY = {'period': 'fy', 'fy': '2025'}

    def test_every_report_builds(self):
        for key in R.REGISTRY:
            rep = R.build(key, self.FY)
            self.assertTrue(rep.title, key)
            for t in rep.tables:
                for row in t.rows:
                    self.assertEqual(len(row), len(t.cols), (key, t.title))

    def test_pnl_report_values(self):
        rep = R.build('pnl', self.FY)
        rows = {r[0]: r for r in rep.tables[0].rows}
        self.assertEqual(rows['Revenue (invoice taxable value, excl. GST)'][1], D('5000.50'))
        self.assertEqual(rows['Revenue (invoice taxable value, excl. GST)'][2], D('1000.00'))
        self.assertEqual(rows['Net Profit / Loss'][1], S.pnl(*self.FY25)['profit'])

    def test_customer_revenue_reconciles(self):
        t = R.build('rev_customer', self.FY).tables[0]
        self.assertEqual(t.total[[c.label for c in t.cols].index(next(c.label for c in t.cols if c.kind == 'money'))],
                         D('5000.50'))

    def test_project_revenue_reconciles_with_lines(self):
        rep = R.build('rev_project', self.FY)
        tot = rep.tables[0].total
        self.assertIn(D('5000.50'), [v for v in tot if isinstance(v, D)])

    def test_exp_summary_and_category_match(self):
        a = R.build('company_expenses', self.FY).tables[0].total
        self.assertIn(D('1180.00'), [v for v in a if isinstance(v, D)])

    def test_fy_compare(self):
        rep = R.build('fy_compare', {'fy_a': '2024', 'fy_b': '2025'})
        row = rep.tables[0].rows[0]
        self.assertEqual(row[1:3], ['1,000.00', '5,000.50'])

    def test_monthly_perf_total_matches_pnl(self):
        t = R.build('monthly_perf', self.FY).tables[0]
        self.assertEqual(t.total[2], D('5000.50'))
        self.assertEqual(t.total[8], S.pnl(*self.FY25)['profit'])

    def test_outstanding_report(self):
        rep = R.build('outstanding', {'period': 'fy', 'fy': '2025'})
        labels = [c['label'] for c in rep.cards]
        self.assertIn('Vendor Payables', labels)
        self.assertEqual(rep.cards[0]['value'], D('6000.50'))

    def test_builder(self):
        from django.http import QueryDict
        rep = builder.build(QueryDict('dataset=invoices&period=fy&fy=2025&group_by=client'))[0]
        self.assertEqual(rep.tables[0].total[-1], D('5000.50'))
        rep = builder.build(QueryDict('dataset=claims&period=fy&fy=2025&cols=claim_id&cols=approved'))[0]
        self.assertEqual(rep.tables[0].total[-1], D('650.00'))

    def test_xlsx_and_fy_workbook(self):
        from openpyxl import load_workbook
        wb = load_workbook(BytesIO(exports.fy_workbook(2025)))
        self.assertEqual(wb.sheetnames, [n for n, _ in exports.FY_SHEETS])
        self.assertEqual(len(wb.sheetnames), 11)
        ws = load_workbook(BytesIO(exports.report_xlsx(R.build('pnl', self.FY)))).active
        self.assertIn('Management Profit and Loss', [c.value for c in ws['B']])

    def test_pdf(self):
        pdf = exports.report_pdf(R.build('invoice_register', self.FY))
        self.assertTrue(pdf.startswith(b'%PDF'))
        pdf = exports.report_pdf(R.build('monthly_perf', self.FY))
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_management_command(self):
        out = tempfile.mkdtemp()
        try:
            call_command('reporting_samples', fy=2025, out=out, stdout=BytesIO() and __import__('io').StringIO())
            import os
            self.assertTrue(os.path.getsize(os.path.join(out, 'sample_fy_report.xlsx')) > 1000)
            self.assertTrue(os.path.getsize(os.path.join(out, 'sample_report.pdf')) > 1000)
        finally:
            shutil.rmtree(out, ignore_errors=True)


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class CustomerAnalysisTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Client.objects.filter(pk=cls.client_a.pk).update(created_on=date(2025, 4, 15))
        Client.objects.filter(pk=cls.client_b.pk).update(
            created_on=date(2025, 10, 5), inactive_since=date(2026, 1, 20), active=False)
        cls.project = ProjectTemplate.objects.create(client=cls.client_a, name='Alpha Support',
                                                     monthly_amount=D('1000'))
        cls.live_deployment = ManpowerDeployment.objects.create(
            client=cls.client_a, project=cls.project, designation='Developer', start_date=date(2025, 10, 1),
            billing_rate=D('1000'), rate_effective_from=date(2025, 10, 1), status='active')
        cls.ended_deployment = ManpowerDeployment.objects.create(
            client=cls.client_b, designation='Analyst', start_date=date(2025, 11, 1),
            end_date=date(2026, 2, 28), billing_rate=D('500'), rate_effective_from=date(2025, 11, 1),
            status='completed')
        InvoicePayment.objects.create(invoice=cls.inv3, receipt_date=date(2025, 11, 15), amount=D('1000'))

    def build(self):
        return CA.build(P.resolve({'period_mode': 'financial', 'fy': '2025'}))

    def test_customer_movement(self):
        data = self.build()
        cards = {c['label']: c['value'] for c in data['cards']}
        self.assertEqual(cards['Active customers'], '1')
        self.assertEqual(cards['Customers added'], '2')
        self.assertEqual(cards['Customers left'], '1')
        self.assertEqual(cards['Customers billed'], '2')
        self.assertEqual(cards['Active projects'], '1')
        self.assertEqual(cards['Live manpower'], '1')
        self.assertEqual(cards['Revenue'], '5,000.50')
        self.assertEqual(cards['Amount received'], '1,000.00')
        self.assertEqual(cards['Outstanding receivables'], '5,000.50')
        added = data['charts'][0]['svg']
        self.assertIn('Apr 25: 1', added)
        self.assertIn('Oct 25: 1', added)
        self.assertIn('Jan 26: 1', added)

    def test_customer_scorecard(self):
        rows = {r['name']: r for r in self.build()['rows']}
        self.assertEqual(rows['Alpha Inc']['revenue'], '2,000.00')
        self.assertEqual(rows['Beta LLC']['revenue'], '3,000.50')
        self.assertEqual(rows['Alpha Inc']['billed_projects'], 1)
        self.assertEqual(rows['Alpha Inc']['manpower'], 1)
        self.assertEqual(rows['Beta LLC']['added'], date(2025, 10, 5))
        self.assertTrue(rows['Beta LLC']['left'])
        self.assertIn(f'client={self.client_b.pk}', rows['Beta LLC']['url'])

    def test_new_and_existing_revenue_split(self):
        svg = self.build()['charts'][3]['svg']
        self.assertIn('New customers', svg)
        self.assertIn('Existing customers', svg)
        self.assertIn('Apr 25: 2,000.00', svg)
        self.assertIn('Oct 25: 3,000.50', svg)

    def test_manpower_movements(self):
        svg = self.build()['charts'][7]['svg']
        self.assertIn('Oct 25: 1', svg)
        self.assertIn('Feb 26: 1', svg)

    def test_client_lifecycle_dates(self):
        c = Client.objects.create(name='Gamma Ltd')
        self.assertEqual(c.created_on, timezone.localdate())
        c.active = False
        c.save(update_fields=['active'])
        c.refresh_from_db()
        self.assertEqual(c.inactive_since, timezone.localdate())
        c.active = True
        c.save(update_fields=['active'])
        c.refresh_from_db()
        self.assertIsNone(c.inactive_since)

    def test_view_and_period_modes(self):
        base = reverse('reporting:customer_projects')
        for query in ('?period_mode=financial&fy=2025',
                      '?period_mode=financial&fy=2025&period_type=quarterly&period_value=Q3',
                      '?period_mode=calendar&calendar_year=2025',
                      '?period_mode=custom&date_from=2025-04-01&date_to=2025-12-31'):
            with self.subTest(query=query):
                r = self.client.get(base + query)
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, 'Customer &amp; Project Analysis')
                self.assertContains(r, 'data-standard-period-filter')
                self.assertContains(r, 'Alpha Inc')
                self.assertContains(r, '<svg')

    def test_view_is_local_only(self):
        r = self.client.get(reverse('reporting:customer_projects'), REMOTE_ADDR='10.1.2.3')
        self.assertEqual(r.status_code, 403)


class ViewTests(Base):
    def test_dashboard(self):
        r = self.client.get(reverse('reporting:dashboard') + '?period=fy&fy=2025')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['ex']['cards']), 17)
        self.assertEqual(len(r.context['ex']['charts']), 11)
        cards = {c['label']: c['value'] for c in r.context['ex']['cards']}
        self.assertEqual(cards['Gross Salary'], D('1000.00'))
        self.assertEqual(cards['Employee Deductions'], D('100.00'))
        self.assertEqual(cards['Employer Contributions'], D('60.00'))
        self.assertEqual(cards['Net Salary Payable'], D('900.00'))
        self.assertContains(r, '<svg', count=None)
        self.assertNotContains(r, 'cdn.')
        self.assertContains(r, 'Employee Deductions')
        self.assertContains(r, 'Recorded Cash Inflow vs Outflow')
        self.assertContains(r, 'difference')

    def test_dashboard_all_period_shapes_render(self):
        queries = [
            '?period=this_month', '?month=2025-04', '?period=this_quarter', '?period=fy&fy=2025&fy_part=Q2',
            '?period=h1', '?period=h2', '?period=current_fy', '?period=prev_fy', '?period=fy&fy=2025',
            '?period=cal_year', '?date_from=2025-04-01&date_to=2025-06-30',
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.client.get(reverse('reporting:dashboard') + query).status_code, 200)

    def test_dashboard_exports_respect_selected_month(self):
        from openpyxl import load_workbook
        url = reverse('reporting:dashboard') + '?month=2025-04&format=xlsx'
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn('april-2025.xlsx', r['Content-Disposition'])
        wb = load_workbook(BytesIO(r.content), data_only=True)
        ws = wb['Monthly Financial Summary']
        values = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertIn('Apr 2025', values)
        self.assertNotIn('May 2025', values)
        self.assertEqual(self.client.get(reverse('reporting:dashboard') + '?month=2025-04&format=pdf').status_code, 200)

    def test_all_report_pages_and_exports(self):
        for key in R.REGISTRY:
            url = reverse('reporting:report', args=[key]) + '?period=fy&fy=2025'
            self.assertEqual(self.client.get(url).status_code, 200, key)
        for fmt, ct in (('xlsx', 'spreadsheetml'), ('pdf', 'pdf')):
            r = self.client.get(reverse('reporting:report', args=['pnl']) + f'?period=fy&fy=2025&format={fmt}')
            self.assertIn(ct, r['Content-Type'])

    def test_sections_builder_sources(self):
        for slug in R.SECTION_SLUGS:
            self.assertEqual(self.client.get(reverse('reporting:section', args=[slug])).status_code, 200)
        self.assertEqual(self.client.get(reverse('reporting:custom') + '?dataset=company_expenses').status_code, 200)
        self.assertEqual(self.client.get(reverse('reporting:sources')).status_code, 200)
        self.assertEqual(self.client.get(reverse('reporting:fy_workbook') + '?fy=2025').status_code, 200)
        self.assertEqual(self.client.get(reverse('reporting:report', args=['nope'])).status_code, 404)

    def test_pagination(self):
        # Shared pager: page/page_size params, sizes 5..500, filters kept in page links.
        r = self.client.get(reverse('reporting:report', args=['invoice_register']) + '?period=fy&fy=2025&page_size=5&page=2')
        self.assertContains(r, 'data-qb-pager')
        self.assertContains(r, '<option value="5" selected>5</option>', html=False)

    def test_nav_dropdown(self):
        r = self.client.get(reverse('reporting:dashboard'))
        self.assertContains(r, 'Reports &amp; Analytics')

    def test_local_only(self):
        r = self.client.get(reverse('reporting:dashboard'), REMOTE_ADDR='10.1.2.3')
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------- Task 5: presets, label, receipts, reconciliation

class PresetTests(TestCase):
    def at(self, t, **k):
        with mock.patch('reporting.periods.today', return_value=t):
            return P.resolve(k)

    def test_every_preset_with_mocked_today(self):
        t = date(2026, 11, 3)  # Tuesday
        exp = {
            'today': (t, t), 'yesterday': (date(2026, 11, 2),) * 2,
            'this_week': (date(2026, 11, 2), date(2026, 11, 8)), 'last_week': (date(2026, 10, 26), date(2026, 11, 1)),
            'this_month': (date(2026, 11, 1), date(2026, 11, 30)), 'prev_month': (date(2026, 10, 1), date(2026, 10, 31)),
            'this_quarter': (date(2026, 10, 1), date(2026, 12, 31)), 'prev_quarter': (date(2026, 7, 1), date(2026, 9, 30)),
            'h1': (date(2026, 4, 1), date(2026, 9, 30)), 'h2': (date(2026, 10, 1), date(2027, 3, 31)),
            'current_fy': (date(2026, 4, 1), date(2027, 3, 31)), 'prev_fy': (date(2025, 4, 1), date(2026, 3, 31)),
            'cal_year': (date(2026, 1, 1), date(2026, 12, 31)), 'prev_cal_year': (date(2025, 1, 1), date(2025, 12, 31)),
            'last_3m': (date(2026, 9, 1), t), 'last_6m': (date(2026, 6, 1), t), 'last_12m': (date(2025, 12, 1), t),
        }
        for key, (s, e) in exp.items():
            p = self.at(t, period=key)
            self.assertEqual((p.start, p.end, p.key), (s, e, key), key)
        self.assertEqual(set(exp) | {'fy', 'month', 'custom'}, {k for k, _ in P.OPTIONS})

    def test_boundaries(self):
        # 31 Mar is still the old FY; 1 Apr starts the new one
        self.assertEqual(self.at(date(2026, 3, 31), period='current_fy').start, date(2025, 4, 1))
        self.assertEqual(self.at(date(2026, 4, 1), period='current_fy').start, date(2026, 4, 1))
        self.assertEqual(self.at(date(2026, 4, 1), period='prev_quarter').start, date(2026, 1, 1))
        self.assertEqual(self.at(date(2026, 1, 1), period='h2').start, date(2025, 10, 1))
        self.assertEqual(self.at(date(2026, 1, 1), period='prev_month').start, date(2025, 12, 1))
        self.assertEqual(self.at(date(2026, 1, 1), period='yesterday').start, date(2025, 12, 31))
        self.assertEqual(self.at(date(2024, 3, 1), period='prev_month').end, date(2024, 2, 29))  # leap year
        self.assertEqual(self.at(date(2026, 11, 2), period='this_week').start, date(2026, 11, 2))  # Monday
        self.assertEqual(self.at(date(2026, 11, 8), period='this_week').start, date(2026, 11, 2))  # Sunday
        self.assertEqual(self.at(date(2026, 11, 3), fy='2025', fy_part='Q4').end, date(2026, 3, 31))
        self.assertEqual(self.at(date(2026, 11, 3), period='fy').key, 'current_fy')  # fy chosen but no year
        self.assertEqual(self.at(date(2026, 11, 3), period='bogus').key, 'current_fy')

    def test_labels(self):
        self.assertEqual(P.resolve({'fy': '2025'}).label, 'FY 2025-26')
        self.assertEqual(P.resolve({'fy': '2025'}).key, 'fy')
        self.assertEqual(P.resolve({'month': '2026-02'}).key, 'month')
        self.assertEqual(P.resolve({'date_from': '2026-01-01'}).key, 'custom')


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class LabelPersistenceTests(Base):
    def test_fy_only_selects_fy_not_today(self):
        for url in (reverse('reporting:dashboard'), reverse('reporting:report', args=['rev_summary'])):
            r = self.client.get(url + '?fy=2025')
            # Legacy ?fy= links resolve to the FY and the shared picker shows that FY selected.
            self.assertContains(r, '<option value="financial" selected>Financial year</option>')
            self.assertContains(r, '<option value="2025" selected>FY 2025-26</option>')
            self.assertContains(r, 'FY 2025-26')

    def test_default_and_explicit(self):
        cur = P.fy_start_year(P.today())
        r = self.client.get(reverse('reporting:dashboard'))
        self.assertContains(r, '<option value="financial" selected>Financial year</option>')
        self.assertContains(r, f'<option value="{cur}" selected>{P.fy_label(cur)}</option>')
        self.assertContains(r, '<option value="full_fy" selected>Full year</option>')
        # Legacy period links still resolve (shown in the report's period line).
        r = self.client.get(reverse('reporting:dashboard') + '?period=prev_month')
        self.assertContains(r, P.resolve({'period': 'prev_month'}).label)
        r = self.client.get(reverse('reporting:dashboard') + '?month=2025-05')
        self.assertContains(r, 'May 2025')

    def test_exports_use_same_label(self):
        from openpyxl import load_workbook
        r = self.client.get(reverse('reporting:report', args=['rev_summary']) + '?fy=2025&format=xlsx')
        self.assertIn('fy-2025-26', r['Content-Disposition'])
        ws = load_workbook(BytesIO(r.content)).active
        self.assertTrue(any('FY 2025-26' in str(c.value) for row in ws.iter_rows() for c in row if c.value))
        r = self.client.get(reverse('reporting:report', args=['rev_summary']) + '?fy=2025&format=pdf')
        self.assertIn('fy-2025-26', r['Content-Disposition'])


class ReceiptReconciliationTests(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        pay = lambda i, d, a, ded='0': InvoicePayment.objects.create(invoice=i, receipt_date=d, amount=D(a),
                                                                     deductions=D(ded), mode='bank')
        pay(cls.inv2, date(2025, 5, 1), '1800', '200')     # full with TDS
        pay(cls.inv3, date(2025, 11, 1), '1000')           # partial
        pay(cls.inv3, date(2026, 4, 5), '500')             # after FY end
        pay(cls.inv1, date(2025, 4, 10), '1100')           # overpaid prev-FY invoice, receipt in FY25
        cls.inv4.cancelled = True
        cls.inv4.save()

    def test_statuses(self):
        self.assertEqual(self.inv2.payment_summary()['status'], 'Paid')
        self.assertEqual(self.inv3.payment_summary()['status'], 'Partially Paid')
        self.assertEqual(self.inv1.payment_summary()['status'], 'Overpaid')
        self.assertEqual(self.inv4.payment_summary()['status'], 'Cancelled')
        rows = {x['inv'].pk: x for x in S.invoice_balances(date(2026, 3, 31))}
        self.assertEqual(rows[self.inv3.pk]['outstanding'], D('2000.50'))  # April receipt not counted yet
        self.assertEqual(rows[self.inv1.pk]['outstanding'], D('-100.00'))
        self.assertNotIn(self.inv4.pk, rows)

    def test_receipts_register_equals_received(self):
        rep = R.build('receipts_register', {'fy': '2025'})
        self.assertEqual(rep.tables[0].total[5], D('3900.00'))
        self.assertEqual(S.invoice_receipts(*self.FY25), D('3900.00'))
        self.assertEqual(S.cash_flow(*self.FY25)['receipts'], D('3900.00'))
        self.assertEqual(sum(v['received'] for v in S.monthly_matrix(*self.FY25).values()), D('3900.00'))

    def test_receivables_equal_sum_of_balances(self):
        a = date(2026, 3, 31)
        rows = S.invoice_balances(a)
        self.assertEqual(S.receivables_outstanding(a), sum((x['outstanding'] for x in rows if x['outstanding'] > 0), D(0)))
        self.assertEqual(S.receivables_outstanding(a), D('2000.50'))
        rep = R.build('receivables', {'fy': '2025'})
        self.assertEqual(rep.cards[0]['value'], D('2000.50'))
        self.assertEqual(rep.tables[0].total[2], D('2000.50'))  # ageing summary total
        self.assertEqual(S.receivables_outstanding(date(2026, 4, 30)), D('1500.50'))

    def test_revenue_excludes_payments_and_cancelled(self):
        self.assertEqual(S.invoice_revenue(*self.FY25), (D('5000.50'), 2))
        self.assertEqual(S.pnl(*self.FY25)['revenue'], D('5000.50'))
        self.assertEqual(S.invoice_revenue(*P.fy_range(2026))[0], D('0.00'))  # inv4 cancelled
        self.assertEqual(list(S.cancelled_invoices(*P.fy_range(2026))), [self.inv4])

    def test_register_columns(self):
        t = R.build('invoice_register', {'fy': '2025'}).tables[0]
        self.assertEqual(t.total[9], D('2800.00'))   # received on FY25 invoices up to 31 Mar 2026
        self.assertEqual(t.total[11], D('2000.50'))

    def test_reports_have_no_not_tracked_receipts(self):
        for key in ('rev_summary', 'cash_flow', 'outstanding', 'fy_compare', 'ratios', 'monthly_perf'):
            rep = R.build(key, {'fy': '2025', 'fy_a': '2024', 'fy_b': '2025'})
            for c in rep.cards:
                if 'Receiv' in c['label'] or 'Received' in c['label'] or 'Inflow' in c['label']:
                    self.assertIsNotNone(c['value'], (key, c['label']))
