import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from reporting import charts

from employee_management.models import Employee
from .models import (CandidateApplication, CandidateStageHistory, PerformanceSettings, PerformanceStage, RecruiterActivity,
                     RecruiterAssignment, RecruitmentJD, DailyBrief, DailyBriefJDDetail)


def period_bounds(params=None, settings=None):
    params = params or {}
    if not params:
        params = {'period_mode': 'financial', 'period_type': 'full_fy'}
    if params.get('period_mode'):
        from reporting import periods as shared_periods
        chosen = shared_periods.resolve(params)
        return {'period': chosen.key, 'start': chosen.start, 'end': chosen.end,
                'year': chosen.start.year, 'month': chosen.start.month,
                'quarter': 1, 'fy_year': shared_periods.fy_start_year(chosen.start),
                'from_date': chosen.start.isoformat(), 'to_date': chosen.end.isoformat(),
                'label': chosen.label}
    settings = settings or PerformanceSettings.current()
    today = timezone.localdate()
    start_month = settings.financial_year_start_month
    mode = params.get('period') or 'this_month'
    year = _int(params.get('year'), today.year)
    month = min(12, max(1, _int(params.get('month'), today.month)))

    def shift_month(y, m, offset):
        index = y * 12 + m - 1 + offset
        return index // 12, index % 12 + 1

    if mode in ('previous_month', 'selected_month'):
        if mode == 'previous_month':
            year, month = shift_month(today.year, today.month, -1)
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    elif mode in ('current_quarter', 'previous_quarter', 'selected_quarter'):
        if mode == 'current_quarter':
            current_fy_year = today.year if today.month >= start_month else today.year - 1
            offset = (today.month - start_month) % 12
            quarter = offset // 3
            fy_year = current_fy_year
            if quarter is None:
                quarter = 0
        elif mode == 'previous_quarter':
            fy_year = today.year if today.month >= start_month else today.year - 1
            quarter = ((today.month - start_month) % 12) // 3 - 1
            if quarter < 0:
                quarter, fy_year = 3, fy_year - 1
        else:
            fy_year = _int(params.get('fy_year'), year)
            quarter = min(4, max(1, _int(params.get('quarter'), 1))) - 1
        first_month = ((start_month - 1 + quarter * 3) % 12) + 1
        start_year = fy_year + ((start_month - 1 + quarter * 3) // 12)
        end_year, end_month = shift_month(start_year, first_month, 2)
        start, end = date(start_year, first_month, 1), date(end_year, end_month,
                                                            calendar.monthrange(end_year, end_month)[1])
    elif mode in ('current_fy', 'previous_fy', 'selected_fy'):
        fy_year = today.year if today.month >= start_month else today.year - 1
        if mode == 'previous_fy':
            fy_year -= 1
        elif mode == 'selected_fy':
            fy_year = _int(params.get('fy_year'), fy_year)
        start = date(fy_year, start_month, 1)
        end_year, end_month = shift_month(fy_year, start_month, 11)
        end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    elif mode == 'selected_year':
        start, end = date(year, 1, 1), date(year, 12, 31)
    elif mode == 'custom':
        try:
            start = date.fromisoformat(params.get('from_date', ''))
            end = date.fromisoformat(params.get('to_date', ''))
            if end < start:
                raise ValueError
        except ValueError:
            start, end = date(today.year, today.month, 1), today
            mode = 'this_month'
    else:
        mode = 'this_month'
        start = date(today.year, today.month, 1)
        end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    return {'period': mode, 'start': start, 'end': end, 'year': year, 'month': month,
            'quarter': _int(params.get('quarter'), 1), 'fy_year': _int(params.get('fy_year'), year),
            'from_date': params.get('from_date', ''), 'to_date': params.get('to_date', '')}


def _int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _date_qs(queryset, field, start, end):
    return queryset.filter(**{f'{field}__range': (start, end)})


def _counts(queryset, field):
    return {row['assignment__recruiter_id']: row['total'] for row in
            queryset.values('assignment__recruiter_id').annotate(total=Count(field, distinct=True))}


def build_report(period):
    """Candidate-verified outcomes and summary-only figures stay in separate datasets."""
    start, end = period['start'], period['end']
    settings = PerformanceSettings.current()
    employees = list(Employee.objects.filter(is_recruiter=True).select_related('department').order_by('employee_id'))
    employee_ids = [e.pk for e in employees]
    # Include recruiters with historical assignments even if the flag was later removed.
    historical_ids = set(RecruiterAssignment.objects.filter(recruiter_id__in=employee_ids).values_list('recruiter_id', flat=True))
    employees = list(Employee.objects.filter(pk__in=set(employee_ids) | historical_ids).select_related('department').order_by('employee_id'))

    assignments = RecruiterAssignment.objects.select_related('jd', 'jd__client', 'recruiter').filter(
        Q(started_on__range=(start, end)) | Q(applications__submission_date__range=(start, end)) |
        Q(applications__rejected_date__range=(start, end)) | Q(applications__selected_date__range=(start, end)) |
        Q(applications__offer_released_date__range=(start, end)) |
        Q(applications__offer_accepted_date__range=(start, end)) | Q(applications__joined_date__range=(start, end)) |
        Q(applications__last_transition_date__range=(start, end)) |
        Q(summary_activities__activity_date__range=(start, end))).distinct()
    assignments_by_recruiter = {r['recruiter_id']: r['total'] for r in assignments.values('recruiter_id').annotate(total=Count('pk', distinct=True))}
    unique_jds_by_recruiter = {r['recruiter_id']: r['total'] for r in assignments.values('recruiter_id').annotate(total=Count('jd_id', distinct=True))}
    submitted = _date_qs(CandidateApplication.objects.select_related('candidate', 'assignment__jd', 'assignment__recruiter'),
                         'submission_date', start, end)
    submissions_by_recruiter = _counts(submitted, 'pk')
    unique_candidates_by_recruiter = _counts(submitted, 'candidate_id')
    outcome_specs = {
        'rejected': ('rejected_date', None), 'selected': ('selected_date', None),
        'offers': ('offer_released_date', None), 'joined': ('joined_date', None)}
    outcomes = {}
    for key, (date_field, outcome) in outcome_specs.items():
        qs = CandidateApplication.objects.all()
        outcomes[key] = _counts(_date_qs(qs, date_field, start, end), 'pk')

    summaries = _date_qs(RecruiterActivity.objects.select_related('assignment__jd', 'assignment__recruiter'),
                         'activity_date', start, end)
    summary_by_recruiter = {}
    for row in summaries.values('assignment__recruiter_id').annotate(
            submissions=Sum('resumes_submitted'), rejected=Sum('resumes_rejected'), selected=Sum('candidates_selected'),
            offers=Sum('offers_released'), joined=Sum('candidates_joined')):
        summary_by_recruiter[row['assignment__recruiter_id']] = row

    metrics = []
    for employee in employees:
        summary = summary_by_recruiter.get(employee.pk, {})
        verified = {
            'jds': unique_jds_by_recruiter.get(employee.pk, 0),
            'assignments': assignments_by_recruiter.get(employee.pk, 0),
            'submissions': submissions_by_recruiter.get(employee.pk, 0),
            'unique_candidates': unique_candidates_by_recruiter.get(employee.pk, 0),
            'rejected': outcomes['rejected'].get(employee.pk, 0),
            'selected': outcomes['selected'].get(employee.pk, 0),
            'offers': outcomes['offers'].get(employee.pk, 0),
            'joined': outcomes['joined'].get(employee.pk, 0),
        }
        sm = {k: int(summary.get(k, 0) or 0) for k in ('submissions', 'rejected', 'selected', 'offers', 'joined')}
        verified['selection_rate'] = round(verified['selected'] / verified['unique_candidates'] * 100, 2) if verified['unique_candidates'] else 0
        verified['rejection_rate'] = round(verified['rejected'] / verified['unique_candidates'] * 100, 2) if verified['unique_candidates'] else 0
        verified['joining_rate'] = round(verified['joined'] / verified['unique_candidates'] * 100, 2) if verified['unique_candidates'] else 0
        metrics.append({'employee': employee, 'verified': verified, 'summary': sm})

    assignment_ids = set(assignments.values_list('pk', flat=True))
    jds = list(RecruitmentJD.objects.prefetch_related('assignments__recruiter').filter(
        assignments__in=assignments).distinct().order_by('reference'))
    jd_rows = []
    for jd in jds:
        jd_assignments = [a for a in jd.assignments.all() if a.pk in assignment_ids]
        jd_rows.append({'jd': jd, 'assignments': jd_assignments,
                        'submitted': submitted.filter(assignment__jd=jd).count(),
                        'summary_submitted': summaries.filter(assignment__jd=jd).aggregate(x=Sum('resumes_submitted'))['x'] or 0,
                        'summary_rejected': summaries.filter(assignment__jd=jd).aggregate(x=Sum('resumes_rejected'))['x'] or 0,
                        'summary_selected': summaries.filter(assignment__jd=jd).aggregate(x=Sum('candidates_selected'))['x'] or 0,
                        'rejected': _date_qs(CandidateApplication.objects.filter(assignment__jd=jd), 'rejected_date', start, end).count(),
                        'selected': _date_qs(CandidateApplication.objects.filter(assignment__jd=jd), 'selected_date', start, end).count()})
    event_applications = CandidateApplication.objects.filter(
        Q(submission_date__range=(start, end)) | Q(rejected_date__range=(start, end)) |
        Q(selected_date__range=(start, end)) | Q(offer_released_date__range=(start, end)) |
        Q(offer_accepted_date__range=(start, end)) | Q(joined_date__range=(start, end)) |
        Q(last_transition_date__range=(start, end)))
    applications = list(event_applications.select_related('candidate', 'stage', 'rejection_stage', 'rejection_reason',
        'assignment__jd', 'assignment__recruiter').distinct().order_by('-submission_date'))
    total = lambda key: sum(row['verified'][key] for row in metrics)
    summary_total = lambda key: sum(row['summary'][key] for row in metrics)
    cards = {
        'recruiters': sum(1 for e in employees if e.status in Employee.EMPLOYED),
        'unique_jds': len(set(assignments.values_list('jd_id', flat=True))),
        'assignments': assignments.count(), 'submissions': total('submissions'), 'unique_candidates': len(set(submitted.values_list('candidate_id', flat=True))),
        'rejected': total('rejected'), 'selected': total('selected'), 'offers': total('offers'), 'joined': total('joined'),
        'summary_submissions': summary_total('submissions'), 'summary_rejected': summary_total('rejected'),
        'summary_selected': summary_total('selected'), 'summary_offers': summary_total('offers'), 'summary_joined': summary_total('joined'),
    }
    ranking = _rank(metrics, settings)
    months = list(_months(start, end))
    monthly = []
    for m_start, m_end in months:
        month_apps = CandidateApplication.objects.filter(submission_date__range=(m_start, m_end))
        month_activity = RecruiterActivity.objects.filter(activity_date__range=(m_start, m_end))
        monthly.append({'label': m_start.strftime('%b %Y'),
                        'submitted': month_apps.count(),
                        'rejected': _date_qs(CandidateApplication.objects.all(), 'rejected_date', m_start, m_end).count(),
                        'selected': _date_qs(CandidateApplication.objects.all(), 'selected_date', m_start, m_end).count(),
                        'offers': _date_qs(CandidateApplication.objects.all(), 'offer_released_date', m_start, m_end).count(),
                        'joined': _date_qs(CandidateApplication.objects.all(), 'joined_date', m_start, m_end).count(),
                        'summary_submitted': month_activity.aggregate(x=Sum('resumes_submitted'))['x'] or 0,
                        'summary_rejected': month_activity.aggregate(x=Sum('resumes_rejected'))['x'] or 0,
                        'summary_selected': month_activity.aggregate(x=Sum('candidates_selected'))['x'] or 0,
                        'summary_offers': month_activity.aggregate(x=Sum('offers_released'))['x'] or 0,
                        'summary_joined': month_activity.aggregate(x=Sum('candidates_joined'))['x'] or 0})
    recruiter_labels = [m['employee'].full_name for m in metrics]
    charts_data = [
        ('Candidate-verified monthly activity', charts.bars([m['label'] for m in monthly],
         [[m['submitted'] for m in monthly], [m['rejected'] for m in monthly],
          [m['selected'] for m in monthly], [m['joined'] for m in monthly]], 'Monthly candidate-verified activity',
         legend=['Submitted', 'Rejected', 'Selected', 'Joined']), 'verified_monthly'),
        ('Candidate-verified recruiter comparison', charts.bars(recruiter_labels,
         [[m['verified']['submissions'] for m in metrics], [m['verified']['rejected'] for m in metrics],
          [m['verified']['selected'] for m in metrics], [m['verified']['joined'] for m in metrics]],
         'Candidate-verified recruiter comparison', legend=['Submitted', 'Rejected', 'Selected', 'Joined']), 'comparison'),
        ('Current recruitment funnel', charts.hbars(_funnel(submitted), 'Recruitment funnel', money=False), 'funnel'),
        ('Rejection stages', charts.hbars(_rejection_analysis(period), 'Rejection stage analysis', money=False), 'rejections'),
        ('Summary-only submissions by month', charts.bars([m['label'] for m in monthly],
         [[m['summary_submitted'] for m in monthly]], 'Summary-only reported submissions'), 'summary_monthly'),
        ('Recruiter performance trend', charts.line([m['label'] for m in monthly],
         [[_recruiter_month_count(e.pk, m_start, m_end) for m_start, m_end in months] for e in employees[:6]],
         'Recruiter candidate-verified monthly submissions', legend=recruiter_labels[:6]), 'trend'),
    ]
    quarterly = _quarterly(monthly, settings.financial_year_start_month)
    return {'period': period, 'metrics': metrics, 'cards': cards, 'ranking': ranking, 'settings': settings,
            'monthly': monthly, 'quarterly': quarterly, 'jd_rows': jd_rows, 'applications': applications,
            'charts': charts_data, 'start': start, 'end': end}


def _recruiter_month_count(employee_id, start, end):
    return CandidateApplication.objects.filter(assignment__recruiter_id=employee_id,
                                               submission_date__range=(start, end)).count()


def _funnel(applications):
    rows = [('Resume Submitted', applications.values('candidate_id').distinct().count())]
    for label in ('Resume Shortlisted', 'Client Submitted', 'Client Interview', 'Technical Interview',
                  'Managerial Interview', 'HR Interview'):
        count = CandidateStageHistory.objects.filter(application_id__in=applications.values('pk'),
            current_stage__name__iexact=label).values('application__candidate_id').distinct().count()
        rows.append((label, count))
    rows.extend([('Selected', applications.filter(selected_date__isnull=False).values('candidate_id').distinct().count()),
                 ('Offers released', applications.filter(offer_released_date__isnull=False).values('candidate_id').distinct().count()),
                 ('Joined', applications.filter(joined_date__isnull=False).values('candidate_id').distinct().count())])
    return rows


def _rejection_analysis(period):
    return list(CandidateStageHistory.objects.filter(current_outcome='rejected', rejection_stage__isnull=False,
        effective_date__range=(period['start'], period['end'])).values('rejection_stage__name')
        .annotate(total=Count('application__candidate_id', distinct=True)).order_by('-total')
        .values_list('rejection_stage__name', 'total'))


def _rank(metrics, settings):
    eligible = [m for m in metrics if m['verified']['selected'] + m['verified']['rejected'] + m['verified']['joined'] > 0]
    max_joined = max((m['verified']['joined'] for m in eligible), default=0)
    max_selected = max((m['verified']['selected'] for m in eligible), default=0)
    max_jds = max((m['verified']['jds'] for m in eligible), default=0)
    for row in eligible:
        m = row['verified']
        # Pending candidates are excluded from the quality denominator; volume alone cannot win the score.
        quality = m['selected'] / (m['selected'] + m['rejected']) if m['selected'] + m['rejected'] else 0
        score = (Decimal(settings.joined_weight) * (Decimal(m['joined']) / Decimal(max_joined) if max_joined else Decimal(0)) +
                 Decimal(settings.selected_weight) * (Decimal(m['selected']) / Decimal(max_selected) if max_selected else Decimal(0)) +
                 Decimal(settings.quality_weight) * Decimal(str(quality)) +
                 Decimal(settings.jd_weight) * (Decimal(m['jds']) / Decimal(max_jds) if max_jds else Decimal(0)))
        row['score'] = score.quantize(Decimal('0.01'))
        row['quality'] = round(quality * 100, 2)
    eligible.sort(key=lambda row: (-row['score'], -row['verified']['joined'], -row['verified']['selected'], row['employee'].employee_id))
    return eligible


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        ms = date(y, m, 1)
        me = date(y, m, calendar.monthrange(y, m)[1])
        yield max(ms, start), min(me, end)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


def _quarterly(monthly, start_month):
    rows = {}
    for month in monthly:
        parsed = datetime.strptime(month['label'], '%b %Y').date()
        q = ((parsed.month - start_month) % 12) // 3 + 1
        fy = parsed.year if parsed.month >= start_month else parsed.year - 1
        row = rows.setdefault((fy, q), {'fy': fy, 'quarter': q, 'submitted': 0, 'rejected': 0,
                                        'selected': 0, 'offers': 0, 'joined': 0, 'summary_submitted': 0,
                                        'summary_rejected': 0, 'summary_selected': 0, 'summary_offers': 0,
                                        'summary_joined': 0})
        for key in ('submitted', 'rejected', 'selected', 'offers', 'joined', 'summary_submitted',
                    'summary_rejected', 'summary_selected', 'summary_offers', 'summary_joined'):
            row[key] += month[key]
    return [rows[key] for key in sorted(rows)]


def daily_brief_period_bounds(params=None, settings=None):
    """Resolve Daily Brief date filters; calendar and financial years are labelled distinctly."""
    params = params or {}
    if not params:
        params = {'period_mode': 'financial', 'period_type': 'full_fy'}
    if params.get('period_mode'):
        from reporting import periods as shared_periods
        chosen = shared_periods.resolve(params)
        start_fy = shared_periods.fy_start_year(chosen.start)
        # Optional Month on a full financial year (Daily Brief): blank = all months, else that month.
        opt = (params.get('optional_month') or '').strip()
        ptype = (params.get('period_type') or 'full_fy').strip().lower()
        if ((params.get('period_mode') or '').strip() == 'financial' and ptype == 'full_fy' and opt.isdigit()):
            try:
                m = int(opt)
                if 1 <= m <= 12:
                    fy = int(params.get('fy') or start_fy)
                    y = fy if m >= 4 else fy + 1
                    keep = dict(chosen.params)
                    keep['optional_month'] = opt
                    chosen = shared_periods.Period(
                        date(y, m, 1), shared_periods.month_end(y, m),
                        date(y, m, 1).strftime('%B %Y'), 'month', 'month', keep)
                    start_fy = shared_periods.fy_start_year(chosen.start)
            except (TypeError, ValueError):
                pass
        return {
            'period': chosen.key, 'start': chosen.start, 'end': chosen.end, 'label': chosen.label,
            'kind': chosen.kind,
            'year': chosen.start.year, 'month': chosen.start.month, 'fy_year': start_fy,
            'quarter': 1, 'half': 1, 'reference_date': chosen.start.isoformat(),
            'fy_start_label': calendar.month_name[4],
            'from_date': chosen.start.isoformat(), 'to_date': chosen.end.isoformat(),
            'period_choices': [], 'month_choices': [(n, calendar.month_name[n]) for n in range(1, 13)],
            'quarter_choices': range(1, 5), 'half_choices': (1, 2),
            'optional_month': (params.get('optional_month') or '').strip(),
        }
    settings = settings or PerformanceSettings.current()
    today = timezone.localdate()
    mode = params.get('period') or 'monthly'
    year = _int(params.get('year'), today.year)
    year = min(2100, max(2000, year))
    month = min(12, max(1, _int(params.get('month'), today.month)))
    fy_year = min(2100, max(2000, _int(params.get('fy_year'), today.year if today.month >= settings.financial_year_start_month else today.year - 1)))
    quarter = min(4, max(1, _int(params.get('quarter'), 1)))
    half = min(2, max(1, _int(params.get('half'), 1)))
    reference_date = parse_date(params.get('reference_date') or '') or today
    start_month = settings.financial_year_start_month

    def shift_month(start_year, start_m, offset):
        index = start_year * 12 + start_m - 1 + offset
        return index // 12, index % 12 + 1

    if mode == 'selected_year':
        start, end = date(year, 1, 1), date(year, 12, 31)
        label = f'Calendar year {year}'
    elif mode == 'weekly':
        start = reference_date - timedelta(days=reference_date.weekday())
        end = start + timedelta(days=6)
        label = f'Week of {start:%d %b %Y} – {end:%d %b %Y}'
    elif mode == 'fifteen_days':
        end = reference_date
        start = end - timedelta(days=14)
        label = f'15 days through {end:%d %b %Y}'
    elif mode == 'monthly':
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        label = start.strftime('%B %Y')
    elif mode in ('quarterly', 'half_yearly', 'yearly'):
        if mode == 'quarterly':
            first_offset, month_count = (quarter - 1) * 3, 3
        elif mode == 'half_yearly':
            first_offset, month_count = (half - 1) * 6, 6
        else:
            first_offset, month_count = 0, 12
        start_year, first_month = shift_month(fy_year, start_month, first_offset)
        end_year, last_month = shift_month(start_year, first_month, month_count - 1)
        start, end = date(start_year, first_month, 1), date(
            end_year, last_month, calendar.monthrange(end_year, last_month)[1])
        fy_label = f'FY {fy_year}–{(fy_year + 1) % 100:02d}'
        label = f'Q{quarter} · {fy_label}' if mode == 'quarterly' else (
            f'H{half} · {fy_label}' if mode == 'half_yearly' else fy_label)
    elif mode == 'custom':
        start = parse_date(params.get('from_date') or '')
        end = parse_date(params.get('to_date') or '')
        if not start or not end or end < start:
            mode = 'monthly'
            start = date(today.year, today.month, 1)
            end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
            label = start.strftime('%B %Y')
        else:
            label = f'{start:%d %b %Y} – {end:%d %b %Y}'
    else:
        mode = 'monthly'
        start = date(today.year, today.month, 1)
        end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
        label = start.strftime('%B %Y')

    kind = {'selected_year': 'year', 'weekly': 'week', 'monthly': 'month', 'quarterly': 'quarter',
            'half_yearly': 'half', 'yearly': 'fy', 'custom': 'days'}.get(mode, 'days')
    return {
        'period': mode, 'start': start, 'end': end, 'label': label, 'kind': kind,
        'year': year, 'month': month, 'fy_year': fy_year, 'quarter': quarter, 'half': half,
        'reference_date': reference_date.isoformat(),
        'fy_start_label': calendar.month_name[start_month],
        'from_date': params.get('from_date', ''), 'to_date': params.get('to_date', ''),
        'optional_month': '',
        'period_choices': [
            ('selected_year', 'Calendar year'), ('weekly', 'Weekly'), ('fifteen_days', '15 days'),
            ('monthly', 'Monthly'), ('quarterly', 'Quarterly (financial year)'),
            ('half_yearly', 'Half-yearly (financial year)'), ('yearly', 'Yearly (financial year)'),
            ('custom', 'Custom date range')],
        'month_choices': [(n, calendar.month_name[n]) for n in range(1, 13)],
        'quarter_choices': range(1, 5), 'half_choices': (1, 2),
    }


def daily_brief_filtered_querysets(period, params):
    """Return parent and detail querysets with matching activity date and search filters."""
    details = DailyBriefJDDetail.objects.filter(
        daily_brief__activity_date__range=(period['start'], period['end']))
    recruiter_id = params.get('recruiter')
    if recruiter_id and str(recruiter_id).isdigit():
        details = details.filter(daily_brief__recruiter_id=int(recruiter_id))
    recruiter_search = (params.get('recruiter_search') or '').strip()
    if recruiter_search:
        for term in recruiter_search.split():
            details = details.filter(
                Q(daily_brief__recruiter__employee_id__icontains=term) |
                Q(daily_brief__recruiter__first_name__icontains=term) |
                Q(daily_brief__recruiter__middle_name__icontains=term) |
                Q(daily_brief__recruiter__last_name__icontains=term) |
                Q(daily_brief__recruiter__alias__icontains=term))
    jd_search = (params.get('jd_search') or '').strip()
    if jd_search:
        details = details.filter(jd_number__icontains=jd_search)
    briefs = DailyBrief.objects.filter(pk__in=details.values('daily_brief_id')).select_related(
        'recruiter', 'recruiter__department').distinct()
    return briefs, details.select_related('daily_brief__recruiter')


def _daily_brief_bucket_mode(period):
    """day = custom/short ranges; month = full year/FY/multi-month; week_of_month = one calendar month."""
    start, end = period['start'], period['end']
    kind = period.get('kind') or ''
    key = period.get('period') or ''
    if key in ('custom',) or kind == 'days':
        return 'day'
    if (start.year, start.month) == (end.year, end.month) and start.day == 1 and end == date(
            end.year, end.month, calendar.monthrange(end.year, end.month)[1]):
        return 'week_of_month'
    if start.day == 1 and end == date(end.year, end.month, calendar.monthrange(end.year, end.month)[1]):
        return 'month'
    return 'day'


def _daily_brief_trend_buckets(period, trend_map, achieved_map):
    """Build chart labels + series for day / calendar-month / week-of-month buckets (zeros included)."""
    mode = _daily_brief_bucket_mode(period)
    labels, jds, resumes, achieved = [], [], [], []

    def add_day(d):
        row = trend_map.get(d, {})
        return row.get('jds', 0) or 0, row.get('resumes', 0) or 0, achieved_map.get(d, 0) or 0

    if mode == 'month':
        y, m = period['start'].year, period['start'].month
        while (y, m) <= (period['end'].year, period['end'].month):
            labels.append(date(y, m, 1).strftime('%b %Y'))
            last = calendar.monthrange(y, m)[1]
            jj = rr = aa = 0
            for day in range(1, last + 1):
                d = date(y, m, day)
                if period['start'] <= d <= period['end']:
                    j, r, a = add_day(d)
                    jj += j; rr += r; aa += a
            jds.append(jj); resumes.append(rr); achieved.append(aa)
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
    elif mode == 'week_of_month':
        y, m = period['start'].year, period['start'].month
        last = calendar.monthrange(y, m)[1]
        n_weeks = (last - 1) // 7 + 1
        for w in range(1, n_weeks + 1):
            d0, d1 = (w - 1) * 7 + 1, min(w * 7, last)
            labels.append(f'Week {w}')
            jj = rr = aa = 0
            for day in range(d0, d1 + 1):
                j, r, a = add_day(date(y, m, day))
                jj += j; rr += r; aa += a
            jds.append(jj); resumes.append(rr); achieved.append(aa)
    else:
        current = period['start']
        while current <= period['end']:
            labels.append(current.strftime('%d %b'))
            j, r, a = add_day(current)
            jds.append(j); resumes.append(r); achieved.append(a)
            current += timedelta(days=1)
    return labels, jds, resumes, achieved, mode



def daily_brief_analytics(period, briefs, details):
    """Aggregate exclusively from DailyBrief and DailyBriefJDDetail records."""
    from employee_management.models import Employee

    total_jds = details.count()
    total_resumes = details.aggregate(total=Sum('total_resumes_submitted'))['total'] or 0
    total_achieved = briefs.aggregate(total=Sum('achieved'))['total'] or 0
    recruiter_count = details.values('daily_brief__recruiter_id').distinct().count()
    brief_count = briefs.count()
    average_resumes_per_jd = round(total_resumes / total_jds, 2) if total_jds else 0
    average_jds_per_recruiter = round(total_jds / recruiter_count, 2) if recruiter_count else 0
    kpis = [
        ('Total JDs Submitted', total_jds), ('Total Resumes Submitted', total_resumes),
        ('Recruiters Active', recruiter_count), ('Daily Briefs Submitted', brief_count),
        ('Average Resumes per JD', average_resumes_per_jd),
        ('Average JDs per Recruiter', average_jds_per_recruiter),
        ('Selections Achieved', total_achieved),
    ]

    trend_rows = details.values('daily_brief__activity_date').annotate(
        jds=Count('pk'), resumes=Sum('total_resumes_submitted'))
    trend_map = {row['daily_brief__activity_date']: row for row in trend_rows}
    achieved_rows = briefs.values('activity_date').annotate(achieved=Sum('achieved'))
    achieved_map = {row['activity_date']: row['achieved'] or 0 for row in achieved_rows}
    labels, daily_jds, daily_resumes, daily_achieved, bucket_mode = _daily_brief_trend_buckets(
        period, trend_map, achieved_map)

    recruiter_rows = details.values('daily_brief__recruiter_id').annotate(
        jds=Count('pk'), resumes=Sum('total_resumes_submitted')).order_by('daily_brief__recruiter_id')
    achieved_by_recruiter = dict(briefs.values('recruiter_id').annotate(
        achieved=Sum('achieved')).values_list('recruiter_id', 'achieved'))
    ids = [row['daily_brief__recruiter_id'] for row in recruiter_rows]
    employees = {employee.pk: employee for employee in Employee.objects.filter(pk__in=ids)}
    comparison = []
    for row in recruiter_rows:
        employee = employees[row['daily_brief__recruiter_id']]
        comparison.append({'employee': employee, 'label': employee.full_name,
                           'jds': row['jds'], 'resumes': row['resumes'],
                           'achieved': achieved_by_recruiter.get(row['daily_brief__recruiter_id'], 0),
                           'average': round(row['resumes'] / row['jds'], 2) if row['jds'] else 0})
    recruiter_labels = [row['label'] for row in comparison]
    jds_by_recruiter = [row['jds'] for row in comparison]
    resumes_by_recruiter = [row['resumes'] for row in comparison]
    shares = [round(value / total_jds * 100, 2) if total_jds else 0 for value in jds_by_recruiter]

    if bucket_mode == 'month':
        jd_title, resume_title, ach_prefix = (
            'Monthly JD submission trend', 'Monthly resume submission trend', 'Monthly selections achieved')
        jd_series, resume_series = 'JDs submitted', 'Resumes submitted'
    elif bucket_mode == 'week_of_month':
        jd_title, resume_title, ach_prefix = (
            'Weekly JD submission trend', 'Weekly resume submission trend', 'Weekly selections achieved')
        jd_series, resume_series = 'JDs submitted', 'Resumes submitted'
    else:
        jd_title, resume_title, ach_prefix = (
            'Daily JD submission trend', 'Daily resume submission trend', 'Daily selections achieved')
        jd_series, resume_series = 'Daily JDs submitted', 'Daily resumes submitted'
    charts_data = [
        (jd_title, charts.line(labels, [daily_jds], jd_series, money=False), 'daily_jds'),
        (resume_title, charts.line(labels, [daily_resumes], resume_series, money=False), 'daily_resumes'),
        (f'{ach_prefix} (total {total_achieved})',
         charts.line(
             labels, [daily_achieved],
             f'{ach_prefix} (total {total_achieved})',
             money=False,
         ),
         'daily_achieved'),
        ('Recruiter-wise JD submission', charts.hbars(list(zip(recruiter_labels, jds_by_recruiter)),
                                                       'JDs by recruiter', money=False), 'recruiter_jds'),
        ('Recruiter-wise resume submission', charts.hbars(list(zip(recruiter_labels, resumes_by_recruiter)),
                                                           'Resumes by recruiter', money=False), 'recruiter_resumes'),
        ('Recruiter productivity comparison', charts.bars(recruiter_labels,
            [jds_by_recruiter, resumes_by_recruiter], 'Recruiter JD and resume totals',
            legend=['JDs', 'Resumes'], money=False), 'productivity'),
        ('JD submission distribution', charts.bars(recruiter_labels, [shares],
            'Share of reported JD rows by recruiter (%)', legend=['Share of JDs (%)'], money=False), 'jd_distribution'),
        ('Average resumes per JD', charts.bars(recruiter_labels, [[row['average'] for row in comparison]],
            'Average resumes per JD by recruiter', legend=['Average resumes / JD'], money=False), 'average_resumes'),
    ]
    return {'kpis': kpis, 'comparison': comparison, 'charts': charts_data,
            'total_jds': total_jds, 'total_resumes': total_resumes, 'total_achieved': total_achieved,
            'average_resumes_per_jd': average_resumes_per_jd,
            'average_jds_per_recruiter': average_jds_per_recruiter,
            'bucket_mode': bucket_mode, 'bucket_labels': labels}
