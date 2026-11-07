from io import BytesIO

from django.db.models import Count
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import CandidateApplication, CandidateStageHistory


def _quarter_rows(monthly, start_month=4):
    quarters = {}
    for month in monthly:
        # The module's reporting year begins in April.
        label = month['label']
        from datetime import datetime
        parsed = datetime.strptime(label, '%b %Y').date()
        q = ((parsed.month - start_month) % 12) // 3 + 1
        fy = parsed.year if parsed.month >= start_month else parsed.year - 1
        key = (fy, q)
        row = quarters.setdefault(key, {'fy': fy, 'quarter': q, 'submitted': 0, 'rejected': 0,
                                        'selected': 0, 'joined': 0, 'summary_submitted': 0})
        for field in ('submitted', 'rejected', 'selected', 'joined', 'summary_submitted'):
            row[field] += month[field]
    return [quarters[key] for key in sorted(quarters)]


def _rejection_rows(start, end):
    return list(CandidateStageHistory.objects.filter(current_outcome='rejected', rejection_stage__isnull=False,
        effective_date__range=(start, end)).values('rejection_stage__name')
        .annotate(total=Count('application__candidate_id', distinct=True)).order_by('-total'))


def report_xlsx(data):
    wb = Workbook()
    summary = wb.active
    summary.title = 'Overall Summary'
    period = data['period']
    cards = data['cards']
    rows = [
        ['Quantbud LLP · Synfowebs Technology'], ['Recruiter Performance Review'],
        ['Period', f"{data['start']:%d %b %Y} to {data['end']:%d %b %Y}"],
        ['Generated', timezone.localtime().strftime('%d %b %Y %H:%M')], [],
        ['Candidate-verified metrics', 'Value'],
        ['Active recruiters', cards['recruiters']], ['Unique JDs worked', cards['unique_jds']],
        ['JD assignments', cards['assignments']], ['Resume submissions', cards['submissions']],
        ['Unique candidates submitted', cards['unique_candidates']], ['Rejected', cards['rejected']],
        ['Selected', cards['selected']], ['Offers released', cards['offers']], ['Joined', cards['joined']], [],
        ['Summary-only metrics (kept separate)', 'Value'],
        ['Reported submissions', cards['summary_submissions']], ['Reported rejected', cards['summary_rejected']],
        ['Reported selected', cards['summary_selected']], ['Reported offers', cards['summary_offers']],
        ['Reported joined', cards['summary_joined']],
        ['Top-performing recruiter', data['ranking'][0]['employee'].full_name if data['ranking'] else 'No eligible recruiter'],
        ['Top recruiter score', float(data['ranking'][0]['score']) if data['ranking'] else 0],
    ]
    for row in rows:
        summary.append(row)
    recruiters = wb.create_sheet('Recruiter Performance')
    ranking = {row['employee'].pk: row for row in data['ranking']}
    recruiters.append(['Employee ID', 'Recruiter', 'Department', 'Designation', 'Rank', 'Score', 'Unique JDs', 'JD Assignments',
                       'Verified submissions', 'Unique candidates', 'Rejected', 'Selected', 'Offers', 'Joined',
                       'Selection rate %', 'Summary submissions', 'Summary rejected', 'Summary selected',
                       'Summary offers', 'Summary joined'])
    for row in data['metrics']:
        employee, v, s = row['employee'], row['verified'], row['summary']
        rank = ranking.get(employee.pk, {})
        recruiters.append([employee.employee_id, employee.full_name, employee.department.name, employee.designation,
                           rank.get('rank', ''), float(rank['score']) if rank else '', v['jds'], v['assignments'], v['submissions'], v['unique_candidates'], v['rejected'],
                           v['selected'], v['offers'], v['joined'], v['selection_rate'], s['submissions'],
                           s['rejected'], s['selected'], s['offers'], s['joined']])
    c = data['cards']
    recruiters.append(['TOTAL', '', '', '', '', '', c['unique_jds'], c['assignments'], c['submissions'],
                       c['unique_candidates'], c['rejected'], c['selected'], c['offers'], c['joined'], '',
                       c['summary_submissions'], c['summary_rejected'], c['summary_selected'],
                       c['summary_offers'], c['summary_joined']])
    jds = wb.create_sheet('JD-wise Performance')
    jds.append(['JD ID', 'Client', 'Job title', 'Status', 'Recruiter', 'Assignment started',
                'Verified submissions', 'Verified rejected', 'Verified selected',
                'Summary submissions', 'Summary rejected', 'Summary selected'])
    for row in data['jd_rows']:
        jd = row['jd']
        for assignment in row['assignments']:
            jds.append([jd.reference, jd.client_display, jd.job_title, jd.get_status_display(),
                        f'{assignment.recruiter.employee_id} - {assignment.recruiter.full_name}',
                        assignment.started_on, row['submitted'], row['rejected'], row['selected'],
                        row['summary_submitted'], row['summary_rejected'], row['summary_selected']])
    candidates = wb.create_sheet('Candidate Outcomes')
    candidates.append(['Candidate ref', 'Candidate', 'JD ID', 'Client', 'Job title', 'Recruiter', 'Submission date',
                       'Stage', 'Outcome', 'Rejection stage', 'Rejection reason', 'Selected date', 'Rejected date',
                       'Offer released', 'Offer accepted', 'Joined date', 'Remarks'])
    for app in data['applications']:
        candidates.append([app.candidate.candidate_code, app.candidate.candidate_name, app.assignment.jd.reference,
                           app.assignment.jd.client_display, app.assignment.jd.job_title, app.assignment.recruiter.full_name,
                           app.submission_date, app.stage.name, app.get_outcome_display(),
                           app.rejection_stage.name if app.rejection_stage_id else '',
                           app.rejection_reason.name if app.rejection_reason_id else '', app.selected_date,
                           app.rejected_date, app.offer_released_date, app.offer_accepted_date, app.joined_date, app.remarks])
    rejection = wb.create_sheet('Rejection Analysis')
    rejection.append(['Rejection stage', 'Candidate-verified rejections'])
    for row in _rejection_rows(data['start'], data['end']):
        rejection.append([row['rejection_stage__name'] or 'Unspecified', row['total']])
    monthly = wb.create_sheet('Monthly Trend')
    monthly.append(['Month', 'Verified submissions', 'Verified rejected', 'Verified selected', 'Verified offers',
                    'Verified joined', 'Summary submissions', 'Summary rejected', 'Summary selected',
                    'Summary offers', 'Summary joined'])
    for row in data['monthly']:
        monthly.append([row['label'], row['submitted'], row['rejected'], row['selected'], row['offers'], row['joined'],
                        row['summary_submitted'], row['summary_rejected'], row['summary_selected'],
                        row['summary_offers'], row['summary_joined']])
    quarters = wb.create_sheet('Quarterly Summary')
    quarters.append(['Financial year starts', 'Quarter', 'Verified submissions', 'Rejected', 'Selected', 'Offers', 'Joined',
                     'Summary submissions', 'Summary rejected', 'Summary selected', 'Summary offers', 'Summary joined'])
    for row in data['quarterly']:
        quarters.append([row['fy'], f"Q{row['quarter']}", row['submitted'], row['rejected'], row['selected'],
                         row['offers'], row['joined'], row['summary_submitted'], row['summary_rejected'],
                         row['summary_selected'], row['summary_offers'], row['summary_joined']])

    for ws in wb.worksheets:
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='174E7A')
        for column in ws.columns:
            values = [len(str(cell.value or '')) for cell in list(column)[:200]]
            width = min(max(max(values, default=10) + 2, 12), 42)
            ws.column_dimensions[get_column_letter(column[0].column)].width = width
            for cell in column:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
    for ws in (recruiters, jds, candidates, monthly, quarters):
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if hasattr(cell.value, 'strftime'):
                    cell.number_format = 'dd-mmm-yyyy'
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def report_pdf(data):
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), rightMargin=12 * mm, leftMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CenteredTitle', parent=styles['Title'], alignment=TA_CENTER))
    story = [Paragraph('Quantbud LLP · Synfowebs Technology', styles['Heading2']),
             Paragraph('Recruiter Performance Review', styles['CenteredTitle']),
             Paragraph(f"Period: {data['start']:%d %b %Y} to {data['end']:%d %b %Y} · Generated: {timezone.localdate():%d %b %Y}", styles['Normal']),
             Spacer(1, 5 * mm)]
    cards = data['cards']
    summary = [['Candidate-verified metric', 'Total', 'Summary-only metric', 'Total'],
               ['Unique JDs', cards['unique_jds'], 'Summary submissions', cards['summary_submissions']],
               ['JD assignments', cards['assignments'], 'Summary rejected', cards['summary_rejected']],
               ['Verified submissions', cards['submissions'], 'Summary selected', cards['summary_selected']],
               ['Unique candidates', cards['unique_candidates'], 'Summary offers', cards['summary_offers']],
               ['Rejected / selected', f"{cards['rejected']} / {cards['selected']}", 'Summary joined', cards['summary_joined']],
               ['Offers / joined', f"{cards['offers']} / {cards['joined']}", '', '']]
    story.extend([Table(summary, repeatRows=1, colWidths=[54 * mm, 25 * mm, 55 * mm, 25 * mm]), Spacer(1, 5 * mm),
                  Paragraph('Recruiter-wise candidate-verified performance', styles['Heading2'])])
    rows = [['Recruiter', 'Unique JDs', 'Assignments', 'Submissions', 'Unique candidates', 'Rejected', 'Selected',
             'Offers', 'Joined', 'Selection %']]
    for row in data['metrics']:
        v = row['verified']
        rows.append([row['employee'].full_name, v['jds'], v['assignments'], v['submissions'], v['unique_candidates'],
                     v['rejected'], v['selected'], v['offers'], v['joined'], f"{v['selection_rate']:.2f}%"])
    story.append(Table(rows, repeatRows=1, colWidths=[44 * mm, 17 * mm, 19 * mm, 20 * mm, 25 * mm,
                                                       17 * mm, 17 * mm, 17 * mm, 17 * mm, 20 * mm]))
    cfg = data['settings']
    formula = (f"Score = normalized joined count ({cfg.joined_weight}%), selected count ({cfg.selected_weight}%), "
               f"resolved-outcome selection conversion ({cfg.quality_weight}%), and unique JDs worked ({cfg.jd_weight}%).")
    story.extend([Spacer(1, 5 * mm), Paragraph('Top Performing Recruiters', styles['Heading2']),
                  Paragraph(formula + ' Pending applications are excluded from the conversion denominator; only recruiters with at least one selected, rejected, or joined outcome are ranked. Ties share a rank and are listed with their underlying measures.', styles['BodyText'])])
    ranks = [['Rank', 'Recruiter', 'Score', 'Unique JDs', 'Submitted', 'Selected', 'Joined']]
    for row in data['ranking']:
        v = row['verified']
        ranks.append([row.get('rank', ''), row['employee'].full_name, f"{row['score']:.2f}", v['jds'],
                      v['submissions'], v['selected'], v['joined']])
    story.append(Table(ranks, repeatRows=1))
    story.extend([Spacer(1, 4 * mm), Paragraph('Rejection stage analysis (candidate-verified)', styles['Heading2'])])
    rej = [['Rejection stage', 'Candidates']]
    for row in _rejection_rows(data['start'], data['end']):
        rej.append([row['rejection_stage__name'] or 'Unspecified', row['total']])
    story.append(Table(rej, repeatRows=1))
    story.extend([Spacer(1, 4 * mm), Paragraph('Monthly candidate-verified trend', styles['Heading2'])])
    story.append(_monthly_chart(data['monthly']))
    trend = [['Month', 'Submissions', 'Rejected', 'Selected', 'Offers', 'Joined', 'Summary submissions',
              'Summary rejected', 'Summary selected', 'Summary offers', 'Summary joined']]
    trend.extend([[r['label'], r['submitted'], r['rejected'], r['selected'], r['offers'], r['joined'],
                   r['summary_submitted'], r['summary_rejected'], r['summary_selected'], r['summary_offers'], r['summary_joined']]
                  for r in data['monthly']])
    story.extend([Table(trend, repeatRows=1), Spacer(1, 4 * mm),
                  Paragraph('Quarterly financial-year summary', styles['Heading2'])])
    quarter_rows = [['FY start year', 'Quarter', 'Verified submissions', 'Rejected', 'Selected', 'Offers', 'Joined',
                     'Summary submissions', 'Summary rejected', 'Summary selected', 'Summary offers', 'Summary joined']]
    quarter_rows.extend([[r['fy'], f"Q{r['quarter']}", r['submitted'], r['rejected'], r['selected'], r['offers'],
                          r['joined'], r['summary_submitted'], r['summary_rejected'], r['summary_selected'],
                          r['summary_offers'], r['summary_joined']] for r in data['quarterly']])
    story.append(Table(quarter_rows, repeatRows=1))
    for table in [item for item in story if isinstance(item, Table)]:
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#174e7a')),
                                   ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                                   ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                                   ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#cbd5e1')),
                                   ('FONTSIZE', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                   ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f2f6fa')])]))
    doc.build(story)
    return output.getvalue()


def _monthly_chart(monthly):
    """Small native PDF grouped-bar chart; no external images or CDN assets are required."""
    width, height = 260 * mm, 58 * mm
    drawing = Drawing(width, height)
    colorset = [colors.HexColor('#2f6fde'), colors.HexColor('#e8743b'),
                colors.HexColor('#19a979'), colors.HexColor('#945ecf')]
    fields = [('submitted', 'Sub'), ('rejected', 'Rej'), ('selected', 'Sel'), ('joined', 'Join')]
    maximum = max((max(row[field] for field, _ in fields) for row in monthly), default=0) or 1
    left, base, chart_height = 16 * mm, 12 * mm, 34 * mm
    group_width = (width - left - 6 * mm) / max(len(monthly), 1)
    drawing.add(String(0, height - 4 * mm, 'Submissions / outcomes by month', fontSize=8, fillColor=colors.HexColor('#34495e')))
    drawing.add(String(left, 2 * mm, 'Blue: submitted   Orange: rejected   Green: selected   Purple: joined',
                      fontSize=7, fillColor=colors.HexColor('#52677d')))
    for index, row in enumerate(monthly):
        x0 = left + index * group_width
        bar_width = min(4 * mm, group_width / 6)
        for series_index, (field, _) in enumerate(fields):
            value = row[field]
            bar_height = chart_height * value / maximum
            drawing.add(Rect(x0 + series_index * (bar_width + .6 * mm), base, bar_width, max(bar_height, .4 * mm),
                             fillColor=colorset[series_index], strokeColor=None))
        label = row['label'][:7]
        drawing.add(String(x0, base - 3 * mm, label, fontSize=6, fillColor=colors.HexColor('#34495e')))
    return drawing
