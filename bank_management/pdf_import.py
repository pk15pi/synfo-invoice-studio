"""Local PDF statement extraction. Ambiguous rows are preserved for manual review."""
import csv
import hashlib
import io
import os
import re
import shutil
from datetime import date
from decimal import Decimal

from django.conf import settings

from . import importer

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 250
DATE_TOKEN = re.compile(r'(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)', re.I)
DATE_EXPR = r'(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]\d{2,4}|\d{4}-\d{2}-\d{2})'
MONEY_TOKEN = re.compile(r'(?<![\w/.-])\(?-?\s*(?:INR\s*)?(?:₹\s*)?\d[\d,]*(?:\.\d{1,2})?\s*\)?\s*(?:DR|CR)?(?![\w/.-])', re.I)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _page_lines(page, textpage=None):
    words = sorted((w[1], w[0], w[2], w[4]) for w in page.get_text('words', textpage=textpage))
    grouped = []
    for y0, x0, x1, text in words:
        if not grouped or abs(y0 - grouped[-1]['y']) > 3:
            grouped.append({'y': y0, 'ys': [], 'words': []})
        line = grouped[-1]
        line['ys'].append(y0)
        line['words'].append((x0, x1, text))
    result = []
    for line in grouped:
        line['words'].sort(key=lambda w: w[0])
        result.append({'y': sum(line['ys']) / len(line['ys']), 'words': line['words'], 'text': ' '.join(w[2] for w in line['words'])})
    return sorted(result, key=lambda item: item['y'])


def _header_anchors(pages):
    anchors = {}
    for lines in pages:
        for line in lines:
            words = line['words']
            line_text = ' '.join(word[2].lower() for word in words)
            has_date = any(re.sub(r'[^a-z]', '', word[2].lower()) in {'date', 'dt'} for word in words)
            has_description = any(term in line_text for term in ('narration', 'particular', 'description', 'details', 'remarks'))
            has_direction = any(term in line_text for term in ('debit', 'withdraw', 'credit', 'deposit', 'balance', 'amount'))
            if not (has_date and has_description and has_direction):
                continue
            for i, (x0, _, word) in enumerate(words):
                low = re.sub(r'[^a-z]', '', word.lower())
                prev = words[i - 1][2].lower() if i else ''
                if low in {'txn', 'trans', 'transaction', 'tran'} and i + 1 < len(words) and 'date' in words[i + 1][2].lower():
                    anchors.setdefault('date', x0)
                elif low in {'value', 'val'} and i + 1 < len(words) and 'date' in words[i + 1][2].lower():
                    anchors.setdefault('value_date', x0)
                elif low in {'date', 'dt'} and 'value' not in prev:
                    anchors.setdefault('date', x0)
                elif low in {'narration', 'particulars', 'description', 'details', 'remarks', 'remark'}:
                    anchors.setdefault('description', x0)
                elif low in {'reference', 'ref', 'chq', 'cheque', 'utr', 'transactionid', 'txnid'}:
                    anchors.setdefault('reference', x0)
                elif low in {'withdrawal', 'debit', 'dr', 'withdrawals'}:
                    anchors.setdefault('debit', x0)
                elif low in {'deposit', 'credit', 'cr', 'deposits'}:
                    anchors.setdefault('credit', x0)
                elif low in {'balance', 'closingbalance'}:
                    anchors.setdefault('balance', x0)
                elif low in {'amount', 'transactionamount'}:
                    anchors.setdefault('amount', x0)
    return anchors


def _cells(words, anchors):
    if not anchors:
        return {}
    ordered = sorted((x, key) for key, x in anchors.items())
    bounds = [(ordered[i][0] + ordered[i + 1][0]) / 2 for i in range(len(ordered) - 1)]
    cells = {key: [] for _, key in ordered}
    for x0, x1, token in words:
        pos = 0
        while pos < len(bounds) and x0 >= bounds[pos]:
            pos += 1
        cells[ordered[pos][1]].append(token)
    return {key: ' '.join(parts) for key, parts in cells.items()}


def _first_date(value):
    match = DATE_TOKEN.search(value or '')
    if not match:
        return None
    try:
        return importer.parse_date(match.group(1))
    except ValueError:
        return None


def _amounts(value):
    found = []
    for token in MONEY_TOKEN.findall(value or ''):
        try:
            number = importer.parse_amount(token)
            if number:
                found.append(abs(number))
        except ValueError:
            continue
    return found


def _amount_from_cell(value):
    tokens = MONEY_TOKEN.findall(value or '')
    if not tokens:
        return Decimal('0')
    try:
        return abs(importer.parse_amount(tokens[-1]))
    except ValueError:
        return Decimal('0')


def _extract_row_group(group, page_number, anchors, row_number):
    text = ' '.join(part['text'] for part in group).strip()
    first = group[0]
    cells = _cells(first['words'], anchors)
    row_date = _first_date(cells.get('date', '')) or _first_date(text)
    value_date = _first_date(cells.get('value_date', ''))
    description = cells.get('description', '').strip()
    reference = cells.get('reference', '').strip()
    if not description and not anchors:
        dates = list(DATE_TOKEN.finditer(text))
        description = text[dates[0].end():].strip(' |-') if dates else ''
        if len(dates) > 1 and not value_date:
            value_date = importer.parse_date(dates[1].group(1))
    if len(group) > 1:
        continuation = ' '.join(part['text'] for part in group[1:]).strip()
        if continuation:
            description = f'{description} {continuation}'.strip()
    debit = _amount_from_cell(cells.get('debit', ''))
    credit = _amount_from_cell(cells.get('credit', ''))
    balance = _amount_from_cell(cells.get('balance', '')) or None
    if not debit and not credit and cells.get('amount'):
        try:
            signed = importer.parse_amount(cells['amount'])
            debit, credit = (abs(signed), Decimal('0')) if signed < 0 else (Decimal('0'), signed)
        except ValueError:
            pass
    if not debit and not credit:
        vals = _amounts(text)
        # Fallback text extraction: the last amount is usually running balance;
        # the preceding amount is the transaction amount. Direction remains uncertain.
        if len(vals) >= 2:
            balance = vals[-1]
            amount = vals[-2]
            lower = text.lower()
            if re.search(r'\b(withdraw|debit| dr\b|payment|paid|charge)\b', lower):
                debit = amount
            elif re.search(r'\b(deposit|credit| cr\b|receipt|received)\b', lower):
                credit = amount
    errors = []
    if not row_date:
        errors.append('Could not identify the transaction date.')
    if not description:
        errors.append('Could not identify the original narration.')
    if bool(debit) == bool(credit):
        errors.append('Could not determine exactly one debit or credit amount from the PDF columns.')
    confidence = Decimal('95') if row_date and description and (bool(debit) != bool(credit)) and anchors else Decimal('55')
    return {
        'row_number': row_number, 'source_page': page_number, 'transaction_date': row_date,
        'value_date': value_date, 'description': description[:500], 'reference': reference[:120],
        'debit': debit, 'credit': credit, 'balance': balance, 'confidence': confidence,
        'raw_source': {'text': text[:3000], 'page': page_number}, 'extraction_error': ' '.join(errors),
    }


def _metadata(text, rows):
    flat = ' '.join(text.split())
    period = re.search(r'(?:statement\s+period|period|from)\s*[:\-]?\s*(' + DATE_EXPR + r')\s*(?:to|through|until|-)\s*(' + DATE_EXPR + r')', flat, re.I)
    start = end = None
    if period:
        try:
            start, end = importer.parse_date(period.group(1)), importer.parse_date(period.group(2))
        except ValueError:
            pass
    tx_dates = [r['transaction_date'] for r in rows if r.get('transaction_date')]
    warning = []
    if not start or not end:
        if tx_dates:
            start, end = min(tx_dates), max(tx_dates)
            warning.append('Statement header dates were not clear; the displayed range is inferred from extracted transaction dates. Confirm it manually.')
        else:
            warning.append('Statement period could not be identified. Enter the correct dates before approval.')
    if start and end and start > end:
        warning.append('Detected statement start date is later than its end date.')
    bank = ''
    for candidate in ('State Bank of India', 'HDFC Bank', 'ICICI Bank', 'IDFC FIRST Bank', 'Axis Bank', 'Kotak Mahindra Bank', 'Punjab National Bank', 'Bank of Baroda', 'Canara Bank', 'Union Bank of India'):
        if candidate.lower() in flat.lower():
            bank = candidate
            break
    acct = re.search(r'(?:account\s*(?:no\.?|number|#)|a/c\s*(?:no\.?|#))\s*[:\-]?\s*([A-Z0-9*X-]{4,34})', flat, re.I)
    mask = ('XXXX' + re.sub(r'\D', '', acct.group(1))[-4:]) if acct else ''
    def balance(label):
        m = re.search(label + r'\s*(?:balance)?\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*([-\d,]+(?:\.\d{1,2})?)', flat, re.I)
        if not m:
            return None
        try:
            return importer.parse_amount(m.group(1))
        except ValueError:
            return None
    return {'bank_name': bank, 'masked_account': mask, 'start': start, 'end': end,
            'opening_balance': balance('opening'), 'closing_balance': balance('closing'), 'warnings': warning}


def extract_pdf(upload):
    data = upload.read()
    if len(data) > MAX_PDF_BYTES:
        raise ValueError('PDF exceeds the 20 MB upload limit.')
    if not data.startswith(b'%PDF-'):
        raise ValueError('The file content is not a valid PDF. Upload a PDF, CSV or Excel statement.')
    try:
        import pymupdf as fitz
        doc = fitz.open(stream=data, filetype='pdf')
    except Exception as exc:
        raise ValueError('The PDF is corrupted or unreadable. Check the file, then try CSV/Excel fallback.') from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError('This PDF is password-protected. Save an unlocked statement PDF and upload it again.')
    if len(doc) > MAX_PDF_PAGES:
        doc.close()
        raise ValueError(f'The PDF has more than {MAX_PDF_PAGES} pages. Split it into smaller statements first.')
    page_texts, pages = [], []
    ocr_used = False
    for page_index, page in enumerate(doc, 1):
        text = page.get_text('text') or ''
        ocr_textpage = None
        if not text.strip():
            tess = os.environ.get('TESSERACT_CMD') or getattr(settings, 'BANK_TESSERACT_CMD', '') or shutil.which('tesseract')
            if tess:
                path = os.path.abspath(tess)
                os.environ['PATH'] = os.path.dirname(path) + os.pathsep + os.environ.get('PATH', '')
                os.environ.setdefault('TESSDATA_PREFIX', os.path.join(os.path.dirname(path), 'tessdata'))
                try:
                    ocr_textpage = page.get_textpage_ocr(language='eng', dpi=220, full=True)
                    text = page.get_text('text', textpage=ocr_textpage) or ''
                    ocr_used = True
                except Exception:
                    text = ''
            if not text.strip():
                page_texts.append('')
                pages.append([])
                continue
        page_texts.append(text)
        pages.append(_page_lines(page, ocr_textpage))
    doc.close()
    if not any(text.strip() for text in page_texts):
        raise ValueError('No selectable text was found. Scanned PDFs need local Tesseract OCR configured; alternatively upload CSV/Excel.')
    anchors = _header_anchors(pages)
    rows = []
    for page_number, lines in enumerate(pages, 1):
        current = []
        for line in lines:
            low = line['text'].lower()
            if any(x in low for x in ('opening balance', 'closing balance', 'page total', 'total debit', 'total credit', 'statement summary')):
                if current:
                    rows.append(_extract_row_group(current, page_number, anchors, len(rows) + 1))
                    current = []
                rows.append({'row_number': len(rows) + 1, 'source_page': page_number,
                             'description': line['text'][:500], 'raw_source': {'text': line['text'][:3000], 'page': page_number},
                             'confidence': Decimal('100'), 'non_transaction': True})
                continue
            cells = _cells(line['words'], anchors)
            starts = _first_date(cells.get('date', '')) or (_first_date(line['text']) if not anchors else None)
            if starts:
                if current:
                    rows.append(_extract_row_group(current, page_number, anchors, len(rows) + 1))
                current = [line]
            elif current and line['text'].strip():
                # Repeated headers and page decorations do not become narration.
                if not any(word in low for word in ('transaction date', 'value date', 'narration', 'particulars', 'withdrawal', 'deposit', 'balance')):
                    current.append(line)
        if current:
            rows.append(_extract_row_group(current, page_number, anchors, len(rows) + 1))
    full_text = '\n'.join(page_texts)
    meta = _metadata(full_text, rows)
    if any(not t.strip() for t in page_texts):
        meta['warnings'].append('At least one page did not yield readable text; check the original PDF before approving extracted rows.')
    if not anchors:
        meta['warnings'].append('Column headings were not confidently identified. Review all debit/credit directions and balances.')
    meta['page_count'] = len(page_texts)
    meta['ocr_used'] = ocr_used
    meta['anchors'] = anchors
    meta['file_sha256'] = sha256(data)
    return {'data': data, 'rows': rows, 'metadata': meta, 'text': full_text}


def csv_bytes(batch, rows):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['Bank Account', 'Statement Start Date', 'Statement End Date', 'Transaction Date', 'Value Date',
                     'Narration', 'Reference Number', 'Debit', 'Credit', 'Running Balance', 'Transaction Type',
                     'Extraction Confidence', 'Source Page', 'Classification', 'Justification'])
    for row in rows:
        writer.writerow([batch.masked_account or batch.bank_account.masked_number,
                         batch.confirmed_start_date or batch.detected_start_date,
                         batch.confirmed_end_date or batch.detected_end_date,
                         row.transaction_date, row.value_date, row.description, row.reference,
                         row.debit, row.credit, row.balance, 'Debit' if row.debit else ('Credit' if row.credit else ''),
                         row.confidence, row.source_page, row.get_classification_display(), row.justification])
    return out.getvalue().encode('utf-8-sig')
