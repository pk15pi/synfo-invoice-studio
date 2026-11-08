from django.contrib import admin

from .models import (CandidateApplication, CandidateStageHistory, PerformanceSettings, PerformanceStage,
                     RecruiterActivity, RecruiterActivityHistory, RecruiterAssignment, RecruitmentJD)


@admin.register(PerformanceStage)
class PerformanceStageAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'sort_order', 'active')
    list_filter = ('kind', 'active')
    search_fields = ('name',)


@admin.register(PerformanceSettings)
class PerformanceSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'joined_weight', 'selected_weight', 'quality_weight', 'jd_weight', 'financial_year_start_month')


@admin.register(RecruitmentJD)
class RecruitmentJDAdmin(admin.ModelAdmin):
    list_display = ('reference', 'job_title', 'client', 'status', 'opening_date')
    list_filter = ('status',)
    search_fields = ('reference', 'job_title', 'client_name', 'client__name')


@admin.register(RecruiterAssignment)
class RecruiterAssignmentAdmin(admin.ModelAdmin):
    list_display = ('jd', 'recruiter', 'started_on', 'status')
    list_filter = ('status',)
    search_fields = ('jd__reference', 'jd__job_title', 'recruiter__employee_id', 'recruiter__first_name', 'recruiter__last_name')


@admin.register(RecruiterActivity)
class RecruiterActivityAdmin(admin.ModelAdmin):
    list_display = ('activity_date', 'assignment', 'resumes_submitted', 'resumes_rejected', 'candidates_selected')
    list_filter = ('activity_date',)
    date_hierarchy = 'activity_date'


@admin.register(CandidateApplication)
class CandidateApplicationAdmin(admin.ModelAdmin):
    list_display = ('candidate', 'assignment', 'submission_date', 'stage', 'outcome')
    list_filter = ('outcome', 'stage')
    search_fields = ('candidate__candidate_name', 'candidate__candidate_code', 'assignment__jd__reference')


@admin.register(CandidateStageHistory)
class CandidateStageHistoryAdmin(admin.ModelAdmin):
    list_display = ('application', 'effective_date', 'previous_stage', 'current_stage', 'current_outcome')
    readonly_fields = [field.name for field in CandidateStageHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecruiterActivityHistory)
class RecruiterActivityHistoryAdmin(admin.ModelAdmin):
    list_display = ('activity', 'recorded_at')
    readonly_fields = [field.name for field in RecruiterActivityHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Remaining models (Daily Brief) get the shared generated admin; history is read-only.
from invoice_app.admin_auto import register_all  # noqa: E402

register_all('performance_review', read_only=('DailyBriefHistory',))

