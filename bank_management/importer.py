"""CSV / XLSX statement import: read raw rows, apply a column mapping, preview, and import idempotently."""
import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from accounting.money import q2

from . import services as S
from .models import ImportBatch

DATE_FORMATS = ['%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y', '%d-%b-%Y', '%d %b %Y', '%d/%m/%y', '%d-%m-%y',
                '%d-%b-%y', '%d %B %Y', '%m/%d/%Y']
FIELDS = [('date', 'Transaction date', True), ('value_date', 'Value date', False), ('description', 'Description', True),
          ('reference', 'Reference / cheque no.', False), ('debit', 'Withdrawal (debit)', False),
          ('credit', 'Deposit (credit)', False), ('amount', 'Signed amount (+ in / - out)', False),
          ('balance', 'Balance', False)]
MAX_ROWS = 5000


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(v)
    return str(v).strip()


def read_rows(upload):
    name = (upload.name or '').lower()
    data = upload.read()
    if name.endswith('.xlsx'):
        import openpyxl
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValueError('The XLSX file content is not a valid Excel workbook.')
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        if (ws.max_row or 0) > MAX_ROWS + 20 or (ws.max_column or 0) > 200:
            wb.close()
            raise ValueError(f'Workbook exceeds the import limit of {MAX_ROWS} rows or 200 columns.')
        rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
    elif name.endswith('.csv') or name.endswith('.txt'):
        if data.startswith(b'%PDF-') or b'\x00' in data[:4096]:
            raise ValueError('The file content is not a readable CSV/TXT statement.')
        text = data.decode('utf-8-sig', errors='replace')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    else:
        raise ValueError('Upload a .csv or .xlsx file.')
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) > MAX_ROWS:
        raise ValueError(f'Too many rows ({len(rows)}); limit is {MAX_ROWS}.')
    if not rows:
        raise ValueError('The file has no rows.')
    return rows


def guess_mapping(header):
    h = [c.lower() for c in header]

    def find(*words, exclude=()):
        for i, c in enumerate(h):
            if any(w in c for w in words) and not any(x in c for x in exclude):
                return str(i)
        return ''
    return {'date': find('txn date', 'transaction date', 'tran date', 'date', exclude=('value',)),
            'value_date': find('value'), 'description': find('narration', 'description', 'particular', 'remark', 'details'),
            'reference': find('ref', 'chq', 'cheque', 'utr'), 'debit': find('withdrawal', 'debit', 'dr', exclude=('desc', 'addr')),
            'credit': find('deposit', 'credit', 'cr', exclude=('desc',)), 'amount': '' , 'balance': find('balance'), 'header_row': '1'}


def parse_date(s):
    s = (s or '').strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    raise ValueError(f'Unrecognised date "{s}"')


def parse_amount(s):
    s = (s or '').strip().replace(',', '').replace('\u20b9', '').replace('INR', '').strip()
    if s in ('', '-'):
        return Decimal('0')
    neg = False
    if s.startswith('(') and s.endswith(')'):
        s, neg = s[1:-1], True
    low = s.lower()
    if low.endswith('dr'):
        s, neg = s[:-2].strip(), True
    elif low.endswith('cr'):
        s = s[:-2].strip()
    try:
        v = Decimal(s)
    except InvalidOperation:
        raise ValueError(f'Unrecognised amount "{s}"')
    return -v if neg else v


def apply_mapping(raw, mapping):
    """Returns list of dicts with parsed values or 'error'."""
    hr = int(mapping.get('header_row') or 1)
    col = {k: (int(mapping[k]) if str(mapping.get(k, '')).strip().isdigit() else None) for k, _, _ in FIELDS}
    if col['date'] is None or col['description'] is None:
        raise ValueError('Map at least the date and description columns.')
    if col['amount'] is None and col['debit'] is None and col['credit'] is None:
        raise ValueError('Map a signed amount column or debit/credit columns.')
    get = lambda r, k: (r[col[k]] if col[k] is not None and col[k] < len(r) else '')
    out = []
    for n, r in enumerate(raw[hr:], start=hr + 1):
        item = {'row': n, 'raw': r}
        try:
            d = parse_date(get(r, 'date'))
            if d is None:
                raise ValueError('missing date')
            if col['amount'] is not None:
                a = parse_amount(get(r, 'amount'))
                dr, cr = (-a, Decimal('0')) if a < 0 else (Decimal('0'), a)
            else:
                dr, cr = abs(parse_amount(get(r, 'debit'))), abs(parse_amount(get(r, 'credit')))
            dr, cr = q2(dr), q2(cr)
            if (dr > 0) == (cr > 0):
                raise ValueError('needs exactly one of withdrawal/deposit')
            bal = get(r, 'balance')
            item.update(date=d, value_date=parse_date(get(r, 'value_date')) if col['value_date'] is not None else None,
                        description=get(r, 'description'), reference=get(r, 'reference'), debit=dr, credit=cr,
                        balance=q2(parse_amount(bal)) if bal.strip() else None)
        except ValueError as e:
            item['error'] = str(e)
        out.append(item)
    return out


def preview(batch, mapping):
    from .models import BankTransaction
    rows = apply_mapping(batch.raw_rows, mapping)
    seen = {}
    for it in rows:
        if 'error' in it:
            continue
        base = (it['date'], it['credit'] - it['debit'], it['reference'].strip().lower(), ' '.join(it['description'].lower().split()))
        occ = seen.get(base, 0)
        seen[base] = occ + 1
        it['occurrence'] = occ
        it['duplicate'] = BankTransaction.objects.filter(
            row_hash=S.row_hash(batch.bank_account_id, it['date'], it['credit'] - it['debit'], it['reference'], it['description'], occ)).exists()
    return rows


def confirm(batch, mapping):
    from django.db import transaction
    if batch.status != ImportBatch.PENDING:
        raise ValueError('This import was already confirmed.')
    rows = preview(batch, mapping)
    imp = dup = err = 0
    notes = []
    with transaction.atomic():
        for it in rows:
            if 'error' in it:
                err += 1
                notes.append(f"Row {it['row']}: {it['error']}")
                continue
            _, created = S.add_transaction(batch.bank_account, it['date'], it['description'], it['debit'], it['credit'],
                                           it['reference'], it['value_date'], it['balance'], source='import', batch=batch,
                                           occurrence=it['occurrence'])
            imp, dup = (imp + 1, dup) if created else (imp, dup + 1)
        batch.mapping, batch.status, batch.imported_at = mapping, ImportBatch.DONE, timezone.now()
        batch.total_rows, batch.imported, batch.duplicates, batch.errors = len(rows), imp, dup, err
        batch.error_notes = '\n'.join(notes)[:5000]
        batch.raw_rows = []
        batch.save()
    return batch


def _norm(value):
    import re
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', (value or '').lower()).split())


def classify_draft_row(bank, data):
    """Return a conservative comparison classification and its evidence."""
    from .models import BankTransaction, ImportDraftRow
    if data.get('non_transaction'):
        return ImportDraftRow.NON_TRANSACTION, None, {}, 'This is a statement opening/closing balance, page total or summary row, not an individual bank transaction.'
    if data.get('extraction_error') or not data.get('transaction_date') or bool(data.get('debit')) == bool(data.get('credit')):
        return ImportDraftRow.UNCERTAIN, None, {}, 'Date, narration or debit/credit direction could not be reliably extracted.'
    if data.get('confidence') is not None and data['confidence'] < 80:
        return ImportDraftRow.UNCERTAIN, None, {}, 'Extraction confidence is low; verify the source PDF before import.'
    amount = q2(data.get('credit', 0) - data.get('debit', 0))
    date_value = data['transaction_date']
    ref = (data.get('reference') or '').strip().casefold()
    description = _norm(data.get('description'))
    candidates = BankTransaction.objects.filter(bank_account=bank)
    if ref:
        strong = list(candidates.filter(reference__iexact=ref)[:5])
        if len(strong) == 1:
            old = strong[0]
            changes = {}
            pairs = [('date', old.date, date_value), ('value_date', old.value_date, data.get('value_date')),
                     ('description', old.description, data.get('description') or ''),
                     ('debit', old.debit, q2(data.get('debit') or 0)), ('credit', old.credit, q2(data.get('credit') or 0)),
                     ('balance', old.balance, data.get('balance'))]
            for name, before, after in pairs:
                if before != after:
                    changes[name] = {'existing': str(before) if before is not None else '',
                                     'statement': str(after) if after is not None else ''}
            if not changes:
                if old.status == BankTransaction.IGNORED:
                    return ImportDraftRow.POSSIBLE, old, {}, 'An exact-looking transaction was previously marked ignored; review the ignore reason before considering a new entry.'
                return (ImportDraftRow.ACCOUNTING if old.matches.filter(active=True).exists() else ImportDraftRow.EXACT,
                        old, {}, 'The bank reference and transaction fields exactly match an existing row.' +
                        (' An active accounting match is already linked.' if old.matches.filter(active=True).exists() else ''))
            return ImportDraftRow.MODIFY, old, changes, 'The bank reference identifies an existing row, but one or more statement fields differ; approve each correction explicitly.'
        if len(strong) > 1:
            return ImportDraftRow.POSSIBLE, strong[0], {}, 'Multiple existing rows use this bank reference; choose the correct record before importing.'
    same_amount = list(candidates.filter(date=date_value, debit=q2(data.get('debit') or 0), credit=q2(data.get('credit') or 0))[:8])
    for old in same_amount:
        if old.reference.strip().casefold() == ref and _norm(old.description) == description:
            return (ImportDraftRow.ACCOUNTING if old.matches.filter(active=True).exists() else ImportDraftRow.EXACT,
                    old, {}, 'Bank account, date, direction, exact amount, reference and normalized narration match an existing row.' +
                    (' An accounting journal match is already linked.' if old.matches.filter(active=True).exists() else ''))
    if same_amount:
        return ImportDraftRow.POSSIBLE, same_amount[0], {}, 'An existing row has the same bank account, date and debit/credit amount, but its reference or narration differs.'
    if ref:
        by_ref = candidates.filter(reference__iexact=ref).first()
        if by_ref:
            return ImportDraftRow.POSSIBLE, by_ref, {}, 'The reference exists on another date or amount; manual duplicate review is required.'
    # A source row may have been ignored after an earlier import; surface a possible duplicate rather than reinsert it.
    ignored = candidates.filter(date=date_value, debit=q2(data.get('debit') or 0), credit=q2(data.get('credit') or 0), status=BankTransaction.IGNORED).first()
    if ignored:
        return ImportDraftRow.POSSIBLE, ignored, {}, 'A similar transaction was previously marked ignored; review its reason before retrying.'
    return ImportDraftRow.NEW, None, {}, 'No existing bank transaction matched this account/date/direction/amount/reference combination; a new entry is proposed.'


def create_draft_rows(batch, parsed_rows):
    """Persist parsed source rows and comparison evidence without writing bank transactions."""
    from decimal import Decimal
    from .models import ImportDraftRow
    batch.draft_rows.all().delete()
    created = []
    seen_in_batch = {}
    for index, data in enumerate(parsed_rows, 1):
        data = dict(data)
        data.setdefault('row_number', index)
        data['debit'] = q2(data.get('debit') or 0)
        data['credit'] = q2(data.get('credit') or 0)
        data['balance'] = q2(data['balance']) if data.get('balance') not in (None, '') else None
        confidence = data.get('confidence')
        if confidence is not None and not isinstance(confidence, Decimal):
            confidence = Decimal(str(confidence))
        classification, existing, changes, justification = classify_draft_row(batch.bank_account, data)
        fingerprint = (data.get('transaction_date'), data['debit'], data['credit'],
                       (data.get('reference') or '').strip().casefold(), _norm(data.get('description')))
        occurrence = seen_in_batch.get(fingerprint, 0)
        if occurrence and classification == ImportDraftRow.NEW:
            classification = ImportDraftRow.POSSIBLE
            justification = 'A second identical row appears within this statement. Confirm that it is a separate bank transaction before importing.'
        seen_in_batch[fingerprint] = occurrence + 1
        row = ImportDraftRow.objects.create(
            batch=batch, row_number=data['row_number'], fingerprint_occurrence=occurrence, source_page=data.get('source_page'),
            transaction_date=data.get('transaction_date'), value_date=data.get('value_date'),
            description=(data.get('description') or '')[:500], normalized_description=_norm(data.get('description'))[:500],
            reference=(data.get('reference') or '')[:120], debit=data['debit'], credit=data['credit'], balance=data['balance'],
            confidence=confidence, raw_source=data.get('raw_source') or {},
            extraction_error=(data.get('extraction_error') or '')[:500], classification=classification,
            justification=justification, existing_transaction=existing, proposed_changes=changes)
        created.append(row)
    return created
