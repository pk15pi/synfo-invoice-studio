import calendar
from datetime import date
from io import BytesIO

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from employee_management.models import Department, Employee
from invoice_app.models import Client, RecruitmentCandidate
from .exports import report_pdf, report_xlsx
from .models import (CandidateApplication, CandidateStageHistory, PerformanceSettings, PerformanceStage,
                     RecruiterActivity, RecruiterAssignment, RecruitmentJD)
from .services import build_report, period_bounds


class PerformanceReviewTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Recruitment', code='REC')
        self.recruiter = Employee.objects.create(employee_id='EMP-R1', first_name='Riya', last_name='Shah',
            department=self.department, designation='Recruiter', date_of_joining=date(2024, 1, 1),
            status=Employee.CONFIRMED, is_recruiter=True)
        self.client_record = Client.objects.create(name='Example Client')
        self.jd = RecruitmentJD.objects.create(reference='JD-TEST-1', client=self.client_record,
            job_title='Analyst', opening_date=date(2026, 10, 1))
        self.assignment = RecruiterAssignment.objects.create(jd=self.jd, recruiter=self.recruiter,
            started_on=date(2026, 10, 1))
        self.candidate = RecruitmentCandidate.objects.create(candidate_name='Candidate One')
        self.stage = PerformanceStage.objects.get(kind=PerformanceStage.STAGE, name='Resume Submitted')
        self.screen = PerformanceStage.objects.get(kind=PerformanceStage.STAGE, name='Technical Interview')
        self.reason = PerformanceStage.objects.get(kind=PerformanceStage.REJECTION_REASON, name='Skills mismatch')

    def add_application(self, **changes):
        data = dict(assignment=self.assignment, candidate=self.candidate, submission_date=date(2026, 10, 6),
                    stage=self.stage, outcome='pending', last_transition_date=date(2026, 10, 6))
        data.update(changes)
        obj = CandidateApplication(**data)
        obj.full_clean()
        obj.save()
        return obj

    def test_unique_jd_and_multiple_jd_assignment_counts(self):
        another = Employee.objects.create(employee_id='EMP-R2', first_name='Mina', department=self.department,
            designation='Recruiter', date_of_joining=date(2024, 1, 1), status=Employee.ACTIVE, is_recruiter=True)
        second_assignment = RecruiterAssignment.objects.create(jd=self.jd, recruiter=another,
            started_on=date(2026, 10, 2))
        self.assertEqual(self.jd.assignments.count(), 2)
        self.assertEqual(period_bounds({'period': 'custom', 'from_date': '2026-10-01', 'to_date': '2026-10-31'})['start'], date(2026, 10, 1))
        report = build_report(period_bounds({'period': 'custom', 'from_date': '2026-10-01', 'to_date': '2026-10-31'}))
        self.assertEqual(report['cards']['unique_jds'], 1)
        self.assertEqual(report['cards']['assignments'], 2)
        self.assertNotEqual(second_assignment.recruiter_id, self.assignment.recruiter_id)

    def test_duplicate_candidate_assignment_is_blocked_but_other_jd_is_allowed(self):
        self.add_application()
        duplicate = CandidateApplication(assignment=self.assignment, candidate=self.candidate,
            submission_date=date(2026, 10, 7), stage=self.stage, outcome='pending')
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        other_jd = RecruitmentJD.objects.create(reference='JD-TEST-2', job_title='Designer')
        other_assignment = RecruiterAssignment.objects.create(jd=other_jd, recruiter=self.recruiter,
            started_on=date(2026, 10, 7))
        application = CandidateApplication(assignment=other_assignment, candidate=self.candidate,
            submission_date=date(2026, 10, 7), stage=self.stage)
        application.full_clean()
        application.save()

    def test_rejection_requires_a_rejection_stage_and_history_is_retained(self):
        app = CandidateApplication(assignment=self.assignment, candidate=self.candidate,
            submission_date=date(2026, 10, 6), stage=self.stage, outcome='rejected', rejected_date=date(2026, 10, 8),
            rejection_reason=self.reason)
        with self.assertRaises(ValidationError):
            app.full_clean()
        app.rejection_stage = self.screen
        app.full_clean()
        app.save()
        app.stage = self.screen
        app.outcome = 'selected'
        app.selected_date = date(2026, 10, 15)
        app.rejected_date = None
        app.last_transition_date = date(2026, 10, 15)
        app.rejection_stage = None
        app.rejection_reason = None
        app.save()
        self.assertEqual(CandidateStageHistory.objects.filter(application=app).count(), 2)
        self.assertEqual(app.history.first().previous_stage, self.stage)

    def test_candidate_verified_and_summary_totals_are_separate(self):
        self.add_application(outcome='selected', selected_date=date(2026, 10, 10))
        joined_candidate = RecruitmentCandidate.objects.create(candidate_name='Candidate Joined')
        self.add_application(candidate=joined_candidate, stage=self.screen, outcome='joined',
            selected_date=date(2026, 10, 11), offer_released_date=date(2026, 10, 12),
            offer_accepted_date=date(2026, 10, 14), joined_date=date(2026, 10, 20))
        pending_candidate = RecruitmentCandidate.objects.create(candidate_name='Candidate Pending')
        self.add_application(candidate=pending_candidate)
        RecruiterActivity.objects.create(assignment=self.assignment, activity_date=date(2026, 10, 10),
            resumes_submitted=5, candidates_selected=2)
        report = build_report(period_bounds({'period': 'custom', 'from_date': '2026-10-01', 'to_date': '2026-10-31'}))
        self.assertEqual(report['cards']['submissions'], 3)
        self.assertEqual(report['cards']['summary_submissions'], 5)
        self.assertEqual(report['cards']['selected'], 2)
        self.assertEqual(report['cards']['summary_selected'], 2)
        self.assertEqual(report['cards']['joined'], 1)
        self.assertEqual(report['ranking'][0]['score'], 100)
        self.assertEqual(report['metrics'][0]['verified']['unique_candidates'], 3)

    def test_summary_record_edits_append_auditable_before_and_after_values(self):
        activity = RecruiterActivity.objects.create(assignment=self.assignment, activity_date=date(2026, 10, 10),
            resumes_submitted=5, remarks='initial')
        activity.resumes_submitted = 7
        activity.remarks = 'corrected source total'
        activity.save()
        change = activity.history.get()
        self.assertEqual(change.previous_values['resumes_submitted'], 5)
        self.assertEqual(change.current_values['resumes_submitted'], 7)

    def test_candidate_selection_is_unique_across_recruiters_for_the_same_jd(self):
        another = Employee.objects.create(employee_id='EMP-R2', first_name='Mina', department=self.department,
            designation='Recruiter', date_of_joining=date(2024, 1, 1), status=Employee.ACTIVE, is_recruiter=True)
        other_assignment = RecruiterAssignment.objects.create(jd=self.jd, recruiter=another,
            started_on=date(2026, 10, 2))
        self.add_application(outcome='selected', selected_date=date(2026, 10, 15))
        duplicate = CandidateApplication(assignment=other_assignment, candidate=self.candidate,
            submission_date=date(2026, 10, 5), stage=self.stage, outcome='selected', selected_date=date(2026, 10, 16))
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_selected_financial_quarter_uses_april_start(self):
        period = period_bounds({'period': 'selected_quarter', 'fy_year': '2026', 'quarter': '3'})
        self.assertEqual((period['start'], period['end']), (date(2026, 10, 1), date(2026, 12, 31)))

    def test_month_quarter_calendar_year_financial_year_and_custom_periods(self):
        today = timezone.localdate()
        this_month = period_bounds({'period': 'this_month'})
        self.assertEqual(this_month['start'], today.replace(day=1))
        prev_year, prev_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        previous = period_bounds({'period': 'previous_month'})
        self.assertEqual((previous['start'], previous['end']),
            (date(prev_year, prev_month, 1), date(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])))
        annual = period_bounds({'period': 'selected_year', 'year': '2025'})
        self.assertEqual((annual['start'], annual['end']), (date(2025, 1, 1), date(2025, 12, 31)))
        fy = period_bounds({'period': 'selected_fy', 'fy_year': '2025'})
        self.assertEqual((fy['start'], fy['end']), (date(2025, 4, 1), date(2026, 3, 31)))
        selected_month = period_bounds({'period': 'selected_month', 'year': '2025', 'month': '2'})
        self.assertEqual((selected_month['start'], selected_month['end']), (date(2025, 2, 1), date(2025, 2, 28)))
        custom = period_bounds({'period': 'custom', 'from_date': '2025-05-06', 'to_date': '2025-06-07'})
        self.assertEqual((custom['start'], custom['end']), (date(2025, 5, 6), date(2025, 6, 7)))

    def test_score_weights_must_total_one_hundred(self):
        settings = PerformanceSettings(joined_weight=40, selected_weight=30, quality_weight=10, jd_weight=10)
        with self.assertRaises(ValidationError):
            settings.full_clean()

    def test_application_edit_keeps_history_and_report_exports_work(self):
        app = self.add_application()
        response = self.client.post(reverse('performance_review:application_edit', args=[app.pk]), {
            'assignment': self.assignment.pk, 'candidate': self.candidate.pk, 'submission_date': '2026-10-06',
            'stage': self.screen.pk, 'outcome': 'joined', 'joined_date': '2026-10-20',
            'selected_date': '2026-10-10', 'offer_released_date': '2026-10-15',
            'offer_accepted_date': '2026-10-18', 'last_transition_date': '2026-10-20',
            'rejection_stage': '', 'rejection_reason': '', 'rejected_date': '', 'remarks': ''})
        # The app follows the existing local management-only architecture, not staff-login gating.
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(app.outcome, 'joined')
        self.assertEqual(app.history.count(), 2)
        report = build_report(period_bounds({'period': 'custom', 'from_date': '2026-10-01', 'to_date': '2026-10-31'}))
        workbook = load_workbook(BytesIO(report_xlsx(report)))
        self.assertEqual(workbook.sheetnames, ['Overall Summary', 'Recruiter Performance', 'JD-wise Performance',
            'Candidate Outcomes', 'Rejection Analysis', 'Monthly Trend', 'Quarterly Summary'])
        self.assertTrue(report_pdf(report).startswith(b'%PDF'))

    def test_dashboard_help_and_export_filters_are_available(self):
        dashboard = self.client.get(reverse('performance_review:dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Summary-only activity')
        self.assertIsNotNone(dashboard.context['page_help_guide'])
        report = self.client.get(reverse('performance_review:reports'), {'period': 'custom',
            'from_date': '2026-10-01', 'to_date': '2026-10-31', 'export': 'xlsx'})
        self.assertEqual(report.status_code, 200)
        self.assertIn('spreadsheetml', report['Content-Type'])
        self.assertIn('20261001_20261031', report['Content-Disposition'])

    def test_page_help_coverage_for_each_module_screen(self):
        app = self.add_application()
        activity = RecruiterActivity.objects.create(assignment=self.assignment, activity_date=date(2026, 10, 10))
        paths = [reverse('performance_review:dashboard'), reverse('performance_review:activity'),
                 reverse('performance_review:records'), reverse('performance_review:reports'),
                 reverse('performance_review:settings'),
                 reverse('performance_review:application_edit', args=[app.pk]),
                 reverse('performance_review:activity_edit', args=[activity.pk]),
                 reverse('performance_review:jd_edit', args=[self.jd.pk]),
                 reverse('performance_review:stage_edit', args=[self.stage.pk])]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIsNotNone(response.context['page_help_guide'], path)

