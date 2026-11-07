from datetime import date, timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from employee_management.models import Department, Employee
from invoice_app.models import RecruitmentCandidate

from .forms import DailyBriefForm, DailyBriefJDDetailFormSet
from .models import (DailyBrief, DailyBriefHistory, DailyBriefJDDetail, PerformanceSettings,
                     RecruitmentJD)
from .services import (daily_brief_analytics, daily_brief_filtered_querysets,
                       daily_brief_period_bounds)


class DailyBriefTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Daily Brief Recruitment', code='DBR')
        self.recruiter = Employee.objects.create(employee_id='EMP-DB1', first_name='Riya', last_name='Shah',
            department=self.department, designation='Recruiter', date_of_joining=date(2024, 1, 1),
            status=Employee.CONFIRMED, is_recruiter=True)
        self.other_recruiter = Employee.objects.create(employee_id='EMP-DB2', first_name='Asha', last_name='Rao',
            department=self.department, designation='Recruiter', date_of_joining=date(2024, 1, 1),
            status=Employee.ACTIVE, is_recruiter=True)

    def make_brief(self, recruiter=None, activity_date=date(2026, 10, 15), rows=None):
        brief = DailyBrief.objects.create(recruiter=recruiter or self.recruiter, activity_date=activity_date)
        for order, (number, resumes) in enumerate(rows or [('07-01', 12)]):
            DailyBriefJDDetail.objects.create(daily_brief=brief, jd_number=number,
                                               total_resumes_submitted=resumes, row_order=order)
        brief.refresh_from_db()
        return brief

    def test_parent_child_models_calculate_jd_and_resume_totals(self):
        brief = self.make_brief(rows=[('07-01', 12), ('07-02', 8), ('07-03', 0)])
        self.assertEqual(brief.total_jds_submitted, 3)
        self.assertEqual(brief.total_resumes_submitted, 20)
        brief.jd_details.first().delete()
        brief.refresh_from_db()
        self.assertEqual(brief.total_jds_submitted, 2)
        self.assertEqual(brief.total_resumes_submitted, 8)

    def test_only_employee_is_external_business_foreign_key(self):
        parent_links = [f for f in DailyBrief._meta.local_fields if f.many_to_one]
        detail_links = [f for f in DailyBriefJDDetail._meta.local_fields if f.many_to_one]
        history_links = [f for f in DailyBriefHistory._meta.local_fields if f.many_to_one]
        self.assertEqual([(f.name, f.related_model) for f in parent_links], [('recruiter', Employee)])
        self.assertEqual([(f.name, f.related_model) for f in detail_links], [('daily_brief', DailyBrief)])
        self.assertEqual([(f.name, f.related_model) for f in history_links], [('daily_brief', DailyBrief)])
        self.assertFalse(hasattr(DailyBriefJDDetail, 'jd_id'))

    def test_database_prevents_duplicate_recruiter_date(self):
        self.make_brief()
        with self.assertRaises(IntegrityError), self.atomic_block():
            DailyBrief.objects.create(recruiter=self.recruiter, activity_date=date(2026, 10, 15))

    def atomic_block(self):
        from django.db import transaction
        return transaction.atomic()

    def _formset_data(self, rows):
        data = {'details-TOTAL_FORMS': str(len(rows)), 'details-INITIAL_FORMS': '0',
                'details-MIN_NUM_FORMS': '1', 'details-MAX_NUM_FORMS': '100'}
        for index, (jd_number, resume_count) in enumerate(rows):
            data[f'details-{index}-jd_number'] = jd_number
            data[f'details-{index}-total_resumes_submitted'] = resume_count
            data[f'details-{index}-row_order'] = str(index)
            data[f'details-{index}-row_active'] = '1'
        return data

    def test_form_requires_recruiter_and_date_and_limits_recruiters_to_active_flagged_employees(self):
        blank_form = DailyBriefForm()
        self.assertEqual(blank_form['activity_date'].value(), timezone.localdate())
        form = DailyBriefForm(data={'recruiter': self.recruiter.pk, 'activity_date': '2026-10-15'})
        self.assertTrue(form.is_valid(), form.errors)
        invalid = DailyBriefForm(data={'recruiter': '', 'activity_date': ''})
        self.assertFalse(invalid.is_valid())
        inactive = Employee.objects.create(employee_id='EMP-DB3', first_name='Former',
            department=self.department, designation='Recruiter', date_of_joining=date(2020, 1, 1),
            status=Employee.LEFT, is_recruiter=True)
        self.assertNotIn(inactive, form.fields['recruiter'].queryset)

    def test_formset_requires_one_complete_row_and_rejects_invalid_or_duplicate_details(self):
        empty = DailyBriefJDDetailFormSet(data=self._formset_data([('', '')]), instance=DailyBrief(), prefix='details')
        self.assertFalse(empty.is_valid())
        missing_resume = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '')]),
                                                   instance=DailyBrief(), prefix='details')
        self.assertFalse(missing_resume.is_valid())
        negative = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '-1')]),
                                             instance=DailyBrief(), prefix='details')
        self.assertFalse(negative.is_valid())
        decimal = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '1.5')]),
                                            instance=DailyBrief(), prefix='details')
        self.assertFalse(decimal.is_valid())
        duplicate = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '1'), ('07-01', '2')]),
                                              instance=DailyBrief(), prefix='details')
        self.assertFalse(duplicate.is_valid())
        self.assertIn('duplicate', str(duplicate.non_form_errors()).lower())
        case_duplicate = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '1'), ('07-01 ', '2')]),
                                                   instance=DailyBrief(), prefix='details')
        self.assertFalse(case_duplicate.is_valid())
        valid = DailyBriefJDDetailFormSet(data=self._formset_data([('07-01', '0'), ('07-02', '12')]),
                                          instance=DailyBrief(), prefix='details')
        self.assertTrue(valid.is_valid(), valid.errors)

    def test_create_is_atomic_and_duplicate_daily_brief_is_linked_for_review(self):
        payload = {'recruiter': self.recruiter.pk, 'activity_date': '2026-10-15',
                   **self._formset_data([('07-01', '12')])}
        with patch.object(DailyBriefJDDetail, 'save', side_effect=RuntimeError('simulated child failure')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('performance_review:daily_brief_records'), payload)
        self.assertEqual(DailyBrief.objects.count(), 0)
        response = self.client.post(reverse('performance_review:daily_brief_records'), payload)
        self.assertRedirects(response, reverse('performance_review:daily_brief_records'))
        brief = DailyBrief.objects.get()
        self.assertEqual(brief.total_jds_submitted, 1)
        self.assertEqual(brief.total_resumes_submitted, 12)
        duplicate_response = self.client.post(reverse('performance_review:daily_brief_records'), payload)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertContains(duplicate_response, 'Review the existing record')
        self.assertContains(duplicate_response, reverse('performance_review:daily_brief_detail', args=[brief.pk]))
        self.assertEqual(DailyBrief.objects.count(), 1)
        self.assertEqual(RecruitmentJD.objects.count(), 0)
        self.assertEqual(RecruitmentCandidate.objects.count(), 0)

    def test_edit_preserves_before_and_after_snapshots_and_rejects_duplicate_date(self):
        brief = self.make_brief(rows=[('07-01', 12)])
        payload = {'recruiter': self.recruiter.pk, 'activity_date': '2026-10-16',
                   **{'details-TOTAL_FORMS': '2', 'details-INITIAL_FORMS': '1',
                      'details-MIN_NUM_FORMS': '1', 'details-MAX_NUM_FORMS': '100',
                      'details-0-id': brief.jd_details.get().pk, 'details-0-jd_number': '07-01',
                      'details-0-daily_brief': str(brief.pk),
                      'details-0-total_resumes_submitted': '15', 'details-0-row_order': '0',
                      'details-0-row_active': '1', 'details-1-jd_number': '',
                      'details-1-total_resumes_submitted': '', 'details-1-row_order': '0',
                      'details-1-row_active': '', 'details-1-daily_brief': str(brief.pk)}}
        response = self.client.post(reverse('performance_review:daily_brief_edit', args=[brief.pk]), payload)
        self.assertRedirects(response, reverse('performance_review:daily_brief_detail', args=[brief.pk]))
        brief.refresh_from_db()
        self.assertEqual(brief.total_resumes_submitted, 15)
        change = brief.history.get()
        self.assertEqual(change.previous_values['activity_date'], '2026-10-15')
        self.assertEqual(change.current_values['activity_date'], '2026-10-16')
        self.assertEqual(change.previous_values['jd_details'][0]['total_resumes_submitted'], 12)
        self.assertEqual(change.current_values['jd_details'][0]['total_resumes_submitted'], 15)

    def test_edit_duplicate_date_warns_and_links_existing_brief(self):
        current = self.make_brief(activity_date=date(2026, 10, 15), rows=[('07-01', 12)])
        duplicate = self.make_brief(activity_date=date(2026, 10, 16), rows=[('08-01', 4)])
        payload = {'recruiter': self.recruiter.pk, 'activity_date': '2026-10-16',
                   'details-TOTAL_FORMS': '2', 'details-INITIAL_FORMS': '1',
                   'details-MIN_NUM_FORMS': '1', 'details-MAX_NUM_FORMS': '100',
                   'details-0-id': current.jd_details.get().pk, 'details-0-daily_brief': current.pk,
                   'details-0-jd_number': '07-01', 'details-0-total_resumes_submitted': '12',
                   'details-0-row_order': '0', 'details-0-row_active': '1',
                   'details-1-daily_brief': current.pk, 'details-1-jd_number': '',
                   'details-1-total_resumes_submitted': '', 'details-1-row_order': '0',
                   'details-1-row_active': ''}
        response = self.client.post(reverse('performance_review:daily_brief_edit', args=[current.pk]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Review the existing record')
        self.assertContains(response, reverse('performance_review:daily_brief_detail', args=[duplicate.pk]))
        current.refresh_from_db()
        self.assertEqual(current.activity_date, date(2026, 10, 15))

    def test_edit_can_delete_a_saved_jd_row_without_deleting_the_brief(self):
        brief = self.make_brief(rows=[('07-01', 12), ('07-02', 8)])
        rows = list(brief.jd_details.order_by('row_order'))
        payload = {
            'recruiter': self.recruiter.pk, 'activity_date': '2026-10-15',
            'details-TOTAL_FORMS': '3', 'details-INITIAL_FORMS': '2',
            'details-MIN_NUM_FORMS': '1', 'details-MAX_NUM_FORMS': '100',
        }
        for index, detail in enumerate(rows):
            payload.update({
                f'details-{index}-id': str(detail.pk), f'details-{index}-daily_brief': str(brief.pk),
                f'details-{index}-jd_number': detail.jd_number,
                f'details-{index}-total_resumes_submitted': str(detail.total_resumes_submitted),
                f'details-{index}-row_order': str(index), f'details-{index}-row_active': '1',
                f'details-{index}-DELETE': '1' if index == 0 else '',
            })
        payload.update({'details-2-daily_brief': str(brief.pk), 'details-2-jd_number': '',
                        'details-2-total_resumes_submitted': '', 'details-2-row_order': '0',
                        'details-2-row_active': '', 'details-2-DELETE': ''})
        response = self.client.post(reverse('performance_review:daily_brief_edit', args=[brief.pk]), payload)
        self.assertRedirects(response, reverse('performance_review:daily_brief_detail', args=[brief.pk]))
        brief.refresh_from_db()
        self.assertEqual(brief.total_jds_submitted, 1)
        self.assertEqual(brief.total_resumes_submitted, 8)
        self.assertEqual(list(brief.jd_details.values_list('jd_number', flat=True)), ['07-02'])
        history = brief.history.get()
        self.assertEqual(len(history.previous_values['jd_details']), 2)
        self.assertEqual(len(history.current_values['jd_details']), 1)

    def test_period_filters_cover_calendar_week_fifteen_day_and_financial_year_boundaries(self):
        settings = PerformanceSettings(financial_year_start_month=4)
        calendar_year = daily_brief_period_bounds({'period': 'selected_year', 'year': '2025'}, settings)
        self.assertEqual((calendar_year['start'], calendar_year['end']), (date(2025, 1, 1), date(2025, 12, 31)))
        week = daily_brief_period_bounds({'period': 'weekly', 'reference_date': '2026-04-01'}, settings)
        self.assertEqual((week['start'], week['end']), (date(2026, 3, 30), date(2026, 4, 5)))
        fifteen = daily_brief_period_bounds({'period': 'fifteen_days', 'reference_date': '2026-04-10'}, settings)
        self.assertEqual((fifteen['start'], fifteen['end']), (date(2026, 3, 27), date(2026, 4, 10)))
        month = daily_brief_period_bounds({'period': 'monthly', 'year': '2024', 'month': '2'}, settings)
        self.assertEqual((month['start'], month['end']), (date(2024, 2, 1), date(2024, 2, 29)))
        quarter = daily_brief_period_bounds({'period': 'quarterly', 'fy_year': '2026', 'quarter': '1'}, settings)
        self.assertEqual((quarter['start'], quarter['end']), (date(2026, 4, 1), date(2026, 6, 30)))
        half = daily_brief_period_bounds({'period': 'half_yearly', 'fy_year': '2026', 'half': '2'}, settings)
        self.assertEqual((half['start'], half['end']), (date(2026, 10, 1), date(2027, 3, 31)))
        year = daily_brief_period_bounds({'period': 'yearly', 'fy_year': '2026'}, settings)
        self.assertEqual((year['start'], year['end']), (date(2026, 4, 1), date(2027, 3, 31)))
        custom = daily_brief_period_bounds({'period': 'custom', 'from_date': '2026-04-04', 'to_date': '2026-04-09'}, settings)
        self.assertEqual((custom['start'], custom['end']), (date(2026, 4, 4), date(2026, 4, 9)))

    def test_filters_use_activity_date_and_combine_recruiter_and_jd_searches(self):
        selected = self.make_brief(activity_date=date(2026, 4, 5), rows=[('07-01', 12), ('07-02', 8)])
        other = self.make_brief(recruiter=self.other_recruiter, activity_date=date(2026, 4, 6),
                                rows=[('08-01', 5)])
        period = daily_brief_period_bounds({'period': 'custom', 'from_date': '2026-04-01', 'to_date': '2026-04-30'})
        briefs, details = daily_brief_filtered_querysets(period, {'recruiter_search': 'Riya Shah', 'jd_search': '07-02'})
        self.assertEqual(list(briefs), [selected])
        self.assertEqual(list(details.values_list('jd_number', flat=True)), ['07-02'])
        briefs, _ = daily_brief_filtered_querysets(period, {'recruiter': str(self.other_recruiter.pk)})
        self.assertEqual(list(briefs), [other])

    def test_kpis_and_all_charts_aggregate_only_daily_brief_rows(self):
        self.make_brief(activity_date=date(2026, 4, 5), rows=[('07-01', 12), ('07-02', 8)])
        self.make_brief(activity_date=date(2026, 4, 6), rows=[('07-01', 5)])
        self.make_brief(recruiter=self.other_recruiter, activity_date=date(2026, 4, 6), rows=[('08-01', 0)])
        period = daily_brief_period_bounds({'period': 'custom', 'from_date': '2026-04-01', 'to_date': '2026-04-30'})
        briefs, details = daily_brief_filtered_querysets(period, {})
        result = daily_brief_analytics(period, briefs, details)
        self.assertEqual(result['total_jds'], 4)
        self.assertEqual(result['total_resumes'], 25)
        self.assertEqual(result['kpis'][2], ('Recruiters Active', 2))
        self.assertEqual(result['kpis'][3], ('Daily Briefs Submitted', 3))
        self.assertEqual(result['average_resumes_per_jd'], 6.25)
        self.assertEqual(result['average_jds_per_recruiter'], 2)
        self.assertEqual(len(result['charts']), 7)
        self.assertEqual(len(result['comparison']), 2)
        self.assertNotIn('₹', result['charts'][0][1])
        self.assertIn('8.33', result['charts'][6][1])
        filtered_briefs, filtered_details = daily_brief_filtered_querysets(period, {'jd_search': '07-01'})
        filtered = daily_brief_analytics(period, filtered_briefs, filtered_details)
        self.assertEqual(filtered['total_jds'], 2)
        self.assertEqual(filtered['total_resumes'], 17)

    def test_records_analytics_detail_edit_routes_and_help_guides_render(self):
        brief = self.make_brief(rows=[('07-01', 12)])
        paths = [
            reverse('performance_review:daily_brief_records'),
            reverse('performance_review:daily_brief_analytics'),
            reverse('performance_review:daily_brief_detail', args=[brief.pk]),
            reverse('performance_review:daily_brief_edit', args=[brief.pk]),
        ]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIsNotNone(response.context['page_help_guide'], path)
            self.assertContains(response, 'qb-page-help-data')
        entry = self.client.get(paths[0])
        self.assertContains(entry, 'data-add-brief-row')
        self.assertContains(entry, 'data-remove-brief-row')
        self.assertContains(entry, 'data-delete-flag')
        analytics = self.client.get(paths[1])
        self.assertEqual(len(analytics.context['charts']), 7)

    def test_empty_period_analytics_are_zero_and_show_empty_chart_states(self):
        period = daily_brief_period_bounds({'period': 'custom', 'from_date': '2020-01-01', 'to_date': '2020-01-10'})
        briefs, details = daily_brief_filtered_querysets(period, {})
        result = daily_brief_analytics(period, briefs, details)
        self.assertEqual(result['total_jds'], 0)
        self.assertEqual(result['total_resumes'], 0)
        self.assertEqual(result['average_resumes_per_jd'], 0)
        self.assertEqual(result['average_jds_per_recruiter'], 0)
        self.assertTrue(all('No data for this period' in chart[1] for chart in result['charts']))
