from io import BytesIO
from xml.sax.saxutils import escape as xml_escape

from docx import Document as WordDocument
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _table_rows(lines):
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if all(set(cell) <= {'-', ':', ' '} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _add_page_field(paragraph):
    run = paragraph.add_run()
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    instruction = OxmlElement('w:instrText')
    instruction.set(qn('xml:space'), 'preserve')
    instruction.text = ' PAGE '
    separate = OxmlElement('w:fldChar')
    separate.set(qn('w:fldCharType'), 'separate')
    text = OxmlElement('w:t')
    text.text = '1'
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    for element in (begin, instruction, separate, text, end):
        run._r.append(element)


def _document_lines(text):
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith('|') and line.endswith('|'):
            group = []
            while index < len(lines) and lines[index].startswith('|') and lines[index].endswith('|'):
                group.append(lines[index])
                index += 1
            yield 'table', _table_rows(group)
            continue
        yield 'line', line
        index += 1


def render_word(spec, output):
    doc = WordDocument()
    section = doc.sections[0]
    section.top_margin = Inches(0.9)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)

    normal = doc.styles['Normal']
    normal.font.name = 'Aptos'
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(7)
    for style_name, size in (('Heading 1', 18), ('Heading 2', 13)):
        style = doc.styles[style_name]
        style.font.name = 'Aptos Display'
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(31, 78, 121)

    header = section.header
    header_table = header.add_table(rows=1, cols=2, width=Inches(6.7))
    header_table.autofit = False
    header_table.columns[0].width = Inches(1.0)
    header_table.columns[1].width = Inches(5.7)
    logo = spec.get('logo')
    if logo:
        header_table.cell(0, 0).paragraphs[0].add_run().add_picture(BytesIO(logo), width=Inches(0.65))
    identity = header_table.cell(0, 1).paragraphs[0]
    identity.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    identity.add_run(spec['company'].get('display_name', '')).bold = True
    if spec['company'].get('address'):
        identity.add_run('\n' + spec['company']['address'])
    if spec['company'].get('email') or spec['company'].get('phone'):
        identity.add_run('\n' + ' | '.join(
            item for item in (spec['company'].get('email'), spec['company'].get('phone')) if item
        ))
    identity.add_run(
        f"\n{spec['document_number']}  |  {spec.get('document_date', '')}"
    )
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run(
        f"{spec['document_number']}  |  "
        f"{spec.get('footer_text') or spec['company'].get('document_footer') or 'Confidential'}  |  Page "
    )
    _add_page_field(footer)

    pending_table = []
    def flush_table():
        if not pending_table:
            return
        rows = pending_table[:]
        table = doc.add_table(rows=len(rows), cols=max(map(len, rows)))
        table.style = 'Light Shading Accent 1'
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                cell = table.cell(row_index, col_index)
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                cell.text = value
                if row_index == 0:
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
        pending_table.clear()

    if spec.get('header_text'):
        paragraph = doc.add_paragraph(spec['header_text'])
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for kind, value in _document_lines(spec['content']):
        if kind == 'table':
            pending_table.extend(value)
            continue
        flush_table()
        if not value.strip():
            doc.add_paragraph()
        elif value.startswith('# '):
            paragraph = doc.add_heading(value[2:].strip(), level=1)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif value.startswith('## '):
            doc.add_heading(value[3:].strip(), level=2)
        elif value.startswith('- '):
            doc.add_paragraph(value[2:].strip(), style='List Bullet')
        else:
            doc.add_paragraph(value)
    flush_table()

    doc.add_paragraph()
    signature_configuration = spec.get('signature_configuration') or {}
    include_counterparty = signature_configuration.get('include_counterparty', True)
    include_witness = signature_configuration.get('include_witness', False)
    sign_table = doc.add_table(rows=1, cols=2 if include_counterparty else 1)
    sign_table.style = 'Table Grid'
    company_label = spec['company'].get('legal_name', '')
    if spec['company'].get('brand_name'):
        company_label += f", operating under the brand name {spec['company']['brand_name']}"
    party_label = spec.get('party_name') or 'Second Party'
    signature_cells = [
        (f'For {company_label}', spec.get('signatory_name', ''), spec.get('signatory_designation', '')),
    ]
    if include_counterparty:
        signature_cells.append((f'For {party_label}', '', ''))
    for cell, (heading, name, designation) in zip(sign_table.rows[0].cells, signature_cells):
        cell.text = f'\n\n\n{heading}\n\nSignature: ____________________\nName: {name}\nDesignation: {designation}\nDate: ____________________'
    if include_witness:
        doc.add_paragraph('Witness: ____________________________________    Date: ____________________')
    doc.save(output)


def _pdf_paragraph(text, style):
    return Paragraph(xml_escape(text).replace('\n', '<br/>'), style)


def render_pdf(spec, output):
    buffer = output if hasattr(output, 'write') else BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='DocTitle', parent=styles['Title'], fontName='Helvetica-Bold',
        fontSize=16, leading=20, textColor=colors.HexColor('#1f4e79'),
        alignment=TA_CENTER, spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name='DocHeading', parent=styles['Heading2'], fontName='Helvetica-Bold',
        fontSize=12, leading=15, textColor=colors.HexColor('#1f4e79'), spaceBefore=9, spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name='DocBody', parent=styles['BodyText'], fontName='Helvetica',
        fontSize=9.5, leading=13, spaceAfter=6, splitLongWords=1,
    ))
    styles.add(ParagraphStyle(
        name='DocSmall', parent=styles['BodyText'], fontName='Helvetica',
        fontSize=8, leading=10, alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name='DocBrand', parent=styles['BodyText'], fontName='Helvetica-Bold',
        fontSize=9, leading=11, textColor=colors.HexColor('#1f2933'), spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name='DocFooter', parent=styles['BodyText'], fontName='Helvetica',
        fontSize=6.5, leading=7.5,
    ))
    story = []
    story.append(_pdf_paragraph(spec['company'].get('display_name', ''), styles['DocBrand']))
    if spec['company'].get('address'):
        story.append(_pdf_paragraph(spec['company']['address'], styles['DocSmall']))
    contact = ' | '.join(
        item for item in (
            spec['company'].get('email'), spec['company'].get('phone'),
            spec['company'].get('website'),
        ) if item
    )
    if contact:
        story.append(_pdf_paragraph(contact, styles['DocSmall']))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        xml_escape(f"{spec['document_number']}  |  {spec.get('document_date', '')}"),
        styles['DocSmall'],
    ))
    if spec.get('header_text'):
        story.append(_pdf_paragraph(spec['header_text'], styles['DocBody']))
    story.append(Spacer(1, 8))
    for kind, value in _document_lines(spec['content']):
        if kind == 'table':
            data = [[_pdf_paragraph(cell, styles['DocBody']) for cell in row] for row in value]
            table = Table(data, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#d9eaf7')),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#9aa8b2')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))
            story.extend((table, Spacer(1, 8)))
            continue
        if not value.strip():
            story.append(Spacer(1, 5))
        elif value.startswith('# '):
            story.append(Paragraph(xml_escape(value[2:].strip()), styles['DocTitle']))
        elif value.startswith('## '):
            story.append(Paragraph(xml_escape(value[3:].strip()), styles['DocHeading']))
        elif value.startswith('- '):
            story.append(_pdf_paragraph('\u2022 ' + value[2:].strip(), styles['DocBody']))
        else:
            story.append(_pdf_paragraph(value, styles['DocBody']))

    signature_configuration = spec.get('signature_configuration') or {}
    include_counterparty = signature_configuration.get('include_counterparty', True)
    include_witness = signature_configuration.get('include_witness', False)
    story.extend((Spacer(1, 22), Paragraph('SIGNATURES', styles['DocHeading'])))
    legal_name = spec['company'].get('display_name', '')
    party_name = spec.get('party_name') or 'Second Party'
    signature_data = [
        [_pdf_paragraph(f'For {legal_name}', styles['DocBody'])],
        [Paragraph(
            '<br/><br/><br/>Signature: ____________________'
            f"<br/>Name: {xml_escape(spec.get('signatory_name', ''))}"
            f"<br/>Designation: {xml_escape(spec.get('signatory_designation', ''))}<br/>Date: __________",
            styles['DocBody'],
        )],
    ]
    if include_counterparty:
        signature_data[0].append(_pdf_paragraph(f'For {party_name}', styles['DocBody']))
        signature_data[1].append(Paragraph(
            '<br/><br/><br/>Signature: ____________________<br/>Name: <br/>Date: __________',
            styles['DocBody'],
        ))
    signature_table = Table(
        signature_data,
        colWidths=(80 * mm, 80 * mm) if include_counterparty else (160 * mm,),
        hAlign='LEFT',
    )
    signature_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#9aa8b2')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(signature_table)
    if include_witness:
        story.append(Spacer(1, 10))
        story.append(_pdf_paragraph('Witness: ____________________________________    Date: ____________________', styles['DocBody']))

    logo_reader = ImageReader(BytesIO(spec['logo'])) if spec.get('logo') else None
    footer_text = (
        spec.get('footer_text') or spec['company'].get('document_footer') or 'Confidential'
    )

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#d0d7de'))
        identity = Paragraph(xml_escape(spec['company'].get('display_name', '')), styles['DocBrand'])
        identity_width = doc.width - (22 * mm if logo_reader else 0)
        _, identity_height = identity.wrap(identity_width, 28 * mm)
        identity.drawOn(canvas, doc.leftMargin, A4[1] - 5 * mm - identity_height)
        if logo_reader:
            canvas.drawImage(
                logo_reader, A4[0] - doc.rightMargin - 18 * mm, A4[1] - 22 * mm,
                width=16 * mm, height=16 * mm, preserveAspectRatio=True, mask='auto',
            )
        canvas.setStrokeColor(colors.HexColor('#d0d7de'))
        canvas.line(doc.leftMargin, 17 * mm, A4[0] - doc.rightMargin, 17 * mm)
        footer = Paragraph(
            xml_escape(f"{spec['document_number']}  |  {footer_text}"),
            styles['DocFooter'],
        )
        _, footer_height = footer.wrap(doc.width - 20 * mm, 13 * mm)
        footer.drawOn(canvas, doc.leftMargin, 2 * mm)
        canvas.setFont('Helvetica', 7.5)
        canvas.drawRightString(A4[0] - doc.rightMargin, 9 * mm, f'Page {doc.page}')
        canvas.restoreState()

    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=40 * mm, bottomMargin=22 * mm,
        title=spec['title'], author=spec['company'].get('legal_name', ''),
    )
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    if buffer is not output:
        return buffer.getvalue()
    return None
