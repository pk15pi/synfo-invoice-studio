"""Create (idempotently) a sample department/employee/salary/bank account and write a
payslip PDF to test_output/. Payroll is NOT saved unless --save-payroll is given."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from employee_management import payroll as engine
from employee_management.models import (Department, Employee, EmployeeBankAccount, EmployeeSalaryStructure,
                                        PayrollPeriod, PayrollSettings, SalaryComponent)
from employee_management.pdf.payslip import build_payslip_data, payslip_filename, render_payslip


class Command(BaseCommand):
    help = 'Create a sample employee and render a sample payslip into test_output/.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=2026)
        parser.add_argument('--month', type=int, default=11)
        parser.add_argument('--save-payroll', action='store_true',
                            help='Also save a DRAFT payroll record for the month (default: preview only).')
        parser.add_argument('--out', default=None)

    def handle(self, *a, **o):
        from invoice_app.models import BusinessProfile
        dept, _ = Department.objects.get_or_create(code='SAMPLE', defaults={'name': 'Sample Department'})
        emp = Employee.objects.filter(notes__startswith='[sample]').first()
        if emp is None:
            emp = Employee.objects.create(
                first_name='Sample', last_name='Employee', department=dept, designation='Analyst',
                date_of_joining=date(2026, 4, 1), status=Employee.CONFIRMED, pan_number='ABCDE1234F',
                mobile='9876543210', notes='[sample] created by make_sample_employee; safe to edit.')
        if not emp.salary_structures.exists():
            st = EmployeeSalaryStructure.objects.create(employee=emp, effective_from=date(2026, 4, 1))
            for kind, code, amt in (('earning', 'basic', '30000'), ('earning', 'hra', '12000'),
                                    ('earning', 'special', '8000'), ('deduction', 'professional_tax', '200')):
                SalaryComponent.objects.create(structure=st, kind=kind, code=code, amount=Decimal(amt))
        if not emp.bank_accounts.exists():
            EmployeeBankAccount.add_for(emp, bank_name='Sample Bank', account_holder_name=emp.full_name,
                                        account_number='123456789012', ifsc_code='SAMP0001234')
        y, m = o['year'], o['month']
        s = PayrollSettings.current()
        if o['save_payroll']:
            period = engine.get_or_create_period(y, m, s)
        else:
            period = PayrollPeriod.objects.filter(year=y, month=m).first() or PayrollPeriod(
                year=y, month=m, divisor=s.default_divisor)
        from django.core.management import call_command
        call_command('seed_sample_attendance', employee=emp.employee_id, year=y, month=m, stdout=self.stdout)
        inputs, summary = engine.attendance_inputs(emp, period)  # attendance -> payroll input point
        calc = engine.calculate(emp, period, inputs)
        if calc.errors:
            self.stderr.write('; '.join(calc.errors))
            return
        if o['save_payroll']:
            saved = engine.write_draft(calc, source='attendance', snapshot=summary.as_json()) if not period.payrolls.filter(employee=emp).exists() else \
                period.payrolls.filter(employee=emp, is_current=True).first()
            data = build_payslip_data(saved, s, BusinessProfile.current())
        else:
            data = build_payslip_data(calc, s, BusinessProfile.current())
        out_dir = Path(o['out'] or settings.BASE_DIR / 'test_output')
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / payslip_filename(data.employee_code, data.month_label)
        path.write_bytes(render_payslip(data))
        self.stdout.write(f'Employee {emp.employee_id}: gross {calc.gross}, deductions {calc.total_deductions}, '
                          f'net {calc.net}')
        self.stdout.write(f'Payslip written: {path}')
