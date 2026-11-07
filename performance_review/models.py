from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class PerformanceStage(models.Model):
    STAGE = 'stage'
    REJECTION_STAGE = 'rejection_stage'
    REJECTION_REASON = 'rejection_reason'
    TYPES = [(STAGE, 'Recruitment stage'), (REJECTION_STAGE, 'Rejection stage'),
             (REJECTION_REASON, 'Rejection reason')]

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=TYPES, default=STAGE)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['kind', 'sort_order', 'name']
        constraints = [models.UniqueConstraint(fields=['kind', 'name'], name='perf_value_kind_name_uniq')]

    def __str__(self):
        return self.name


class PerformanceSettings(models.Model):
    """Scoring weights are configurable but must always add up to 100."""
    joined_weight = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('40.00'))
    selected_weight = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('30.00'))
    quality_weight = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('20.00'))
    jd_weight = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))
    financial_year_start_month = models.PositiveSmallIntegerField(default=4)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'performance settings'

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def clean(self):
        fields = (self.joined_weight, self.selected_weight, self.quality_weight, self.jd_weight)
        errors = {}
        if any(value < 0 or value > 100 for value in fields):
            errors['joined_weight'] = 'Each scoring weight must be between 0 and 100.'
        if sum(fields) != Decimal('100.00'):
            errors['quality_weight'] = 'Scoring weights must total 100%.'
        if not 1 <= self.financial_year_start_month <= 12:
            errors['financial_year_start_month'] = 'Choose a month from January to December.'
        if errors:
            raise ValidationError(errors)


class RecruitmentJD(models.Model):
    STATUSES = [('open', 'Open'), ('on_hold', 'On hold'), ('closed', 'Closed'), ('filled', 'Filled')]
    reference = models.CharField('JD ID', max_length=80, unique=True)
    client = models.ForeignKey('invoice_app.Client', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='performance_jds')
    client_name = models.CharField(max_length=160, blank=True, help_text='Use when this client is not in the customer list.')
    job_title = models.CharField(max_length=180)
    opening_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default='open')
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['reference']

    def clean(self):
        if self.client_id and self.client_name:
            self.client_name = ''

    @property
    def client_display(self):
        return self.client.name if self.client_id else self.client_name

    def __str__(self):
        return f'{self.reference} - {self.job_title}'


class RecruiterAssignment(models.Model):
    STATUSES = [('active', 'Active'), ('completed', 'Completed'), ('inactive', 'Inactive')]
    jd = models.ForeignKey(RecruitmentJD, on_delete=models.PROTECT, related_name='assignments')
    recruiter = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                  related_name='performance_assignments')
    started_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=12, choices=STATUSES, default='active')
    remarks = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['jd__reference', 'recruiter__employee_id']
        constraints = [models.UniqueConstraint(fields=['jd', 'recruiter'], name='perf_jd_recruiter_uniq')]

    def __str__(self):
        return f'{self.jd.reference} - {self.recruiter.employee_id}'


class RecruiterActivity(models.Model):
    """Summary-only totals. Never added to candidate-verified totals in charts or reports."""
    assignment = models.ForeignKey(RecruiterAssignment, on_delete=models.PROTECT, related_name='summary_activities')
    activity_date = models.DateField(default=timezone.localdate)
    resumes_submitted = models.PositiveIntegerField(default=0)
    resumes_rejected = models.PositiveIntegerField(default=0)
    candidates_selected = models.PositiveIntegerField(default=0)
    offers_released = models.PositiveIntegerField(default=0)
    candidates_joined = models.PositiveIntegerField(default=0)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-activity_date', '-id']

    def save(self, *args, **kwargs):
        previous = None
        fields = ('activity_date', 'resumes_submitted', 'resumes_rejected', 'candidates_selected',
                  'offers_released', 'candidates_joined', 'remarks')
        if self.pk:
            previous = RecruiterActivity.objects.filter(pk=self.pk).values(*fields).first()
        super().save(*args, **kwargs)
        current = {field: getattr(self, field).isoformat() if field == 'activity_date' else getattr(self, field)
                   for field in fields}
        if previous:
            old = {field: previous[field].isoformat() if field == 'activity_date' else previous[field] for field in fields}
            if old != current:
                RecruiterActivityHistory.objects.create(activity=self, previous_values=old, current_values=current)


class CandidateApplication(models.Model):
    OUTCOMES = [('pending', 'Pending'), ('rejected', 'Rejected'), ('selected', 'Selected'),
                ('offer_released', 'Offer released'), ('offer_accepted', 'Offer accepted'),
                ('joined', 'Joined'), ('on_hold', 'On hold'), ('withdrawn', 'Withdrawn')]
    assignment = models.ForeignKey(RecruiterAssignment, on_delete=models.PROTECT, related_name='applications')
    candidate = models.ForeignKey('invoice_app.RecruitmentCandidate', on_delete=models.PROTECT,
                                  related_name='performance_applications')
    submission_date = models.DateField(default=timezone.localdate)
    stage = models.ForeignKey(PerformanceStage, on_delete=models.PROTECT, related_name='applications')
    outcome = models.CharField(max_length=20, choices=OUTCOMES, default='pending')
    rejection_stage = models.ForeignKey(PerformanceStage, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='rejected_applications')
    rejection_reason = models.ForeignKey(PerformanceStage, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name='application_rejection_reasons')
    selected_date = models.DateField(null=True, blank=True)
    rejected_date = models.DateField(null=True, blank=True)
    offer_released_date = models.DateField(null=True, blank=True)
    offer_accepted_date = models.DateField(null=True, blank=True)
    joined_date = models.DateField(null=True, blank=True)
    last_transition_date = models.DateField(default=timezone.localdate)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submission_date', 'candidate__candidate_name']
        constraints = [models.UniqueConstraint(fields=['assignment', 'candidate'], name='perf_assignment_candidate_uniq')]

    def clean(self):
        errors = {}
        if self.stage_id and self.stage.kind != PerformanceStage.STAGE:
            errors['stage'] = 'Choose a configured recruitment stage.'
        if self.rejection_stage_id and self.rejection_stage.kind not in (PerformanceStage.STAGE, PerformanceStage.REJECTION_STAGE):
            errors['rejection_stage'] = 'Choose a configured recruitment rejection stage.'
        if self.rejection_reason_id and self.rejection_reason.kind != PerformanceStage.REJECTION_REASON:
            errors['rejection_reason'] = 'Choose a configured rejection reason.'
        if self.outcome == 'rejected' and not self.rejection_stage_id:
            errors['rejection_stage'] = 'Record the stage where this candidate was rejected.'
        if self.outcome == 'rejected' and not self.rejected_date:
            self.rejected_date = self.last_transition_date
        progressed = ('selected', 'offer_released', 'offer_accepted', 'joined')
        if self.outcome in progressed and not self.selected_date:
            errors['selected_date'] = 'Record the date the candidate was selected.'
        if self.outcome in ('offer_released', 'offer_accepted', 'joined') and not self.offer_released_date:
            errors['offer_released_date'] = 'Record the date the offer was released.'
        if self.outcome in ('offer_accepted', 'joined') and not self.offer_accepted_date:
            errors['offer_accepted_date'] = 'Record the date the offer was accepted.'
        if self.outcome == 'joined' and not self.joined_date:
            errors['joined_date'] = 'Record the joining date.'
        if self.outcome in ('selected', 'offer_released', 'offer_accepted', 'joined') and self.candidate_id:
            duplicate = CandidateApplication.objects.filter(
                assignment__jd_id=self.assignment.jd_id, candidate_id=self.candidate_id,
                outcome__in=['selected', 'offer_released', 'offer_accepted', 'joined']).exclude(pk=self.pk)
            if duplicate.exists():
                errors['outcome'] = 'This candidate is already recorded as selected for this JD.'
            historical_selection = CandidateStageHistory.objects.filter(
                application__assignment__jd_id=self.assignment.jd_id, application__candidate_id=self.candidate_id,
                current_outcome__in=['selected', 'offer_released', 'offer_accepted', 'joined']).exclude(
                    application_id=self.pk).exists()
            if historical_selection:
                errors['outcome'] = 'This candidate already has a recorded selection history for this JD.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = CandidateApplication.objects.filter(pk=self.pk).values(
                'stage_id', 'outcome', 'rejection_stage_id', 'rejection_reason_id').first()
        super().save(*args, **kwargs)
        current = {'stage_id': self.stage_id, 'outcome': self.outcome,
                   'rejection_stage_id': self.rejection_stage_id, 'rejection_reason_id': self.rejection_reason_id}
        if previous != current:
            CandidateStageHistory.objects.create(
                application=self,
                previous_stage_id=previous['stage_id'] if previous else None,
                current_stage=self.stage,
                previous_outcome=previous['outcome'] if previous else '', current_outcome=self.outcome,
                effective_date=self.last_transition_date, rejection_stage=self.rejection_stage,
                rejection_reason_id=self.rejection_reason_id)

    def __str__(self):
        return f'{self.candidate} / {self.assignment.jd.reference}'


class CandidateStageHistory(models.Model):
    application = models.ForeignKey(CandidateApplication, on_delete=models.PROTECT, related_name='history')
    previous_stage = models.ForeignKey(PerformanceStage, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='previous_application_stages')
    current_stage = models.ForeignKey(PerformanceStage, on_delete=models.PROTECT,
                                      related_name='current_application_stages')
    previous_outcome = models.CharField(max_length=20, blank=True)
    current_outcome = models.CharField(max_length=20, choices=CandidateApplication.OUTCOMES)
    effective_date = models.DateField(default=timezone.localdate)
    rejection_stage = models.ForeignKey(PerformanceStage, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name='rejection_history')
    rejection_reason = models.ForeignKey(PerformanceStage, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name='rejection_history_reasons')
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['effective_date', 'recorded_at', 'id']


class RecruiterActivityHistory(models.Model):
    activity = models.ForeignKey(RecruiterActivity, on_delete=models.PROTECT, related_name='history')
    previous_values = models.JSONField()
    current_values = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at', '-id']


class DailyBrief(models.Model):
    """Manual recruiter daily report, kept independent of operational recruitment records."""
    recruiter = models.ForeignKey('employee_management.Employee', on_delete=models.PROTECT,
                                  related_name='daily_briefs')
    activity_date = models.DateField(default=timezone.localdate, db_index=True)
    achieved = models.PositiveIntegerField('Selections Achieved', default=0)
    total_jds_submitted = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-activity_date', '-id']
        constraints = [models.UniqueConstraint(fields=['recruiter', 'activity_date'],
                                                name='daily_brief_recruiter_date_uniq')]
        indexes = [models.Index(fields=['activity_date', 'recruiter'], name='daily_brief_date_rec_idx')]

    def __str__(self):
        return f'{self.activity_date:%d-%m-%Y} · {self.recruiter.full_name}'

    @property
    def total_resumes_submitted(self):
        if 'jd_details' in getattr(self, '_prefetched_objects_cache', {}):
            return sum(detail.total_resumes_submitted for detail in self.jd_details.all())
        return self.jd_details.aggregate(total=models.Sum('total_resumes_submitted'))['total'] or 0

    def refresh_jd_count(self):
        if not self.pk:
            return
        count = self.jd_details.count()
        type(self).objects.filter(pk=self.pk).update(total_jds_submitted=count)
        self.total_jds_submitted = count


class DailyBriefJDDetail(models.Model):
    daily_brief = models.ForeignKey(DailyBrief, on_delete=models.CASCADE, related_name='jd_details')
    jd_number = models.CharField(max_length=80)
    total_resumes_submitted = models.PositiveIntegerField()
    row_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['row_order', 'id']
        constraints = [models.UniqueConstraint(fields=['daily_brief', 'jd_number'],
                                                name='daily_brief_jd_number_uniq')]

    def __str__(self):
        return f'{self.jd_number}: {self.total_resumes_submitted}'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.daily_brief.refresh_jd_count()

    def delete(self, *args, **kwargs):
        parent = self.daily_brief
        result = super().delete(*args, **kwargs)
        parent.refresh_jd_count()
        return result


class DailyBriefHistory(models.Model):
    daily_brief = models.ForeignKey(DailyBrief, on_delete=models.PROTECT, related_name='history')
    previous_values = models.JSONField()
    current_values = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at', '-id']

