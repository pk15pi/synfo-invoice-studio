from django.contrib import messages
from django.http import HttpResponse
from calendar import month_name

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from employee_management.models import Employee
from invoice_app.models import RecruitmentCandidate
from .exports import report_pdf, report_xlsx
from .forms import (CandidateApplicationForm, PerformanceSettingsForm, PerformanceStageForm,
                    RecruiterActivityForm, RecruiterAssignmentForm, RecruiterSelectionForm, RecruitmentJDForm)
from .models import (CandidateApplication, PerformanceSettings, PerformanceStage,
                     RecruiterActivity, RecruiterAssignment, RecruitmentJD)
from .services import build_report, period_bounds


def _report_context(request):
    settings = PerformanceSettings.current()
    period = period_bounds(request.GET, settings)
    data = build_report(period)
    period['label'] = f"{period['start']:%d %b %Y} – {period['end']:%d %b %Y}"
    period['period_choices'] = [
        ('this_month', 'This month'), ('previous_month', 'Previous month'), ('current_quarter', 'Current quarter'),
        ('previous_quarter', 'Previous quarter'), ('selected_quarter', 'Selected quarter'),
        ('current_fy', 'Current financial year'), ('previous_fy', 'Previous financial year'),
        ('selected_fy', 'Selected financial year'), ('selected_year', 'Selected calendar year'),
        ('selected_month', 'Selected month'), ('custom', 'Custom date range')]
    period['month_choices'] = [(n, month_name[n]) for n in range(1, 13)]
    period['quarter_choices'] = range(1, 5)
    return settings, period, data


def dashboard(request):
    _, period, data = _report_context(request)
    _rank_positions(data['ranking'])
    cards = data['cards']
    verified_cards = [('Recruiters', cards['recruiters']), ('Unique JDs', cards['unique_jds']),
                      ('JD assignments', cards['assignments']), ('Resumes submitted', cards['submissions']),
                      ('Unique candidates', cards['unique_candidates']), ('Resumes rejected', cards['rejected']),
                      ('Candidates selected', cards['selected']), ('Offers released', cards['offers']),
                      ('Candidates joined', cards['joined'])]
    summary_cards = [('Reported submissions', cards['summary_submissions']), ('Reported rejected', cards['summary_rejected']),
                     ('Reported selected', cards['summary_selected']), ('Reported offers', cards['summary_offers']),
                     ('Reported joined', cards['summary_joined'])]
    return render(request, 'performance_review/dashboard.html', {'period': period, 'verified_cards': verified_cards,
                   'summary_cards': summary_cards, **data})


def activity_entry(request):
    forms = {'jd': RecruitmentJDForm(prefix='jd'), 'assignment': RecruiterAssignmentForm(prefix='assignment'),
             'summary': RecruiterActivityForm(prefix='summary'), 'candidate': CandidateApplicationForm(prefix='candidate')}
    if request.method == 'POST':
        section = request.POST.get('section')
        cls = {'jd': RecruitmentJDForm, 'assignment': RecruiterAssignmentForm,
               'summary': RecruiterActivityForm, 'candidate': CandidateApplicationForm}.get(section)
        if cls:
            form = cls(request.POST, prefix=section)
            if form.is_valid():
                obj = form.save()
                messages.success(request, 'Job description saved.' if section == 'jd' else
                                 'Recruiter assignment saved.' if section == 'assignment' else
                                 'Summary-only activity saved separately.' if section == 'summary' else
                                 'Candidate outcome saved; stage history was recorded.')
                return redirect('performance_review:activity')
            forms[section] = form
    return render(request, 'performance_review/activity.html', {'forms': forms,
        'candidate_count': RecruitmentCandidate.objects.count()})


def records(request):
    _, period, data = _report_context(request)
    activities = RecruiterActivity.objects.select_related('assignment__jd', 'assignment__recruiter').filter(
        activity_date__range=(period['start'], period['end']))
    return render(request, 'performance_review/records.html', {'period': period, 'applications': data['applications'],
                   'activities': activities, 'jd_rows': data['jd_rows']})


def reports(request):
    settings, period, data = _report_context(request)
    _rank_positions(data['ranking'])
    if request.GET.get('export') in ('xlsx', 'pdf'):
        fmt = request.GET['export']
        payload = report_xlsx(data) if fmt == 'xlsx' else report_pdf(data)
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if fmt == 'xlsx' else 'application/pdf'
        response = HttpResponse(payload, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="Recruiter_Performance_{period["start"]:%Y%m%d}_{period["end"]:%Y%m%d}.{fmt}"'
        return response
    return render(request, 'performance_review/reports.html', {'period': period, 'settings': settings, **data})


def settings(request):
    config = PerformanceSettings.current()
    if request.method == 'POST':
        section = request.POST.get('section')
        if section == 'recruiters':
            selection_form = RecruiterSelectionForm(request.POST)
            if selection_form.is_valid():
                selection_form.save()
                messages.success(request, 'Recruiter eligibility updated on the selected Employee records.')
                return redirect('performance_review:settings')
            forms = {'recruiters': selection_form}
        elif section == 'weights':
            weights_form = PerformanceSettingsForm(request.POST, instance=config)
            if weights_form.is_valid():
                weights_form.save()
                messages.success(request, 'Performance score settings saved.')
                return redirect('performance_review:settings')
            forms = {'weights': weights_form}
        elif section == 'stage':
            stage_form = PerformanceStageForm(request.POST, prefix='stage')
            if stage_form.is_valid():
                stage_form.save()
                messages.success(request, 'Performance setting saved.')
                return redirect('performance_review:settings')
            forms = {'stage': stage_form}
        else:
            forms = {}
    else:
        forms = {}
    forms.setdefault('recruiters', RecruiterSelectionForm(initial={
        'recruiters': list(Employee.objects.filter(is_recruiter=True, status__in=Employee.EMPLOYED).values_list('pk', flat=True))}))
    forms.setdefault('weights', PerformanceSettingsForm(instance=config))
    forms.setdefault('stage', PerformanceStageForm(prefix='stage'))
    values = PerformanceStage.objects.all()
    return render(request, 'performance_review/settings.html', {'forms': forms, 'values': values, 'config': config})


def stage_edit(request, pk):
    obj = get_object_or_404(PerformanceStage, pk=pk)
    form = PerformanceStageForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Performance stage or reason updated.')
        return redirect('performance_review:settings')
    return render(request, 'performance_review/simple_form.html', {'form': form,
                   'title': f'Edit {obj.get_kind_display().lower()}',
                   'back_url': reverse('performance_review:settings')})


def application_edit(request, pk):
    obj = get_object_or_404(CandidateApplication.objects.select_related('assignment__jd'), pk=pk)
    form = CandidateApplicationForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Candidate outcome updated; prior stage history is retained.')
        return redirect('performance_review:records')
    return render(request, 'performance_review/application_form.html', {'form': form, 'application': obj})


def activity_edit(request, pk):
    obj = get_object_or_404(RecruiterActivity, pk=pk)
    form = RecruiterActivityForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Summary-only activity updated.')
        return redirect('performance_review:records')
    return render(request, 'performance_review/simple_form.html', {'form': form, 'title': 'Edit summary-only activity',
                   'back_url': reverse('performance_review:records')})


def jd_edit(request, pk):
    obj = get_object_or_404(RecruitmentJD, pk=pk)
    form = RecruitmentJDForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Job description updated.')
        return redirect('performance_review:records')
    return render(request, 'performance_review/simple_form.html', {'form': form, 'title': f'Edit JD {obj.reference}',
                   'back_url': reverse('performance_review:records')})


def _rank_positions(rows):
    previous = None
    rank = 0
    for index, row in enumerate(rows, 1):
        score = row['score']
        if score != previous:
            rank = index
        row['rank'] = rank
        previous = score
