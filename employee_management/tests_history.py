"""History must not move: editing an employee, department or salary structure after payroll is finalized leaves
past payroll rows, payslip data/PDFs, journal entries and reports exactly as they were, and is audited."""
import dataclasses
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

from django.test import TestCase, override_settings

from accounting.models import JournalEntry
from audit.models import AuditEvent
from employee_management import payroll as engine
from employee_management.forms import SalaryStructureForm
from employee_management.models import (Department, Employee, EmployeePayroll, EmployeeSalaryStructure,
                                        FinalizedRecordError, PayrollPeriod, SalaryComponent)
from employee_management.pdf import payslip as PS
from reporting import builder

AADHAAR, PAN = '234567890124', 'ABCDE1234F'


def payroll_state(p):
    p = EmployeePayroll.objects.get(pk=p.pk)
    lines = [(x.code, x.label, x.amount) for x in p.earnings.all()] + \
            [(x.code, x.label, x.amount) for x in p.deductions.all()]
    return dict(dept=p.snap_department, desig=p.snap_designation, name=p.snap_name, rate=p.snap_rate,
                gross=p.gross_earnings, ded=p.total_deductions, net=p.net_salary, structure=p.structure_id,
                lines=sorted(lines))


def journal_state():
    return [(je.pk, je.status, sorted((ln.account_id, ln.debit, ln.credit, ln.description) for ln in je.lines.all()))
            for je in JournalEntry.objects.order_by('pk')]


class HistoryImmutabilityTests(TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix='qbhist-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        ov = override_settings(EMPLOYEE_PRIVATE_ROOT=tmp / 'priv', MEDIA_ROOT=tmp / 'media', BACKUP_DIR=tmp / 'bk')
        ov.enable()
        self.addCleanup(ov.disable)
        self.dept = Department.objects.create(name='Dev Ops', code='DEVX')
        self.other = Department.objects.create(name='Sales', code='SALX')
        self.emp = Employee.objects.create(first_name='Asha', last_name='Rao', department=self.dept,
                                           designation='Developer', date_of_joining=date(2026, 1, 1),
                                           status=Employee.CONFIRMED, aadhaar_number=AADHAAR, pan_number=PAN)
        self.st = EmployeeSalaryStructure.objects.create(employee=self.emp, effective_from=date(2026, 1, 1),
                                                         divisor='fixed30')
        self.basic = SalaryComponent.objects.create(structure=self.st, kind='earning', code='basic', amount=D('40000'))
        SalaryComponent.objects.create(structure=self.st, kind='earning', code='hra', amount=D('20000'))
        self.period = PayrollPeriod.objects.create(year=2026, month=3)
        engine.generate(self.period)
        self.p = EmployeePayroll.objects.get(period=self.period, employee=self.emp, is_current=True)
        engine.finalize([self.p])
        self.p.refresh_from_db()

    def snapshot_all(self):
        data = dataclasses.asdict(PS.build_payslip_data(EmployeePayroll.objects.get(pk=self.p.pk),
                                                        engine.PayrollSettings.current(), _profile()))
        data.pop('signature', None)
        rep = list(builder._payroll(SimpleNamespace(start=self.period.start, end=self.period.end)))
        return payroll_state(self.p), data, journal_state(), rep

    def test_edits_after_finalize_leave_history_unchanged_and_are_audited(self):
        self.assertEqual(self.p.net_salary, D('60000.00'))
        self.assertTrue(JournalEntry.objects.exists())
        before = self.snapshot_all()
        pdf_before = PS.payslip_for(EmployeePayroll.objects.get(pk=self.p.pk))[0]
        n0 = AuditEvent.objects.count()

        # rename the department, move the employee, change designation and name
        self.dept.name = 'Engineering'
        self.dept.save()
        self.emp.department = self.other
        self.emp.designation = 'Sales Lead'
        self.emp.first_name = 'Asha Renamed'
        self.emp.save()
        # new salary version through the normal form (creates a new structure, closes the old one)
        form = SalaryStructureForm({'structure_type': 'monthly', 'rate': '0', 'divisor': 'fixed30',
                                    'effective_from': '2026-04-01', 'e_basic': '50000', 'e_hra': '25000'},
                                   employee=self.emp)
        self.assertTrue(form.is_valid(), form.errors)
        new = form.save_structure()
        self.assertNotEqual(new.pk, self.st.pk)
        self.st.refresh_from_db()
        self.assertEqual(self.st.effective_to, date(2026, 3, 31))

        after = self.snapshot_all()
        self.assertEqual(before[0], after[0], 'payroll row changed')
        self.assertEqual(before[1], after[1], 'payslip data changed')
        self.assertEqual(before[2], after[2], 'journal entries changed')
        self.assertEqual(before[3], after[3], 'payroll report changed')
        self.assertEqual(after[1]['department'], 'Dev Ops')
        self.assertEqual(after[1]['designation'], 'Developer')
        # regenerated PDF is built from the same frozen data
        pdf_after = PS.payslip_for(EmployeePayroll.objects.get(pk=self.p.pk))[0]
        self.assertEqual(_pdf_body(pdf_before), _pdf_body(pdf_after))

        ev = AuditEvent.objects.all()[: AuditEvent.objects.count() - n0]
        models = {(e.model, e.action) for e in ev}
        self.assertIn(('Department', 'update'), models)
        self.assertIn(('Employee', 'update'), models)
        self.assertIn(('EmployeeSalaryStructure', 'create'), models)
        self.assertIn(('EmployeeSalaryStructure', 'update'), models)
        self.assertIn(('SalaryComponent', 'create'), models)
        emp_ev = [e for e in ev if e.model == 'Employee'][0]
        self.assertEqual(emp_ev.changes['designation'], ['Developer', 'Sales Lead'])

    def test_used_structure_and_components_cannot_be_mutated(self):
        before = self.snapshot_all()
        self.st.divisor = 'actual'
        with self.assertRaises(FinalizedRecordError):
            self.st.save()
        self.st.refresh_from_db()
        self.basic.amount = D('99999')
        with self.assertRaises(FinalizedRecordError):
            self.basic.save()
        with self.assertRaises(FinalizedRecordError):
            SalaryComponent.objects.get(pk=self.basic.pk).delete()
        with self.assertRaises(FinalizedRecordError):
            SalaryComponent.objects.create(structure=self.st, kind='earning', code='bonus', amount=D('1'))
        self.st.effective_to = date(2026, 2, 28)   # would cut off the finalized March payroll
        with self.assertRaises(FinalizedRecordError):
            self.st.save()
        self.st.refresh_from_db()
        self.st.notes = 'annotated'                # notes are fine
        self.st.save()
        self.assertEqual(before, self.snapshot_all())

    def test_unused_structure_still_editable(self):
        st = EmployeeSalaryStructure.objects.create(employee=self.emp, effective_from=date(2026, 6, 1))
        c = SalaryComponent.objects.create(structure=st, kind='earning', code='basic', amount=D('1'))
        c.amount = D('2')
        c.save()
        st.divisor = 'actual'
        st.save()

    def test_employee_audit_masks_ids(self):
        n0 = AuditEvent.objects.count()
        e = Employee.objects.create(first_name='Mask', department=self.dept, designation='X',
                                    date_of_joining=date(2026, 1, 1), aadhaar_number='345678901234',
                                    pan_number='PQRSX6789Z', passport_number='Z1234567')
        e.aadhaar_number = AADHAAR
        e.save()
        blob = ' '.join(str(x.changes) + x.object_repr + x.reason for x in AuditEvent.objects.all()[:AuditEvent.objects.count() - n0])
        for full in ('345678901234', AADHAAR, 'PQRSX6789Z', 'Z1234567'):
            self.assertNotIn(full, blob)
        self.assertIn('1234', blob)
        self.assertIn('0124', blob)


def _pdf_body(b):
    import re
    return re.sub(rb'/(CreationDate|ModDate|ID)\s*(\(.*?\)|\[.*?\])', b'', b)


def _profile():
    from invoice_app.models import BusinessProfile
    return BusinessProfile.current()