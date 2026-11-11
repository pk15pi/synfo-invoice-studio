"""One-page asset handover receipt (ReportLab). Reuses the Business Profile header."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PRIMARY = colors.HexColor('#1f3a5f')
LIGHT = colors.HexColor('#eef2f7')
BORDER = colors.HexColor('#b8c4d4')


def _profile():
    try:
        from invoice_app.models import BusinessProfile
        return BusinessProfile.objects.filter(pk=1).first()
    except Exception:
        return None


def _company(profile):
    if profile is None:
        return 'Company', []
    name = (profile.brand_name or profile.name or 'Company').strip()
    lines = [line.strip() for line in (profile.address or '').splitlines() if line.strip()]
    contact = ' | '.join(
        value for value in (
            f'Phone: {profile.phone}' if profile.phone else '',
            f'Email: {profile.email}' if profile.email else '',
        ) if value
    )
    if contact:
        lines.append(contact)
    return name, lines


def receipt_number(assignment):
    return assignment.handover_number or f'AH-{assignment.pk:03d}'


def render_handover_receipt(assignment) -> bytes:
    profile = _profile()
    company, address_lines = _company(profile)
    employee = assignment.employee
    asset = assignment.asset
    acknowledgement = assignment.acknowledgements.select_related('document').order_by(
        '-acknowledged_at', '-id',
    ).first()

    buf = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(f'Asset handover receipt {receipt_number(assignment)}')
    pdf.setAuthor(company)
    left, right = 18 * mm, width - 18 * mm
    y = height - 20 * mm

    pdf.setFillColor(PRIMARY)
    pdf.setFont('Helvetica-Bold', 15)
    pdf.drawString(left, y, company[:70])
    pdf.setFillColor(colors.black)
    pdf.setFont('Helvetica', 8.5)
    y -= 5 * mm
    for line in address_lines[:3]:
        pdf.drawString(left, y, line[:110])
        y -= 4 * mm

    pdf.setStrokeColor(BORDER)
    pdf.line(left, y, right, y)
    y -= 9 * mm
    pdf.setFont('Helvetica-Bold', 13)
    pdf.drawString(left, y, 'ASSET HANDOVER RECEIPT')
    pdf.setFont('Helvetica', 9)
    pdf.drawRightString(right, y, f'Receipt no: {receipt_number(assignment)}')
    y -= 5 * mm
    pdf.drawRightString(right, y, f'Issue date: {assignment.issue_date:%d-%m-%Y}')
    y -= 8 * mm

    def row(label, value, x, y_pos, column):
        pdf.setFont('Helvetica', 8.5)
        pdf.setFillColor(colors.HexColor('#5a6b7c'))
        pdf.drawString(x, y_pos, label)
        pdf.setFillColor(colors.black)
        pdf.setFont('Helvetica-Bold', 9.5)
        pdf.drawString(x + column, y_pos, (str(value) if value not in (None, '') else '-')[:70])

    pdf.setFillColor(LIGHT)
    pdf.rect(left, y - 4 * mm, right - left, 30 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.black)
    row('Employee', employee.full_name, left + 4 * mm, y + 1 * mm, 22 * mm)
    row('Employee ID', employee.employee_id, left + 110 * mm, y + 1 * mm, 24 * mm)
    y -= 7 * mm
    row('Department', getattr(employee.department, 'name', ''), left + 4 * mm, y + 1 * mm, 22 * mm)
    row('Designation', employee.designation, left + 110 * mm, y + 1 * mm, 24 * mm)
    y -= 7 * mm
    row('Email', employee.work_email or employee.personal_email, left + 4 * mm, y + 1 * mm, 22 * mm)
    row('Condition', assignment.get_issue_condition_display(), left + 110 * mm, y + 1 * mm, 24 * mm)
    y -= 13 * mm

    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(left, y, 'Asset issued')
    y -= 4 * mm
    pdf.setFillColor(PRIMARY)
    pdf.rect(left, y - 5 * mm, right - left, 7 * mm, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('Helvetica-Bold', 8.5)
    pdf.drawString(left + 2 * mm, y - 3 * mm, 'Asset ID')
    pdf.drawString(left + 34 * mm, y - 3 * mm, 'Name')
    pdf.drawString(left + 100 * mm, y - 3 * mm, 'Category')
    pdf.drawString(left + 135 * mm, y - 3 * mm, 'Serial number')
    y -= 10 * mm
    pdf.setFillColor(colors.black)
    pdf.setFont('Helvetica', 9)
    pdf.drawString(left + 2 * mm, y, asset.asset_code[:40])
    pdf.drawString(left + 34 * mm, y, asset.name[:40])
    pdf.drawString(left + 100 * mm, y, asset.category.name[:24])
    pdf.drawString(left + 135 * mm, y, (asset.serial_number or '-')[:30])
    y -= 6 * mm
    if asset.brand or asset.model_number:
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(
            left + 2 * mm, y,
            f'Brand / model: {asset.brand or "-"} {asset.model_number or ""}'.strip()[:110],
        )
        y -= 5 * mm
    accessories = ', '.join(assignment.accessories_handed_over()) or 'None'
    pdf.drawString(left + 2 * mm, y, f'Accessories handed over: {accessories}'[:110])
    y -= 6 * mm
    if assignment.expected_return_date:
        pdf.drawString(
            left + 2 * mm, y,
            f'Expected return date: {assignment.expected_return_date:%d-%m-%Y}',
        )
        y -= 6 * mm

    pdf.setFont('Helvetica', 9)
    text = pdf.beginText(left, y)
    text.setLeading(12)
    statement = (
        f'I, {employee.full_name}, acknowledge that I have received the asset listed above from '
        f'{company}. I will use it for company work, keep it in good condition and return it when '
        'asked or when my employment ends.'
    )
    for line in _wrap(pdf, statement, right - left):
        text.textLine(line)
    pdf.drawText(text)
    y = text.getY() - 6 * mm

    pdf.setFont('Helvetica', 8.5)
    if acknowledgement:
        detail = (
            f'Acknowledgement: {acknowledgement.get_acknowledgement_type_display()} by '
            f'{acknowledgement.acknowledgement_name} on '
            f'{acknowledgement.acknowledged_at:%d-%m-%Y %H:%M}'
        )
        pdf.drawString(left, y, detail[:120])
        y -= 5 * mm
        if acknowledgement.confirmation_text:
            for line in _wrap(pdf, f'Confirmation: {acknowledgement.confirmation_text}', right - left)[:3]:
                pdf.drawString(left, y, line)
                y -= 4 * mm
        if acknowledgement.document_id:
            pdf.drawString(left, y, f'Attached document: {acknowledgement.document.title}'[:120])
            y -= 5 * mm

    y = min(y, 70 * mm)
    pdf.setStrokeColor(BORDER)
    pdf.line(left, y, left + 70 * mm, y)
    pdf.line(right - 70 * mm, y, right, y)
    pdf.setFont('Helvetica', 8.5)
    pdf.drawString(left, y - 4 * mm, 'Employee signature')
    pdf.drawRightString(right, y - 4 * mm, 'For ' + company[:50])

    pdf.setFont('Helvetica', 7.5)
    pdf.setFillColor(colors.HexColor('#5a6b7c'))
    pdf.drawString(left, 14 * mm, f'Generated from {company} on {_today():%d-%m-%Y}. '
                                  'This receipt was issued with the asset handover.')
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _wrap(pdf, text, max_width):
    words = text.split()
    lines, current = [], ''
    for word in words:
        trial = f'{current} {word}'.strip()
        if pdf.stringWidth(trial, 'Helvetica', 9) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _today():
    from django.utils import timezone
    return timezone.localdate()
