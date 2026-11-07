from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from employee_management.models import Employee

from .forms import DailyBriefForm, DailyBriefJDDetailFormSet
from .models import DailyBrief, DailyBriefHistory
from .services import (daily_brief_analytics, daily_brief_filtered_querysets,
                       daily_brief_period_bounds)


def _filters_context(request):
    period = daily_brief_period_bounds(request.GET)
    briefs, details = daily_brief_filtered_querysets(period, request.GET)
    active_recruiters = Employee.objects.filter(
        Q(is_recruiter=True) | Q(daily_briefs__isnull=False)).distinct().order_by('employee_id')
    return {
        'period': period,
        'briefs': briefs,
        'details': details,
        'recruiters': active_recruiters,
        'selected_recruiter': request.GET.get('recruiter', ''),
        'recruiter_search': request.GET.get('recruiter_search', ''),
        'jd_search': request.GET.get('jd_search', ''),
    }


def _records_context(request, entry_form=None, detail_formset=None, duplicate_record=None):
    context = _filters_context(request)
    summaries = context['briefs'].annotate(
        total_jds=Count('jd_details'),
        total_resumes=Sum('jd_details__total_resumes_submitted')).prefetch_related('jd_details')
    sort_map = {
        'date': 'activity_date', '-date': '-activity_date',
        'recruiter': 'recruiter__first_name', '-recruiter': '-recruiter__first_name',
        'jds': 'total_jds', '-jds': '-total_jds',
        'resumes': 'total_resumes', '-resumes': '-total_resumes',
        'achieved': 'achieved', '-achieved': '-achieved',
        'created': 'created_at', '-created': '-created_at',
    }
    sort = request.GET.get('sort', '-date')
    if sort not in sort_map:
        sort = '-date'
    summaries = summaries.order_by(sort_map[sort], '-pk')
    page_size = request.GET.get('page_size', '25')
    if page_size not in ('25', '50', '100'):
        page_size = '25'
    page_obj = Paginator(summaries, int(page_size)).get_page(request.GET.get('page'))
    context.update({
        'page_obj': page_obj,
        'page_size': page_size,
        'page_sizes': ('25', '50', '100'),
        'sort': sort,
        'entry_form': entry_form or DailyBriefForm(),
        'detail_formset': detail_formset or DailyBriefJDDetailFormSet(instance=DailyBrief(), prefix='details'),
        'duplicate_record': duplicate_record,
        'filtered_brief_count': context['briefs'].count(),
        'filtered_jd_count': context['details'].count(),
        'filtered_resume_count': context['details'].aggregate(total=Sum('total_resumes_submitted'))['total'] or 0,
    })
    return context


def _snapshot(brief):
    recruiter = Employee.objects.get(pk=brief.recruiter_id)
    return {
        'recruiter_id': brief.recruiter_id,
        'recruiter_name': recruiter.full_name,
        'activity_date': brief.activity_date.isoformat(),
        'achieved': brief.achieved,
        'jd_details': list(brief.jd_details.order_by('row_order', 'pk').values(
            'jd_number', 'total_resumes_submitted', 'row_order')),
    }


def _save_brief(form, detail_formset, instance=None, before_snapshot=None):
    before = before_snapshot if before_snapshot is not None else (
        _snapshot(instance) if instance and instance.pk else None)
    try:
        with transaction.atomic():
            brief = form.save(commit=False)
            brief.save()
            detail_formset.instance = brief
            detail_formset.save()
            brief.refresh_jd_count()
            if before is not None:
                after = _snapshot(brief)
                if before != after:
                    DailyBriefHistory.objects.create(daily_brief=brief, previous_values=before,
                                                     current_values=after)
            return brief
    except IntegrityError:
        # The database uniqueness constraint is the final guard if two saves race.
        raise


def daily_brief_records(request):
    form = DailyBriefForm(request.POST or None)
    formset = DailyBriefJDDetailFormSet(request.POST or None, instance=DailyBrief(), prefix='details')
    if request.method == 'POST':
        form_valid = form.is_valid()
        detail_formset_valid = formset.is_valid()
    else:
        form_valid = detail_formset_valid = False
    if request.method == 'POST' and form_valid and detail_formset_valid:
        try:
            brief = _save_brief(form, formset)
        except IntegrityError:
            form.duplicate_record = DailyBrief.objects.filter(
                recruiter=form.cleaned_data.get('recruiter'),
                activity_date=form.cleaned_data.get('activity_date')).first()
            form.add_error(None, 'A Daily Brief already exists for this recruiter and activity date. Review it before adding another.')
        else:
            messages.success(request, 'Daily Brief saved. JD and resume totals were calculated from its detail rows.')
            return redirect('performance_review:daily_brief_records')
    return render(request, 'performance_review/daily_brief_records.html', _records_context(
        request, entry_form=form, detail_formset=formset, duplicate_record=form.duplicate_record))


def daily_brief_detail(request, pk):
    brief = get_object_or_404(DailyBrief.objects.select_related('recruiter').prefetch_related(
        'jd_details', 'history'), pk=pk)
    return render(request, 'performance_review/daily_brief_detail.html', {'brief': brief})


def daily_brief_edit(request, pk):
    brief = get_object_or_404(DailyBrief.objects.select_related('recruiter'), pk=pk)
    before_snapshot = _snapshot(brief)
    form = DailyBriefForm(request.POST or None, instance=brief)
    formset = DailyBriefJDDetailFormSet(request.POST or None, instance=brief, prefix='details')
    if request.method == 'POST':
        form_valid = form.is_valid()
        detail_formset_valid = formset.is_valid()
    else:
        form_valid = detail_formset_valid = False
    if request.method == 'POST' and form_valid and detail_formset_valid:
        try:
            _save_brief(form, formset, instance=brief, before_snapshot=before_snapshot)
        except IntegrityError:
            form.duplicate_record = DailyBrief.objects.filter(
                recruiter=form.cleaned_data.get('recruiter'),
                activity_date=form.cleaned_data.get('activity_date')).exclude(pk=brief.pk).first()
            form.add_error(None, 'A Daily Brief already exists for this recruiter and activity date. Choose another date or edit the existing record.')
        else:
            messages.success(request, 'Daily Brief updated. Its previous and current values are retained in the change history.')
            return redirect('performance_review:daily_brief_detail', pk=brief.pk)
    context = {'brief': brief, 'entry_form': form, 'detail_formset': formset,
               'duplicate_record': form.duplicate_record}
    return render(request, 'performance_review/daily_brief_edit.html', context)


def daily_brief_analytics_page(request):
    context = _filters_context(request)
    context.update(daily_brief_analytics(context['period'], context['briefs'], context['details']))
    return render(request, 'performance_review/daily_brief_analytics.html', context)
