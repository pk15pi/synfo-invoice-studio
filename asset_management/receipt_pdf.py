"""One-page asset handover receipt (ReportLab). Reuses the Business Profile header."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PRIMARY = colors.HexColor('#1f3a5f')
LIGHT = colors.HexColor('#eef2f7')
STRIPE = colors.HexColor('#f7f9fc')
BORDER = colors.HexColor('#b8c4d4')
MUTED = colors.HexColor('#5a6b7c')

MARGIN = 18 * mm
FOOTER_Y = 14 * mm
CONTENT_BOTTOM = 50 * mm
COLUMNS = (
    ('Asset ID', 30),
    ('Name', 60),
    ('Category', 28),
    ('Serial number', 44),
    ('Qty', 12),
)


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


def issued_items(assignment):
    """The assigned asset plus every registered accessory asset in this handover."""
    items = [(assignment.asset, 1)]
    accessories = assignment.accessory_assignments.select_related(
        'asset__category',
    ).order_by('asset__asset_code')
    items.extend((link.asset, 1) for link in accessories)
    return items


def manual_accessories(assignment):
    return [str(item) for item in assignment.accessories_issued or []]


def _fit(pdf, text, max_width, font='Helvetica', size=9):
    text = '' if text is None else str(text)
    if pdf.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = '\u2026'
    while text and pdf.stringWidth(text + ellipsis, font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + ellipsis


def _wrap(pdf, text, max_width, font='Helvetica', size=9, max_lines=None):
    lines = []
    for paragraph in (str(text or '').splitlines() or ['']):
        words = paragraph.split()
        if not words:
            lines.append('')
            continue
        current = ''
        for word in words:
            candidate = f'{current} {word}'.strip()
            if pdf.stringWidth(candidate, font, size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ''
            if pdf.stringWidth(word, font, size) <= max_width:
                current = word
                continue
            chunk = ''
            for char in word:
                if pdf.stringWidth(chunk + char, font, size) <= max_width:
                    chunk += char
                else:
                    lines.append(chunk)
                    chunk = char
            current = chunk
        if current:
            lines.append(current)
    lines = lines or ['']
    if max_lines and len(lines) > max_lines:
        last = lines[max_lines - 1]
        while last and pdf.stringWidth(last + '\u2026', font, size) > max_width:
            last = last[:-1]
        lines = lines[:max_lines - 1] + [last + '\u2026']
    return lines


class _Receipt:
    def __init__(self, pdf, company, number):
        self.pdf = pdf
        self.company = company
        self.number = number
        self.left = MARGIN
        self.right = A4[0] - MARGIN
        self.width = self.right - self.left
        self.y = A4[1] - 20 * mm

    def _new_page(self):
        self.pdf.showPage()
        self.y = A4[1] - 18 * mm
        pdf = self.pdf
        pdf.setFillColor(PRIMARY)
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawString(self.left, self.y, _fit(pdf, self.company, self.width - 50 * mm, 'Helvetica-Bold', 11))
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 8)
        pdf.drawRightString(self.right, self.y, f'{self.number} (continued)')
        self.y -= 3 * mm
        pdf.setStrokeColor(BORDER)
        pdf.line(self.left, self.y, self.right, self.y)
        self.y -= 7 * mm

    def ensure(self, needed):
        if self.y - needed < CONTENT_BOTTOM:
            self._new_page()
            return False
        return True

    def gap(self, amount):
        self.y -= amount

    def header(self, address_lines):
        pdf = self.pdf
        pdf.setFillColor(PRIMARY)
        pdf.setFont('Helvetica-Bold', 15)
        pdf.drawString(self.left, self.y, _fit(pdf, self.company, self.width, 'Helvetica-Bold', 15))
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 8.5)
        for line in address_lines[:3]:
            self.y -= 4.4 * mm
            pdf.drawString(self.left, self.y, _fit(pdf, line, self.width, size=8.5))
        self.y -= 4 * mm
        pdf.setStrokeColor(BORDER)
        pdf.line(self.left, self.y, self.right, self.y)
        self.y -= 9 * mm

    def title(self, issue_date):
        pdf = self.pdf
        right_lines = (f'Receipt no: {self.number}', f'Issue date: {issue_date:%d-%m-%Y}')
        pdf.setFont('Helvetica', 9)
        right_width = max(pdf.stringWidth(line, 'Helvetica', 9) for line in right_lines)
        pdf.setFillColor(colors.black)
        pdf.setFont('Helvetica-Bold', 13)
        pdf.drawString(
            self.left, self.y,
            _fit(pdf, 'ASSET HANDOVER RECEIPT', self.width - right_width - 8 * mm, 'Helvetica-Bold', 13),
        )
        pdf.setFont('Helvetica', 9)
        pdf.drawRightString(self.right, self.y, right_lines[0])
        pdf.drawRightString(self.right, self.y - 4.8 * mm, right_lines[1])
        self.y -= 9.8 * mm

    def details(self, employee, assignment):
        pdf = self.pdf
        rows = (
            ('Employee', employee.full_name, 'Employee ID', employee.employee_id),
            ('Department', getattr(employee.department, 'name', ''), 'Designation', employee.designation),
            ('Email', employee.work_email or employee.personal_email, 'Condition',
             assignment.get_issue_condition_display()),
        )
        col2 = self.left + 96 * mm
        value1_x = self.left + 27 * mm
        value1_width = col2 - 4 * mm - value1_x
        value2_x = col2 + 27 * mm
        value2_width = self.right - 4 * mm - value2_x
        height = 6 * mm + 5.4 * mm * (len(rows) - 1) + 5 * mm
        pdf.setFillColor(LIGHT)
        pdf.rect(self.left, self.y - height, self.width, height, fill=1, stroke=0)
        row_y = self.y - 6 * mm
        for label1, value1, label2, value2 in rows:
            pdf.setFillColor(MUTED)
            pdf.setFont('Helvetica', 8)
            pdf.drawString(self.left + 4 * mm, row_y, label1)
            pdf.drawString(col2, row_y, label2)
            pdf.setFillColor(colors.black)
            pdf.setFont('Helvetica-Bold', 9)
            pdf.drawString(value1_x, row_y, _fit(pdf, value1 or '-', value1_width, 'Helvetica-Bold', 9))
            pdf.drawString(value2_x, row_y, _fit(pdf, value2 or '-', value2_width, 'Helvetica-Bold', 9))
            row_y -= 5.4 * mm
        self.y -= height + 7 * mm

    def heading(self, text):
        pdf = self.pdf
        pdf.setFillColor(colors.black)
        pdf.setFont('Helvetica-Bold', 9.5)
        pdf.drawString(self.left, self.y, text)
        self.y -= 5 * mm

    def _table_header(self):
        pdf = self.pdf
        self.ensure(8 * mm + 2 * mm)
        top = self.y
        pdf.setFillColor(PRIMARY)
        pdf.rect(self.left, top - 8 * mm, self.width, 8 * mm, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont('Helvetica-Bold', 8.5)
        x = self.left
        for index, (title, column_width) in enumerate(COLUMNS):
            if index == len(COLUMNS) - 1:
                pdf.drawRightString(x + column_width * mm - 2 * mm, top - 5.4 * mm, title)
            else:
                pdf.drawString(x + 2 * mm, top - 5.4 * mm, title)
            x += column_width * mm
        self.y -= 8 * mm

    def items_table(self, items):
        pdf = self.pdf
        self.heading('Items issued')
        self._table_header()
        for index, (asset, quantity) in enumerate(items, 1):
            name_lines = _wrap(pdf, asset.name, 56 * mm, size=8.5, max_lines=2)
            serial_lines = _wrap(pdf, asset.serial_number or '-', 40 * mm, size=8.5, max_lines=2)
            line_count = max(len(name_lines), len(serial_lines), 1)
            row_height = 10.2 * mm + 4.2 * mm * (line_count - 1)
            if not self.ensure(row_height + 2 * mm):
                self._table_header()
            top = self.y
            if index % 2 == 0:
                pdf.setFillColor(STRIPE)
                pdf.rect(self.left, top - row_height, self.width, row_height, fill=1, stroke=0)
            pdf.setFillColor(colors.black)
            pdf.setFont('Helvetica', 8.5)
            baseline = top - 5.7 * mm
            x = self.left
            pdf.drawString(x + 2 * mm, baseline, _fit(pdf, asset.asset_code, 26 * mm, size=8.5))
            x += 30 * mm
            for offset, line in enumerate(name_lines):
                pdf.drawString(x + 2 * mm, baseline - offset * 4.2 * mm, line)
            x += 60 * mm
            pdf.drawString(
                x + 2 * mm, baseline,
                _fit(pdf, getattr(asset.category, 'name', '') or '-', 24 * mm, size=8.5),
            )
            x += 28 * mm
            for offset, line in enumerate(serial_lines):
                pdf.drawString(x + 2 * mm, baseline - offset * 4.2 * mm, line)
            x += 44 * mm
            pdf.drawRightString(x + 10 * mm, baseline, str(quantity))
            pdf.setStrokeColor(BORDER)
            pdf.setLineWidth(0.4)
            pdf.line(self.left, top - row_height, self.right, top - row_height)
            self.y -= row_height

    def paragraph(self, text, *, size=8.5, leading=None, bold=False):
        pdf = self.pdf
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        leading = leading or size * 1.35
        lines = _wrap(pdf, text, self.width, font, size)
        needed = leading * len(lines) + 2 * mm
        self.ensure(needed)
        pdf.setFillColor(colors.black)
        block = pdf.beginText(self.left, self.y)
        block.setFont(font, size)
        block.setLeading(leading)
        for line in lines:
            block.textLine(line)
        pdf.drawText(block)
        self.y -= needed

    def acknowledgement(self, acknowledgement):
        if not acknowledgement:
            return
        self.heading('Acknowledgement')
        self.paragraph(
            f'{acknowledgement.get_acknowledgement_type_display()} acknowledgement by '
            f'{acknowledgement.acknowledgement_name} on '
            f'{acknowledgement.acknowledged_at:%d-%m-%Y %H:%M}',
        )
        if acknowledgement.confirmation_text:
            self.paragraph(f'Confirmation: {acknowledgement.confirmation_text}')
        if acknowledgement.document_id:
            self.paragraph(f'Attached document: {acknowledgement.document.title}')

    def signature(self):
        pdf = self.pdf
        line_y = max(min(self.y - 16 * mm, 55 * mm), 34 * mm)
        pdf.setStrokeColor(BORDER)
        pdf.setLineWidth(0.6)
        pdf.line(self.left, line_y, self.left + 70 * mm, line_y)
        pdf.line(self.right - 70 * mm, line_y, self.right, line_y)
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 8.5)
        pdf.drawString(self.left, line_y - 4.5 * mm, 'Employee signature')
        pdf.drawRightString(
            self.right, line_y - 4.5 * mm, _fit(pdf, f'For {self.company}', 70 * mm, size=8.5),
        )

    def footer(self, today):
        pdf = self.pdf
        pdf.setFillColor(MUTED)
        pdf.setFont('Helvetica', 7.5)
        pdf.drawString(
            self.left, FOOTER_Y,
            _fit(
                pdf,
                f'Generated from {self.company} on {today:%d-%m-%Y}. '
                'This receipt was issued with the asset handover.',
                self.width, size=7.5,
            ),
        )


def render_handover_receipt(assignment) -> bytes:
    profile = _profile()
    company, address_lines = _company(profile)
    employee = assignment.employee
    asset = assignment.asset
    acknowledgement = assignment.acknowledgements.select_related('document').order_by(
        '-acknowledged_at', '-id',
    ).first()
    items = issued_items(assignment)
    other_accessories = manual_accessories(assignment)

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    number = receipt_number(assignment)
    pdf.setTitle(f'Asset handover receipt {number}')
    pdf.setAuthor(company)
    pdf.setSubject('Asset handover acknowledgement')
    page = _Receipt(pdf, company, number)
    page.header(address_lines)
    page.title(assignment.issue_date)
    page.details(employee, assignment)
    page.items_table(items)
    page.gap(3.5 * mm)

    if asset.brand or asset.model_number:
        page.paragraph('Brand / model: ' + ' '.join(
            (part for part in (asset.brand, asset.model_number) if part),
        ))
    if other_accessories:
        page.paragraph('Other accessories: ' + ', '.join(other_accessories))
    if assignment.expected_return_date:
        page.paragraph(f'Expected return date: {assignment.expected_return_date:%d-%m-%Y}')
    page.gap(1.5 * mm)
    page.paragraph(
        f'Total items received: {len(items) + len(other_accessories)}', size=9, bold=True,
    )
    page.gap(2 * mm)
    page.paragraph(
        f'I, {employee.full_name}, acknowledge that I have received the items listed above from '
        f'{company}. I will use them for company work, keep them in good condition and return them '
        'when asked or when my employment ends.',
        size=9, leading=4.6 * mm,
    )
    page.gap(2 * mm)
    page.acknowledgement(acknowledgement)
    page.signature()
    page.footer(_today())
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _today():
    from django.utils import timezone
    return timezone.localdate()
