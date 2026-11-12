from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, ProtectedError, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.core.paginator import Paginator
from invoice_app.pagination import paginate as qb_paginate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounting import services as A
from accounting.models import JournalLine
from accounting.money import q2
from invoice_app.table_sorting import apply_queryset_sort
from reporting import periods as rp

from . import importer, pdf_import, services as S
from .forms import BankAccountForm, DraftRowEditForm, DraftRowForm, ManualEntryForm, TransactionForm, TransferForm, UploadForm
from .models import BankAccount, BankTransaction, BankTransfer, ImportBatch, ImportDraftRow, ReconciliationMatch


def _err(e):
    return '; '.join(e.messages) if isinstance(e, ValidationError) else str(e)


def _cancel_block_reason(batch):
    if batch.status == 'cancelled':
        return 'This draft is already cancelled.'
    if batch.status == 'importing':
        return 'Import is in progress. Wait for it to finish before changing this draft.'
    if (batch.imported or batch.verified or batch.transactions.exists() or
            batch.draft_rows.filter(review_status__in=[ImportDraftRow.IMPORTED, ImportDraftRow.VERIFIED]).exists()):
        return 'This batch has already affected bank transactions. Use the bank transaction correction or reversal workflow.'
    if batch.status in {'imported', 'completed', 'verification_failed'}:
        return 'This batch has entered the import workflow and cannot be cancelled as an ordinary draft. Use transaction correction or reversal after reviewing its import results.'
    if batch.status not in {'uploaded', 'extracted', 'draft_ready', 'under_review', 'partially_approved', 'approved', 'pending'}:
        return 'This batch is no longer an unimported draft and cannot be cancelled.'
    return ''


def _import_source_path(batch):
    root = batch.source_batch or batch
    if not root.original_file:
        raise ValueError('The selected batch has no retained original upload.')
    storage = root.original_file.storage
    if not storage.exists(root.original_file.name):
        raise ValueError('The original upload is no longer available in statement storage.')
    return root


def _regenerated_rows(source, batch):
    """Extract a retained statement again and return parsed rows plus source metadata."""
    root = _import_source_path(source)
    with root.original_file.open('rb') as source_file:
        content = source_file.read()
    if not content:
        raise ValueError('The selected original upload is empty or corrupted.')
    digest = pdf_import.sha256(content)
    if root.file_sha256 and digest != root.file_sha256:
        raise ValueError('The retained original upload failed its SHA-256 integrity check.')
    upload = ContentFile(content, name=root.file_name)
    if root.source_kind == 'pdf':
        if not content.startswith(b'%PDF-'):
            raise ValueError('The selected original no longer has a valid PDF signature.')
        extracted = pdf_import.extract_pdf(upload)
        meta = extracted['metadata']
        parsed = extracted['rows']
        batch.bank_name = meta['bank_name'] or batch.bank_account.bank_name
        batch.masked_account = meta['masked_account'] or batch.bank_account.masked_number
        batch.detected_start_date = batch.confirmed_start_date = meta['start']
        batch.detected_end_date = batch.confirmed_end_date = meta['end']
        batch.opening_balance, batch.closing_balance = meta['opening_balance'], meta['closing_balance']
        batch.statement_warnings = meta['warnings']
        batch.extraction_metadata = {k: v for k, v in meta.items() if k not in (
            'warnings', 'opening_balance', 'closing_balance', 'start', 'end')}
    elif root.source_kind in ('csv', 'xlsx'):
        raw_rows = importer.read_rows(upload)
        mapping = dict(source.mapping or root.mapping or importer.guess_mapping(raw_rows[0]))
        if not mapping.get('date') or not mapping.get('description'):
            mapping = importer.guess_mapping(raw_rows[0])
        if not mapping.get('amount') and not mapping.get('debit') and not mapping.get('credit'):
            mapping = importer.guess_mapping(raw_rows[0])
        batch.raw_rows, batch.mapping = raw_rows, mapping
        mapped = importer.apply_mapping(raw_rows, mapping)
        parsed = [{
            'row_number': row['row'], 'transaction_date': row.get('date'), 'value_date': row.get('value_date'),
            'description': row.get('description', ''), 'reference': row.get('reference', ''),
            'debit': row.get('debit', 0), 'credit': row.get('credit', 0), 'balance': row.get('balance'),
            'confidence': 100 if not row.get('error') else 0,
            'extraction_error': row.get('error', ''), 'raw_source': {'source_row': row.get('raw', [])},
        } for row in mapped]
        dates = [row['transaction_date'] for row in parsed if row.get('transaction_date')]
        if dates:
            batch.detected_start_date = batch.confirmed_start_date = min(dates)
            batch.detected_end_date = batch.confirmed_end_date = max(dates)
    else:
        raise ValueError('The selected upload format is not supported for regeneration.')
    if not parsed:
        raise ValueError('No transaction or review rows could be extracted from the selected file.')
    return parsed


def _mark_regeneration_changes(previous, drafts):
    old_rows = list(previous.draft_rows.order_by('row_number', 'id'))
    old_by_signature = {}
    old_by_number = {row.row_number: row for row in old_rows}
    def signature(row):
        return (row.transaction_date, row.value_date, row.debit, row.credit, row.balance,
                (row.reference or '').strip().casefold(), importer._norm(row.description), row.fingerprint_occurrence)
    for row in old_rows:
        old_by_signature.setdefault(signature(row), []).append(row)
    used = set()
    counts = {'new': 0, 'changed': 0, 'unchanged': 0}
    for row in drafts:
        candidates = old_by_signature.get(signature(row), [])
        prior = next((candidate for candidate in candidates if candidate.pk not in used), None)
        if prior:
            kind = 'unchanged'
        else:
            prior = old_by_number.get(row.row_number)
            kind = 'changed' if prior and prior.pk not in used else 'new'
        if prior:
            used.add(prior.pk)
        row.regeneration_change = kind
        row.save(update_fields=['regeneration_change'])
        counts[kind] += 1
    counts['removed'] = sum(1 for row in old_rows if row.pk not in used)
    return counts


def _period_ctx(request, default='current_fy'):
    return {'f': rp.resolve(request.GET, default=default), 'periods': rp.OPTIONS, 'fy_list': rp.fy_choices(), 'params': request.GET}


def accounts(request):
    rows = list(BankAccount.objects.select_related('ledger_account'))
    for b in rows:
        b.book = A.balance(b.ledger_account)
        b.unrec = b.transactions.filter(status__in=['unreconciled', 'partial']).count()
    return render(request, 'bank_management/accounts.html', {'rows': rows})


def account_form(request, pk=None):
    obj = get_object_or_404(BankAccount, pk=pk) if pk else None
    form = BankAccountForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                b = form.save(commit=False)
                chosen = form.cleaned_data.get('ledger_choice')
                if chosen:
                    S.validate_ledger_account(chosen, b)
                    if b.pk and chosen.pk != b.ledger_account_id and JournalLine.objects.filter(account_id=b.ledger_account_id).exists():
                        raise S.BankError('The current ledger account already has postings; it cannot be swapped.')
                    b.ledger_account = chosen
                elif not b.pk:
                    b.ledger_account = S.create_ledger_account(b.name, b.kind)
                b.save()
                S.sync_opening(b)
        except ValidationError as e:
            messages.error(request, _err(e))
        else:
            messages.success(request, f'Saved {b}.')
            return redirect('bank:account_detail', b.pk)
    return render(request, 'bank_management/account_form.html', {'form': form, 'obj': obj})


def account_detail(request, pk):
    b = get_object_or_404(BankAccount.objects.select_related('ledger_account', 'opening_entry'), pk=pk)
    return render(request, 'bank_management/account_detail.html', {
        'b': b, 'book': A.balance(b.ledger_account), 'summary': S.summary(b, timezone.localdate()),
        'batches': b.imports.all()[:10]})


def transactions(request, pk):
    b = get_object_or_404(BankAccount, pk=pk)
    form = TransactionForm(request.POST or None, initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        try:
            t, created = S.add_transaction(b, c['date'], c['description'], c['debit'] or 0, c['credit'] or 0,
                                           c['reference'], c['value_date'], c['balance'])
            if created:
                messages.success(request, 'Statement line added.')
            else:
                messages.warning(request, 'An identical statement line already exists; skipped.')
            return redirect('bank:transactions', b.pk)
        except ValidationError as e:
            messages.error(request, _err(e))
    ctx = _period_ctx(request)
    qs = b.transactions.filter(date__gte=ctx['f'].start, date__lte=ctx['f'].end)
    st = request.GET.get('status', '')
    if st:
        qs = qs.filter(status=st)
    qs = qs.order_by('-date', '-id')
    qs, _, _ = apply_queryset_sort(qs, request, {
        'date': 'date', 'description': 'description', 'reference': 'reference', 'debit': 'debit',
        'credit': 'credit', 'balance': 'balance', 'source': 'source', 'status': 'status',
    })
    ctx.update(b=b, form=form, rows=qs[:1000], status=st, statuses=BankTransaction.STATUSES)
    return render(request, 'bank_management/transactions.html', ctx)


def txn_detail(request, pk):
    t = get_object_or_404(BankTransaction.objects.select_related('bank_account__ledger_account'), pk=pk)
    if request.method == 'POST':
        try:
            line = get_object_or_404(JournalLine.objects.select_related('entry'), pk=request.POST.get('line'))
            S.match(t, line, request.POST.get('amount') or None)
            messages.success(request, 'Matched.')
        except (ValidationError, ValueError) as e:
            messages.error(request, _err(e))
        return redirect('bank:txn_detail', t.pk)
    return render(request, 'bank_management/txn_detail.html', {
        't': t, 'remaining': S.txn_remaining(t), 'matches': t.matches.select_related('journal_line__entry'),
        'suggestions': S.suggestions(t)})


@require_POST
def txn_ignore(request, pk):
    t = get_object_or_404(BankTransaction, pk=pk)
    try:
        S.set_ignored(t, request.POST.get('ignore') == '1', request.POST.get('reason', ''))
    except ValidationError as e:
        messages.error(request, _err(e))
    return redirect('bank:txn_detail', t.pk)


@require_POST
def unmatch(request, pk):
    m = get_object_or_404(ReconciliationMatch, pk=pk)
    try:
        S.unmatch(m, request.POST.get('reason', ''))
        messages.success(request, 'Match removed.')
    except ValidationError as e:
        messages.error(request, _err(e))
    return redirect('bank:txn_detail', m.transaction_id)


def import_upload(request, pk):
    b = get_object_or_404(BankAccount, pk=pk)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        up = form.cleaned_data['file']
        name = up.name[:200]
        ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''
        content = up.read()
        digest = pdf_import.sha256(content)
        previous = ImportBatch.objects.filter(bank_account=b, file_sha256=digest, source_batch__isnull=True).first()
        if previous:
            messages.warning(request, f'This exact file was already uploaded as batch #{previous.pk}. Open that batch to continue review.')
            return redirect('bank:import_review', previous.pk) if previous.draft_rows.exists() else redirect('bank:import_batch', previous.pk)
        if ext == 'pdf':
            try:
                up.seek(0)
                extracted = pdf_import.extract_pdf(up)
            except Exception as e:  # noqa: BLE001 - present safe parser errors to local user
                messages.error(request, f'Could not extract the PDF: {e}')
                return render(request, 'bank_management/import_upload.html', {'b': b, 'form': form})
            meta = extracted['metadata']
            try:
                batch = ImportBatch.objects.create(
                    bank_account=b, file_name=name, source_kind='pdf', file_sha256=digest,
                    bank_name=meta['bank_name'] or b.bank_name, masked_account=meta['masked_account'] or b.masked_number,
                    detected_start_date=meta['start'], detected_end_date=meta['end'],
                    confirmed_start_date=meta['start'], confirmed_end_date=meta['end'],
                    opening_balance=meta['opening_balance'], closing_balance=meta['closing_balance'],
                    statement_warnings=meta['warnings'],
                    extraction_metadata={k: v for k, v in meta.items() if k not in ('warnings', 'opening_balance', 'closing_balance', 'start', 'end')},
                    status='draft_ready', created_by=request.user if getattr(request.user, 'is_authenticated', False) else None)
            except IntegrityError:
                previous = ImportBatch.objects.get(bank_account=b, file_sha256=digest, source_batch__isnull=True)
                messages.warning(request, f'This exact file was uploaded by another request as batch #{previous.pk}. Open that batch to continue review.')
                return redirect('bank:import_review', previous.pk) if previous.draft_rows.exists() else redirect('bank:import_batch', previous.pk)
            batch.original_file.save(name, ContentFile(content), save=False)
            batch.raw_rows = []
            drafts = importer.create_draft_rows(batch, extracted['rows'])
            batch.total_rows = len(drafts)
            batch.errors = sum(1 for row in drafts if row.classification == ImportDraftRow.UNCERTAIN)
            batch.save()
            _check_statement_totals(batch)
            batch.save(update_fields=['statement_warnings'])
            _refresh_batch_counts(batch)
            _store_batch_csv(batch)
            messages.success(request, f'PDF extracted into a review draft with {len(drafts)} transaction row(s). No bank transaction or accounting record has been created.')
            return redirect('bank:import_review', batch.pk)
        try:
            up.seek(0)
            rows = importer.read_rows(up)
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the user
            messages.error(request, f'Could not read file: {e}')
        else:
            try:
                batch = ImportBatch.objects.create(bank_account=b, file_name=name, raw_rows=rows,
                                                   mapping=importer.guess_mapping(rows[0]), source_kind='xlsx' if ext == 'xlsx' else 'csv',
                                                   file_sha256=digest,
                                                   created_by=request.user if getattr(request.user, 'is_authenticated', False) else None)
            except IntegrityError:
                previous = ImportBatch.objects.get(bank_account=b, file_sha256=digest, source_batch__isnull=True)
                messages.warning(request, f'This exact file was uploaded by another request as batch #{previous.pk}. Open that batch to continue review.')
                return redirect('bank:import_review', previous.pk) if previous.draft_rows.exists() else redirect('bank:import_batch', previous.pk)
            batch.original_file.save(name, ContentFile(content), save=True)
            return redirect('bank:import_batch', batch.pk)
    return render(request, 'bank_management/import_upload.html', {'b': b, 'form': form})


def import_batch(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    if request.method == 'POST' and request.POST.get('action') == 'cancel_batch':
        return import_cancel(request, batch.pk)
    if batch.status != ImportBatch.PENDING:
        if batch.draft_rows.exists():
            return redirect('bank:import_review', batch.pk)
        return render(request, 'bank_management/import_done.html', {'batch': batch})
    mapping = dict(batch.mapping)
    if request.method == 'POST':
        mapping = {k: request.POST.get(k, '') for k, _, _ in importer.FIELDS}
        mapping['header_row'] = request.POST.get('header_row') or '1'
    rows = None
    if request.method == 'POST':
        try:
            rows = importer.preview(batch, mapping)
            if request.POST.get('action') in ('create_draft', 'confirm'):
                parsed = []
                for row in rows:
                    parsed.append({
                        'row_number': row['row'], 'transaction_date': row.get('date'), 'value_date': row.get('value_date'),
                        'description': row.get('description', ''), 'reference': row.get('reference', ''),
                        'debit': row.get('debit', 0), 'credit': row.get('credit', 0), 'balance': row.get('balance'),
                        'confidence': 100 if not row.get('error') else 0,
                        'extraction_error': row.get('error', ''), 'raw_source': {'source_row': row.get('raw', [])},
                    })
                drafts = importer.create_draft_rows(batch, parsed)
                batch.mapping = mapping
                batch.status = 'draft_ready'
                dates = [row.transaction_date for row in drafts if row.transaction_date]
                if dates:
                    batch.detected_start_date = batch.confirmed_start_date = min(dates)
                    batch.detected_end_date = batch.confirmed_end_date = max(dates)
                batch.total_rows = len(drafts)
                batch.errors = sum(1 for row in drafts if row.classification == ImportDraftRow.UNCERTAIN)
                batch.raw_rows = []
                batch.save()
                _check_statement_totals(batch)
                batch.save(update_fields=['statement_warnings'])
                _refresh_batch_counts(batch)
                _store_batch_csv(batch)
                return redirect('bank:import_review', batch.pk)
            batch.mapping = mapping
            batch.save(update_fields=['mapping'])
        except ValueError as e:
            messages.error(request, str(e))
    header = batch.raw_rows[int(mapping.get('header_row') or 1) - 1] if batch.raw_rows else []
    cols = [(str(i), f'{i + 1}: {h}' if h else f'Column {i + 1}') for i, h in enumerate(header)]
    return render(request, 'bank_management/import_batch.html', {
        'batch': batch, 'mapping': mapping, 'fields': [(k, l, r, str(mapping.get(k, ''))) for k, l, r in importer.FIELDS], 'cols': cols, 'rows': rows,
        'sample': batch.raw_rows[:6],
        'counts': rows and {'ok': sum(1 for r in rows if 'error' not in r and not r['duplicate']),
                            'dup': sum(1 for r in rows if r.get('duplicate')), 'err': sum(1 for r in rows if 'error' in r)}})


def _check_statement_totals(batch):
    warnings = list(batch.statement_warnings or [])
    rows = list(batch.draft_rows.exclude(classification=ImportDraftRow.NON_TRANSACTION))
    txns = [row for row in rows if row.transaction_date]
    if txns:
        earliest, latest = min(row.transaction_date for row in txns), max(row.transaction_date for row in txns)
        if batch.confirmed_start_date and any(row.transaction_date < batch.confirmed_start_date for row in txns):
            warnings.append('One or more transactions are before the confirmed statement start date.')
        if batch.confirmed_end_date and any(row.transaction_date > batch.confirmed_end_date for row in txns):
            warnings.append('One or more transactions are after the confirmed statement end date.')
        debits = sum((row.debit for row in txns), 0)
        credits = sum((row.credit for row in txns), 0)
        if batch.opening_balance is not None and batch.closing_balance is not None:
            if q2(batch.opening_balance + credits - debits) != q2(batch.closing_balance):
                warnings.append(f"Statement arithmetic differs: opening + credits - debits is {q2(batch.opening_balance + credits - debits)}, but closing is {q2(batch.closing_balance)}.")
        balances = [row for row in txns if row.balance is not None]
        for prev, current in zip(balances, balances[1:]):
            expected = q2(prev.balance + current.credit - current.debit)
            if expected != q2(current.balance):
                warnings.append(f'Running balance does not reconcile between draft rows {prev.row_number} and {current.row_number}.')
                break
    batch.statement_warnings = list(dict.fromkeys(warnings))


def _refresh_batch_counts(batch):
    rows = batch.draft_rows.all()
    batch.matched = rows.filter(classification__in=[ImportDraftRow.EXACT, ImportDraftRow.ACCOUNTING]).count()
    batch.new_transactions = rows.filter(classification=ImportDraftRow.NEW).count()
    batch.possible_duplicates = rows.filter(classification=ImportDraftRow.POSSIBLE).count()
    batch.proposed_modifications = rows.filter(classification=ImportDraftRow.MODIFY).count()
    batch.failed_extractions = rows.filter(classification=ImportDraftRow.UNCERTAIN).count()
    batch.total_rows = rows.count()
    batch.extracted_transactions = rows.exclude(classification=ImportDraftRow.NON_TRANSACTION).count()
    batch.save(update_fields=['matched', 'new_transactions', 'possible_duplicates', 'proposed_modifications', 'failed_extractions', 'total_rows', 'extracted_transactions'])


def _set_batch_review_status(batch):
    rows = batch.draft_rows.all()
    approved = rows.filter(review_status=ImportDraftRow.APPROVED).count()
    pending = rows.filter(review_status=ImportDraftRow.PENDING).count()
    batch.status = 'partially_approved' if approved and pending else 'approved' if approved else 'under_review'
    batch.save(update_fields=['status'])


def _store_batch_csv(batch):
    rows = batch.draft_rows.all()
    content = pdf_import.csv_bytes(batch, rows)
    batch.generated_csv.save(f'bank_statement_{batch.pk}.csv', ContentFile(content), save=True)


@require_POST
def import_cancel(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    reason = (request.POST.get('cancellation_reason') or '').strip()
    try:
        with transaction.atomic():
            locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
            block_reason = _cancel_block_reason(locked)
            if block_reason:
                raise ValueError(block_reason)
            if not reason:
                raise ValueError('Enter a cancellation reason so the draft history explains why processing stopped.')
            actor = request.user if getattr(request.user, 'is_authenticated', False) else None
            locked.cancelled_from_status = locked.status
            locked.status = 'cancelled'
            locked.cancelled_at = timezone.now()
            locked.cancelled_by = actor
            locked.cancelled_by_label = actor.get_username() if actor else 'Local user'
            locked.cancellation_reason = reason[:500]
            locked.save(update_fields=['status', 'cancelled_from_status', 'cancelled_at', 'cancelled_by',
                                       'cancelled_by_label', 'cancellation_reason'])
        messages.warning(request, f'Draft version {locked.version_number} cancelled. The uploaded statement, extracted rows and review decisions remain available.')
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('bank:import_review', batch.pk)


def import_csv_download(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if not batch.generated_csv:
        raise Http404('No extracted CSV is available for this batch.')
    response = FileResponse(batch.generated_csv.open('rb'), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="bank_statement_{batch.pk}.csv"'
    return response


def import_source_download(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if not batch.original_file:
        raise Http404('The original source file is unavailable.')
    response = FileResponse(batch.original_file.open('rb'), content_type='application/pdf' if batch.source_kind == 'pdf' else 'application/octet-stream')
    from django.utils.http import content_disposition_header
    response['Content-Disposition'] = content_disposition_header(True, batch.file_name)
    return response


@require_POST
def import_regenerate(request, pk):
    selected = get_object_or_404(ImportBatch.objects.select_related('bank_account', 'source_batch'), pk=pk)
    try:
        root = _import_source_path(selected)
        parsed = _regenerated_rows(selected, selected)
    except Exception as exc:  # noqa: BLE001 - report local parser/storage failures without mutating prior versions
        messages.error(request, f'Could not regenerate this selected statement: {exc}')
        return redirect('bank:import_batch', selected.pk) if selected.status == ImportBatch.PENDING and not selected.draft_rows.exists() else redirect('bank:import_review', selected.pk)

    try:
        with transaction.atomic():
            selected = ImportBatch.objects.select_for_update().select_related('bank_account', 'source_batch').get(pk=pk)
            root = ImportBatch.objects.select_for_update().get(pk=selected.source_batch_id or selected.pk)
            if selected.status == 'importing':
                raise ValueError('This batch is currently importing. Wait until that operation finishes, then regenerate from its retained upload.')
            version_number = (ImportBatch.objects.filter(Q(pk=root.pk) | Q(source_batch=root))
                              .aggregate(latest=Max('version_number'))['latest'] or root.version_number) + 1
            version = ImportBatch.objects.create(
                bank_account=root.bank_account, source_batch=root, version_number=version_number,
                file_name=root.file_name, source_kind=root.source_kind, original_file=root.original_file.name,
                file_sha256=root.file_sha256, bank_name=selected.bank_name or root.bank_name,
                masked_account=selected.masked_account or root.masked_account, status='draft_ready',
                detected_start_date=selected.detected_start_date,
                detected_end_date=selected.detected_end_date,
                confirmed_start_date=selected.confirmed_start_date,
                confirmed_end_date=selected.confirmed_end_date,
                opening_balance=selected.opening_balance, closing_balance=selected.closing_balance,
                statement_warnings=list(selected.statement_warnings or []),
                extraction_metadata=dict(selected.extraction_metadata or {}),
                created_by=request.user if getattr(request.user, 'is_authenticated', False) else None,
                mapping=dict(selected.mapping or root.mapping), raw_rows=list(selected.raw_rows or []),
            )
            drafts = importer.create_draft_rows(version, parsed)
            version.raw_rows = []
            version.total_rows = len(drafts)
            version.errors = sum(1 for row in drafts if row.classification == ImportDraftRow.UNCERTAIN)
            version.save()
            _check_statement_totals(version)
            version.save(update_fields=['statement_warnings'])
            _refresh_batch_counts(version)
            changes = _mark_regeneration_changes(selected, drafts)
            previous_counts = {key: selected.draft_rows.filter(classification=key).count()
                               for key, _ in ImportDraftRow.CLASSIFICATIONS}
            version.regeneration_summary = {
                'previous_batch_id': selected.pk,
                'original_transaction_count': selected.extracted_transactions,
                'previous_source_rows': selected.total_rows,
                'newly_extracted_count': version.extracted_transactions,
                'existing_matches': version.matched,
                'new_transactions': version.new_transactions,
                'possible_duplicates': version.possible_duplicates,
                'proposed_modifications': version.proposed_modifications,
                'extraction_failures': version.failed_extractions,
                'excluded_rows': version.draft_rows.filter(review_status=ImportDraftRow.EXCLUDED).count(),
                'previous_excluded_rows': selected.draft_rows.filter(review_status=ImportDraftRow.EXCLUDED).count(),
                'new_rows': changes['new'], 'changed_rows': changes['changed'],
                'unchanged_rows': changes['unchanged'], 'removed_rows': changes['removed'],
                'previous_classifications': previous_counts,
            }
            version.save(update_fields=['regeneration_summary'])
        _store_batch_csv(version)
    except (ValueError, IntegrityError) as exc:
        messages.error(request, f'Could not create a new draft version: {exc}')
        return redirect('bank:import_batch', selected.pk) if selected.status == ImportBatch.PENDING and not selected.draft_rows.exists() else redirect('bank:import_review', selected.pk)
    except Exception as exc:  # noqa: BLE001 - preserve older versions if storage fails
        messages.error(request, f'Regeneration did not complete; the previous draft versions were preserved. Details: {exc}')
        return redirect('bank:import_batch', selected.pk) if selected.status == ImportBatch.PENDING and not selected.draft_rows.exists() else redirect('bank:import_review', selected.pk)
    messages.success(request, f'Regenerated {version.extracted_transactions} transaction row(s) as Draft Version {version.version_number}. Existing bank rows were rechecked; no transactions were inserted.')
    return redirect('bank:import_review', version.pk)


def import_review(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'cancel_batch':
            return import_cancel(request, batch.pk)
        if batch.status in ('imported', 'completed', 'cancelled'):
            if batch.status == 'cancelled':
                messages.error(request, 'This draft is already cancelled and cannot be processed further.')
            else:
                messages.error(request, 'This batch has been imported and cannot be cancelled as a draft. Use the bank transaction correction or reversal workflow; imported accounting entries are not reversed here.')
            return redirect('bank:import_review', batch.pk)
        if action == 'confirm_period':
            try:
                start = importer.parse_date(request.POST.get('from_date', ''))
                end = importer.parse_date(request.POST.get('to_date', ''))
                if not start or not end or start > end:
                    raise ValueError('Enter a valid date range where From Date is on or before To Date.')
                outside = batch.draft_rows.filter(transaction_date__lt=start).exists() or batch.draft_rows.filter(transaction_date__gt=end).exists()
                if (outside or batch.statement_warnings) and not request.POST.get('acknowledge_period'):
                    raise ValueError('Review the extracted dates and statement warnings, then explicitly acknowledge them before saving the period.')
                batch.confirmed_start_date, batch.confirmed_end_date = start, end
                metadata = dict(batch.extraction_metadata or {})
                metadata['date_range_acknowledged'] = bool(outside)
                metadata['statement_warnings_acknowledged'] = bool(outside or batch.statement_warnings)
                batch.extraction_metadata = metadata
                batch.status = 'under_review'
                batch.save(update_fields=['confirmed_start_date', 'confirmed_end_date', 'extraction_metadata', 'status'])
                _check_statement_totals(batch)
                batch.save(update_fields=['statement_warnings'])
                messages.success(request, 'Confirmed statement period saved. Review any remaining warnings before import.')
            except ValueError as exc:
                messages.error(request, str(exc))
            _refresh_batch_counts(batch)
            _set_batch_review_status(batch)
            return redirect('bank:import_review', batch.pk)
        if action in ('save_row', 'approve_row', 'exclude_row', 'new_and_approve', 'approve_change'):
            draft = get_object_or_404(batch.draft_rows, pk=request.POST.get('row_id'))
            if action == 'save_row':
                form = DraftRowForm(request.POST, instance=draft)
                if form.is_valid():
                    draft = form.save(commit=False)
                    draft.normalized_description = importer._norm(draft.description)
                    data = {'transaction_date': draft.transaction_date, 'value_date': draft.value_date,
                            'description': draft.description, 'reference': draft.reference,
                            'debit': draft.debit, 'credit': draft.credit, 'balance': draft.balance,
                            'confidence': 100, 'extraction_error': ''}
                    draft.classification, draft.existing_transaction, draft.proposed_changes, draft.justification = importer.classify_draft_row(batch.bank_account, data)
                    draft.extraction_error = ''
                    draft.review_status = ImportDraftRow.PENDING
                    draft.save()
                    messages.success(request, 'Draft row saved and comparison refreshed. No bank or accounting record was changed.')
                else:
                    messages.error(request, 'Correct the row fields: ' + '; '.join(form.errors.get('__all__', [])))
            elif action == 'exclude_row':
                draft.review_status, draft.decision, draft.reviewed_at = ImportDraftRow.EXCLUDED, 'exclude', timezone.now()
                draft.save(update_fields=['review_status', 'decision', 'reviewed_at'])
            elif action == 'new_and_approve':
                if draft.classification not in (ImportDraftRow.POSSIBLE, ImportDraftRow.UNCERTAIN):
                    messages.error(request, 'This action is only for a possible duplicate or uncertain row.')
                elif not draft.transaction_date or not draft.description or bool(draft.debit) == bool(draft.credit):
                    messages.error(request, 'Complete and validate the transaction date, narration, and exactly one debit or credit before approval.')
                else:
                    draft.classification = ImportDraftRow.NEW
                    draft.justification = 'Manually reviewed and explicitly approved as a new transaction by the local user.'
                    draft.review_status, draft.decision, draft.reviewed_at = ImportDraftRow.APPROVED, 'new', timezone.now()
                    draft.save()
            elif action == 'approve_change':
                if draft.classification != ImportDraftRow.MODIFY or not draft.existing_transaction_id:
                    messages.error(request, 'This row has no proposed existing-record modification.')
                elif draft.existing_transaction.matches.filter(active=True).exists():
                    messages.error(request, 'This bank transaction has active accounting matches. Review/unmatch those records before modifying it.')
                else:
                    draft.review_status, draft.decision, draft.reviewed_at = ImportDraftRow.APPROVED, 'modify', timezone.now()
                    draft.save(update_fields=['review_status', 'decision', 'reviewed_at'])
            else:
                if draft.classification in (ImportDraftRow.NEW, ImportDraftRow.EXACT, ImportDraftRow.ACCOUNTING):
                    decision = 'new' if draft.classification == ImportDraftRow.NEW else 'match'
                    draft.review_status, draft.decision, draft.reviewed_at = ImportDraftRow.APPROVED, decision, timezone.now()
                    draft.save(update_fields=['review_status', 'decision', 'reviewed_at'])
                else:
                    messages.error(request, 'Possible duplicates and modifications require individual review.')
            _refresh_batch_counts(batch)
            _set_batch_review_status(batch)
            return redirect('bank:import_review', batch.pk)
        if action == 'approve_selected':
            selected = request.POST.getlist('row_ids')
            approved = 0
            for draft in batch.draft_rows.filter(pk__in=selected, classification__in=[ImportDraftRow.NEW, ImportDraftRow.EXACT, ImportDraftRow.ACCOUNTING]):
                decision = 'new' if draft.classification == ImportDraftRow.NEW else 'match'
                draft.review_status, draft.decision, draft.reviewed_at = ImportDraftRow.APPROVED, decision, timezone.now()
                draft.save(update_fields=['review_status', 'decision', 'reviewed_at'])
                approved += 1
            messages.success(request, f'Approved {approved} straightforward row(s). Possible duplicates, modifications and uncertain rows were left for individual review.')
            _set_batch_review_status(batch)
            return redirect('bank:import_review', batch.pk)
        if action == 'import_approved':
            return _import_approved_rows(request, batch)
    all_rows = batch.draft_rows.select_related('existing_transaction', 'actual_transaction').all()
    counts = {key: all_rows.filter(classification=key).count() for key, _ in ImportDraftRow.CLASSIFICATIONS}
    summary = {'debits': sum((x.debit for x in all_rows), 0), 'credits': sum((x.credit for x in all_rows), 0),
               'approved': all_rows.filter(review_status=ImportDraftRow.APPROVED).count(),
               'verified': all_rows.filter(verification_status='verified').count(),
               'failed': all_rows.filter(verification_status='failed').count()}
    rows = qb_paginate(request, all_rows)
    root = batch.source_batch or batch
    versions = ImportBatch.objects.filter(Q(pk=root.pk) | Q(source_batch=root)).order_by('version_number', 'id')
    source_available = False
    try:
        source_root = _import_source_path(batch)
        source_available = source_root.original_file.storage.exists(source_root.original_file.name)
    except Exception:  # noqa: BLE001 - missing storage is shown as a disabled regeneration action
        pass
    cancel_block_reason = _cancel_block_reason(batch)
    period_label = ('%s to %s' % (batch.confirmed_start_date or batch.detected_start_date or 'Period not detected',
                                  batch.confirmed_end_date or batch.detected_end_date or 'Period not detected'))
    cancel_confirmation = (f'Cancel batch #{batch.pk}, Draft Version {batch.version_number}, for {period_label}? '
                           'Processing will stop. The original upload, extracted rows and review history will remain; this will not reverse imported transactions.')
    return render(request, 'bank_management/import_review.html', {
        'batch': batch, 'rows': rows, 'counts': counts, 'summary': summary,
        'review_statuses': ImportDraftRow.REVIEW_STATUSES,
        'classification_options': [(key, label, counts[key]) for key, label in ImportDraftRow.CLASSIFICATIONS],
        'versions': versions, 'source_available': source_available,
        'cancel_block_reason': cancel_block_reason, 'can_cancel': not cancel_block_reason,
        'cancel_confirmation': cancel_confirmation,
        'open_draft_count': _open_draft_rows(batch).count(),
        'delete_block_reason': _delete_block_reason(batch), 'can_delete': not _delete_block_reason(batch),
        'can_review': batch.status not in {'imported', 'completed', 'cancelled', 'importing'},
        'can_regenerate': source_available and batch.status != 'importing',
    })


MIGRATION_CLASSIFICATIONS = {'expense', 'vendor_payment', 'salary', 'reimbursement', 'customer_receipt',
                             'tax', 'bank_transfer', 'journal', 'asset', 'loan', 'capital', 'other', 'review'}


def _draft_company_expense(batch, row, category_id):
    """Create the statement-line follow-up as a draft only; never post, pay or approve."""
    from expense_management.models import CompanyExpense, ExpenseCategory
    txn = row.actual_transaction
    category = ExpenseCategory.for_company().filter(pk=category_id).first() if category_id else None
    if category is None:
        category = (ExpenseCategory.for_company().filter(name__iexact='Other').first()
                    or ExpenseCategory.for_company().first())
    if category is None:
        return None, 'No active company expense category is configured.'
    existing = CompanyExpense.objects.filter(source_bank_transaction=txn).first()
    if existing:
        return existing, ''
    expense = CompanyExpense(
        source_bank_transaction=txn, expense_date=txn.date, category=category,
        description=(txn.description or 'Bank statement withdrawal')[:300],
        amount_mode=CompanyExpense.DIRECT, total_amount=txn.debit, currency='INR', exchange_rate=1,
        is_draft=True, payment_status=CompanyExpense.UNPAID,
        remarks=(f'Draft from bank statement import batch #{batch.pk}, row {row.row_number}, '
                 f'bank transaction #{txn.pk} ({txn.date:%d-%m-%Y}). Review and finalize in Expense Management.'))
    expense.full_clean()
    expense.save()
    return expense, ''


def _draft_employee_claim(batch, row, employee_id, category_id):
    """Create the reimbursement follow-up as a draft only; never submit, approve or reimburse."""
    from employee_management.models import Employee
    from expense_management.models import EmployeeExpenseClaim, ExpenseCategory
    txn = row.actual_transaction
    employee = Employee.objects.filter(pk=employee_id).first() if employee_id else None
    if employee is None:
        return None, 'Choose the employee before a draft claim can be created.'
    category = ExpenseCategory.for_claims().filter(pk=category_id).first() if category_id else None
    if category is None:
        category = (ExpenseCategory.for_claims().filter(name__iexact='Employee Expenses').first()
                    or ExpenseCategory.for_claims().first())
    if category is None:
        return None, 'No active employee claim category is configured.'
    existing = EmployeeExpenseClaim.objects.filter(source_bank_transaction=txn).first()
    if existing:
        return existing, ''
    claim = EmployeeExpenseClaim(
        source_bank_transaction=txn, employee=employee, claim_date=timezone.localdate(),
        expense_date=txn.date, category=category,
        description=(txn.description or 'Bank statement reimbursement')[:300],
        amount_claimed=txn.debit, currency='INR', exchange_rate=1,
        status=EmployeeExpenseClaim.DRAFT,
        remarks=(f'Draft from bank statement import batch #{batch.pk}, row {row.row_number}, '
                 f'bank transaction #{txn.pk} ({txn.date:%d-%m-%Y}). Not submitted, approved or reimbursed.'))
    claim.full_clean()
    claim.save()
    return claim, ''


def _draft_follow_up_links(rows):
    from expense_management.models import CompanyExpense, EmployeeExpenseClaim
    txn_ids = [row.actual_transaction_id for row in rows if row.actual_transaction_id]
    expenses = {e.source_bank_transaction_id: e for e in
                CompanyExpense.objects.filter(source_bank_transaction_id__in=txn_ids)}
    claims = {c.source_bank_transaction_id: c for c in
              EmployeeExpenseClaim.objects.filter(source_bank_transaction_id__in=txn_ids)}
    for row in rows:
        row.accounting_linked = bool(row.actual_transaction_id and
                                     row.actual_transaction.matches.filter(active=True).exists())
        row.draft_expense = expenses.get(row.actual_transaction_id)
        row.draft_claim = claims.get(row.actual_transaction_id)


def import_migration(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    if batch.status == 'cancelled':
        messages.error(request, 'A cancelled draft cannot be migrated or classified for accounting.')
        return redirect('bank:import_review', batch.pk)
    rows = list(batch.draft_rows.filter(verification_status='verified').select_related('actual_transaction').order_by('row_number'))
    _draft_follow_up_links(rows)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'defer':
            batch.accounting_migration_status = 'deferred'
            batch.save(update_fields=['accounting_migration_status'])
            messages.info(request, 'Accounting classification was deferred. The bank statement import remains verified and unchanged.')
            return redirect('bank:import_review', batch.pk)
        if action == 'save_classification':
            created_expenses, created_claims, warnings = [], [], []
            for row in rows:
                classification = request.POST.get(f'class_{row.pk}', '')
                if classification not in MIGRATION_CLASSIFICATIONS:
                    continue
                row.migration_classification = classification
                row.migration_status = 'review' if classification in ('review', 'other') else 'classified'
                row.migration_justification = 'User selected a classification for follow-up. No accounting record was created.'
                txn = row.actual_transaction
                if classification in ('expense', 'reimbursement'):
                    if txn is None or txn.debit <= 0:
                        warnings.append(f'Row {row.row_number}: only a withdrawal can become a draft '
                                        f'{"company expense" if classification == "expense" else "employee claim"}.')
                    elif row.draft_expense or row.draft_claim:
                        linked = row.draft_expense or row.draft_claim
                        row.migration_status = 'draft'
                        row.migration_justification = (
                            f'{linked.expense_id if row.draft_expense else linked.claim_id} draft is already linked. '
                            'It has not been posted, approved, paid or reconciled.')
                    else:
                        try:
                            if classification == 'expense':
                                draft, error = _draft_company_expense(
                                    batch, row, request.POST.get(f'expense_category_{row.pk}'))
                            else:
                                draft, error = _draft_employee_claim(
                                    batch, row, request.POST.get(f'claim_employee_{row.pk}'),
                                    request.POST.get(f'claim_category_{row.pk}'))
                        except (ValidationError, IntegrityError) as exc:
                            draft, error = None, _err(exc)
                        if draft:
                            row.migration_status = 'draft'
                            if classification == 'expense':
                                created_expenses.append(draft.expense_id)
                                row.migration_justification = (
                                    f'Draft company expense {draft.expense_id} created. It is not posted or paid.')
                            else:
                                created_claims.append(draft.claim_id)
                                row.migration_justification = (
                                    f'Draft employee claim {draft.claim_id} created. It is not submitted, approved or reimbursed.')
                        else:
                            warnings.append(f'Row {row.row_number}: {error}')
                            row.migration_justification = f'Classification saved, but no draft was created: {error}'
                row.save(update_fields=['migration_classification', 'migration_status', 'migration_justification'])
            batch.accounting_migration_status = 'review'
            batch.save(update_fields=['accounting_migration_status'])
            messages.success(request,
                             f'Classifications saved. {len(created_expenses)} draft company expense(s) and '
                             f'{len(created_claims)} draft employee claim(s) created. Drafts are not posted, '
                             'approved, paid or reconciled here.')
            for warning in warnings[:6]:
                messages.warning(request, warning)
            if len(warnings) > 6:
                messages.warning(request, f'{len(warnings) - 6} more row(s) need attention before a draft can be created.')
            return redirect('bank:import_migration', batch.pk)
    from employee_management.models import Employee
    from expense_management.models import ExpenseCategory
    return render(request, 'bank_management/import_migration.html', {
        'batch': batch, 'rows': rows, 'categories': ImportDraftRow.MIGRATION_CATEGORIES,
        'company_categories': ExpenseCategory.for_company(),
        'claim_categories': ExpenseCategory.for_claims(),
        'employees': Employee.objects.filter(status__in=Employee.EMPLOYED).order_by('first_name', 'last_name'),
    })


def _import_approved_rows(request, batch):
    if batch.status == 'cancelled':
        messages.error(request, 'This draft is cancelled. Regenerate a new version from its retained upload if review needs to resume.')
        return redirect('bank:import_review', batch.pk)
    if batch.status in ('imported', 'completed'):
        messages.info(request, 'This batch has already been imported. Reopening it will not create duplicate transactions.')
        return redirect('bank:import_review', batch.pk)
    if not batch.confirmed_start_date or not batch.confirmed_end_date:
        messages.error(request, 'Confirm the statement period before importing approved rows.')
        return redirect('bank:import_review', batch.pk)
    outside = batch.draft_rows.filter(transaction_date__lt=batch.confirmed_start_date).exists() or batch.draft_rows.filter(transaction_date__gt=batch.confirmed_end_date).exists()
    if outside and not (batch.extraction_metadata or {}).get('date_range_acknowledged'):
        messages.error(request, 'Confirm the date-range discrepancy before importing.')
        return redirect('bank:import_review', batch.pk)
    if batch.statement_warnings and not (batch.extraction_metadata or {}).get('statement_warnings_acknowledged'):
        messages.error(request, 'Review and explicitly acknowledge the statement balance and extraction warnings before importing.')
        return redirect('bank:import_review', batch.pk)
    approved_ids = list(batch.draft_rows.filter(review_status=ImportDraftRow.APPROVED).values_list('pk', flat=True))
    if not approved_ids:
        messages.error(request, 'Approve at least one row before import.')
        return redirect('bank:import_review', batch.pk)
    failures = []
    imported = duplicates = modified = verified = 0
    with transaction.atomic():
        batch = ImportBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.status == 'cancelled':
            messages.error(request, 'This draft was cancelled before import could start.')
            return redirect('bank:import_review', batch.pk)
        if batch.status == 'importing':
            messages.error(request, 'Another import operation is already in progress for this draft.')
            return redirect('bank:import_review', batch.pk)
        if batch.status in ('imported', 'completed'):
            messages.info(request, 'This batch has already been imported. Reopening it will not create duplicate transactions.')
            return redirect('bank:import_review', batch.pk)
        batch.status = 'importing'
        batch.save(update_fields=['status'])
        for draft_id in approved_ids:
            draft = ImportDraftRow.objects.select_for_update().select_related('existing_transaction').get(pk=draft_id)
            if draft.decision == 'match' and draft.existing_transaction_id:
                target = draft.existing_transaction
                if draft.classification == ImportDraftRow.MODIFY:
                    continue
                draft.actual_transaction = target
                result = 'verified'
                note = 'Existing bank transaction read back; statement fields match the reviewed draft.'
                duplicates += 1
            elif draft.decision == 'modify' and draft.existing_transaction_id:
                target = BankTransaction.objects.select_for_update().get(pk=draft.existing_transaction_id)
                if target.matches.filter(active=True).exists():
                    failures.append((draft.pk, 'Existing transaction has active accounting matches; modification was blocked.'))
                    continue
                draft.before_values = {'date': str(target.date), 'value_date': str(target.value_date or ''),
                                       'description': target.description, 'reference': target.reference,
                                       'debit': str(target.debit), 'credit': str(target.credit), 'balance': str(target.balance or ''),
                                       'row_hash': target.row_hash}
                target.date, target.value_date = draft.transaction_date, draft.value_date
                target.description, target.reference = draft.description[:500], draft.reference[:120]
                target.debit, target.credit, target.balance = draft.debit, draft.credit, draft.balance
                target.row_hash = S.row_hash(target.bank_account_id, target.date, target.credit - target.debit,
                                             target.reference, target.description, 0)
                try:
                    with transaction.atomic():
                        target.save(update_fields=['date', 'value_date', 'description', 'reference', 'debit', 'credit', 'balance', 'row_hash'])
                except IntegrityError:
                    failures.append((draft.pk, 'The proposed fields conflict with an existing unique transaction fingerprint.'))
                    continue
                target.refresh_from_db()
                modified += 1
                result = 'verified'
                note = 'Approved existing transaction updated and read back against the draft.'
            elif draft.decision == 'new':
                BankAccount.objects.select_for_update().get(pk=batch.bank_account_id)
                current_class, current_match, _, current_reason = importer.classify_draft_row(batch.bank_account, {
                    'transaction_date': draft.transaction_date, 'value_date': draft.value_date,
                    'description': draft.description, 'reference': draft.reference, 'debit': draft.debit,
                    'credit': draft.credit, 'balance': draft.balance, 'confidence': 100,
                })
                if current_class != ImportDraftRow.NEW and current_match and current_match.batch_id != batch.pk and draft.reviewed_at and current_match.created_at > draft.reviewed_at:
                    draft.classification, draft.existing_transaction = current_class, current_match
                    draft.justification = current_reason
                    draft.review_status, draft.decision = ImportDraftRow.PENDING, ''
                    draft.save(update_fields=['classification', 'existing_transaction', 'justification', 'review_status', 'decision'])
                    failures.append((draft.pk, 'A similar bank row appeared after approval; review the updated comparison before importing.'))
                    continue
                try:
                    with transaction.atomic():
                        target, created = S.add_transaction(batch.bank_account, draft.transaction_date, draft.description,
                            draft.debit, draft.credit, draft.reference, draft.value_date, draft.balance,
                            source='import', batch=batch, occurrence=draft.fingerprint_occurrence)
                except (ValidationError, IntegrityError) as exc:
                    failures.append((draft.pk, f'Could not create transaction: {_err(exc)}'))
                    continue
                target.refresh_from_db()
                if not created:
                    failures.append((draft.pk, 'A duplicate transaction was found at write time; no new row was created.'))
                    continue
                imported += 1
                result = 'verified'
                note = 'New bank transaction committed, read back, and compared with every approved draft field.'
            else:
                failures.append((draft.pk, 'No importable approval decision was saved for this row.'))
                continue
            differences = []
            expected = {'bank_account_id': batch.bank_account_id, 'date': draft.transaction_date,
                        'value_date': draft.value_date, 'description': draft.description[:500],
                        'reference': draft.reference[:120], 'debit': draft.debit, 'credit': draft.credit,
                        'balance': draft.balance, 'batch_id': batch.pk if draft.decision == 'new' else target.batch_id}
            target.refresh_from_db()
            for field, value in expected.items():
                if getattr(target, field) != value:
                    differences.append(field)
            if differences:
                failures.append((draft.pk, 'Read-back mismatch in: ' + ', '.join(differences)))
                if draft.decision == 'modify':
                    previous = draft.before_values
                    from datetime import date as date_type
                    target.date = date_type.fromisoformat(previous['date'])
                    target.value_date = date_type.fromisoformat(previous['value_date']) if previous.get('value_date') else None
                    target.description, target.reference = previous['description'], previous['reference']
                    target.debit, target.credit = q2(previous['debit']), q2(previous['credit'])
                    target.balance = q2(previous['balance']) if previous.get('balance') else None
                    target.row_hash = previous['row_hash']
                    target.save(update_fields=['date', 'value_date', 'description', 'reference', 'debit', 'credit', 'balance', 'row_hash'])
                    modified -= 1
                elif draft.decision == 'new':
                    target.delete()
                    imported -= 1
                else:
                    duplicates -= 1
                draft.actual_transaction = None
                draft.verification_status, draft.verification_note = 'failed', 'Read-back mismatch: ' + ', '.join(differences)
                draft.review_status = ImportDraftRow.FAILED
            else:
                draft.actual_transaction = target
                draft.verification_status, draft.verification_note = result, note
                draft.review_status = ImportDraftRow.VERIFIED
                verified += 1
            draft.verified_at = timezone.now()
            draft.save(update_fields=['actual_transaction', 'verification_status', 'verification_note', 'review_status', 'verified_at', 'before_values'])
        batch.imported += imported
        batch.duplicates += duplicates
        batch.modified += modified
        batch.verified += verified
        batch.errors += len(failures)
        batch.imported_at = timezone.now()
        batch.status = 'verification_failed' if failures else 'imported'
        batch.error_notes = '\n'.join(f'Row {row_id}: {note}' for row_id, note in failures)[:5000]
        batch.save(update_fields=['imported', 'duplicates', 'modified', 'verified', 'errors', 'imported_at', 'status', 'error_notes'])
    if failures:
        messages.error(request, f'Import finished with {len(failures)} row(s) needing correction; successful rows were verified and failures remain in this batch.')
    else:
        messages.success(request, f'Imported {imported} new transaction(s), verified {imported + duplicates} row(s), and applied {modified} approved modification(s). No accounting entries were created.')
    return redirect('bank:import_review', batch.pk)


def imports(request):
    qs = ImportBatch.objects.select_related('bank_account').annotate(
        open_draft_rows=Count('draft_rows', distinct=True, filter=Q(
            draft_rows__review_status__in=DRAFT_OPEN_STATUSES, draft_rows__actual_transaction__isnull=True)),
        locked_rows=Count('draft_rows', distinct=True, filter=Q(
            draft_rows__review_status__in=[ImportDraftRow.IMPORTED, ImportDraftRow.VERIFIED]) | Q(draft_rows__actual_transaction__isnull=False)),
        txn_count=Count('transactions', distinct=True), version_count=Count('versions', distinct=True))
    qs, _, _ = apply_queryset_sort(qs, request, {
        'when': 'created_at', 'account': 'bank_account__name', 'file': 'file_name', 'status': 'status',
        'rows': 'total_rows', 'imported': 'imported', 'duplicates': 'duplicates', 'errors': 'errors',
    })
    return render(request, 'bank_management/imports.html', {
        'rows': _with_delete_flags(qs[:200]),
        'accounts': BankAccount.objects.order_by('kind', 'name'),
    })


def manual_entry(request, pk):
    b = get_object_or_404(BankAccount, pk=pk)
    form = ManualEntryForm(request.POST or None, bank=b, initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        try:
            e = S.manual_entry(b, c['date'], c['direction'], c['amount'], c['counter_account'], c['narration'])
            messages.success(request, f'Posted {e.number}.')
            return redirect('bank:account_detail', b.pk)
        except ValidationError as e:
            messages.error(request, _err(e))
    return render(request, 'bank_management/manual_entry.html', {'b': b, 'form': form})


def transfers(request):
    form = TransferForm(request.POST or None, initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        try:
            t = S.transfer(c['from_account'], c['to_account'], c['amount'], c['date'], c['narration'], c['reference'])
            messages.success(request, f'Transfer posted as {t.entry.number}.')
            return redirect('bank:transfers')
        except ValidationError as e:
            messages.error(request, _err(e))
    qs = BankTransfer.objects.select_related('from_account', 'to_account', 'entry')
    qs, _, _ = apply_queryset_sort(qs, request, {
        'date': 'date', 'from': 'from_account__name', 'to': 'to_account__name', 'amount': 'amount',
        'reference': 'reference', 'journal': 'entry__number',
    })
    return render(request, 'bank_management/transfers.html', {'form': form, 'rows': qs[:200]})


def reconciliation(request, pk):
    b = get_object_or_404(BankAccount, pk=pk)
    ctx = _period_ctx(request, default='this_month')
    ctx.update(b=b, s=S.summary(b, ctx['f'].end, ctx['f'].start))
    return render(request, 'bank_management/reconciliation.html', ctx)


def book(request, kind):
    if kind not in ('cash', 'bank'):
        raise Http404
    ctx = _period_ctx(request, default='this_month')
    banks = BankAccount.objects.filter(kind=kind).select_related('ledger_account')
    sel = request.GET.get('account')
    if sel:
        banks = banks.filter(pk=sel)
    books = [{'b': b, 'data': A.ledger(b.ledger_account, ctx['f'].start, ctx['f'].end)} for b in banks]
    ctx.update(kind=kind, books=books, all_accounts=BankAccount.objects.filter(kind=kind), sel=sel)
    return render(request, 'bank_management/book.html', ctx)


def legacy_preview(request):
    return render(request, 'bank_management/legacy_preview.html', {'rows': S.legacy_bank_text_preview()})


# ------------------------------------------------------------- draft rows: list and edit
# A draft row stays editable until it has been written to, or verified against, the bank register.
DRAFT_OPEN_STATUSES = (ImportDraftRow.PENDING, ImportDraftRow.APPROVED, ImportDraftRow.EXCLUDED, ImportDraftRow.FAILED)
DRAFT_EDIT_FIELDS = ('transaction_date', 'value_date', 'description', 'reference', 'debit', 'credit', 'balance')
_CHECKED_WARNING_PREFIXES = (
    'One or more transactions are before the confirmed statement start date.',
    'One or more transactions are after the confirmed statement end date.',
    'Statement arithmetic differs:',
    'Running balance does not reconcile between draft rows',
)


def _open_draft_rows(batch):
    """Rows of a batch that are still drafts (not imported/verified and not linked to a bank transaction)."""
    return batch.draft_rows.filter(review_status__in=DRAFT_OPEN_STATUSES, actual_transaction__isnull=True)


def _draft_row_is_open(row):
    return row.review_status in DRAFT_OPEN_STATUSES and not row.actual_transaction_id


def _draft_edit_block_reason(batch, row=None):
    """Why draft rows of this batch (or this particular row) cannot be edited; '' when editing is allowed."""
    if batch.status == 'cancelled':
        return 'This draft is cancelled. Regenerate a new version from its retained upload to edit rows again.'
    if batch.status == 'importing':
        return 'Import is in progress. Wait for it to finish before editing draft rows.'
    if batch.status in ('imported', 'completed'):
        return ('This batch has been imported, so its rows can no longer be edited as drafts. '
                'Use the bank transaction correction workflow, or regenerate a new draft version.')
    if batch.status == ImportBatch.PENDING:
        return 'Map the statement columns and create the review draft before editing rows.'
    if row is not None and not _draft_row_is_open(row):
        return (f'Row {row.row_number} has already been imported or verified against the bank register '
                'and cannot be edited as a draft.')
    return ''


def _draft_fingerprint(transaction_date, debit, credit, reference, description):
    """Same in-statement duplicate fingerprint that importer.create_draft_rows uses."""
    return (transaction_date, q2(debit or 0), q2(credit or 0), (reference or '').strip().casefold(), importer._norm(description))


def _row_fingerprint(row):
    return _draft_fingerprint(row.transaction_date, row.debit, row.credit, row.reference, row.description)


def _refresh_statement_checks(batch):
    """Recompute the warnings that _check_statement_totals derives from draft rows after a row edit.

    Extractor warnings are kept; derived period/arithmetic/running-balance warnings are recalculated so a
    corrected row clears a stale warning. If an edit introduces a new warning, any earlier acknowledgement
    is withdrawn so the existing import guard asks for the warnings to be reviewed again.
    """
    before = list(batch.statement_warnings or [])
    batch.statement_warnings = [w for w in before if not str(w).startswith(_CHECKED_WARNING_PREFIXES)]
    _check_statement_totals(batch)
    update = ['statement_warnings']
    if set(batch.statement_warnings) - set(before):
        metadata = dict(batch.extraction_metadata or {})
        if metadata.get('statement_warnings_acknowledged'):
            metadata['statement_warnings_acknowledged'] = False
            batch.extraction_metadata = metadata
            update.append('extraction_metadata')
    batch.save(update_fields=update)


def _apply_draft_row_edit(batch, row, data):
    """Save edited statement fields on an open draft row and refresh every value derived from them.

    Mirrors the inline 'save_row' review action: the row is re-compared with the bank register and returns
    to pending review. No bank transaction or accounting record is touched.
    """
    old_fingerprint = _row_fingerprint(row)
    before = {field: getattr(row, field) for field in DRAFT_EDIT_FIELDS}
    row.transaction_date = data.get('transaction_date')
    row.value_date = data.get('value_date')
    row.description = (data.get('description') or '').strip()[:500]
    row.reference = (data.get('reference') or '').strip()[:120]
    row.debit = q2(data.get('debit') or 0)
    row.credit = q2(data.get('credit') or 0)
    row.balance = q2(data['balance']) if data.get('balance') is not None else None
    row.normalized_description = importer._norm(row.description)[:500]
    classification, existing, changes, justification = importer.classify_draft_row(batch.bank_account, {
        'transaction_date': row.transaction_date, 'value_date': row.value_date, 'description': row.description,
        'reference': row.reference, 'debit': row.debit, 'credit': row.credit, 'balance': row.balance,
        'confidence': 100, 'extraction_error': '',
    })
    new_fingerprint = _row_fingerprint(row)
    if new_fingerprint != old_fingerprint:
        twins = [other for other in batch.draft_rows.exclude(pk=row.pk) if _row_fingerprint(other) == new_fingerprint]
        row.fingerprint_occurrence = max(other.fingerprint_occurrence for other in twins) + 1 if twins else 0
    if row.fingerprint_occurrence and classification == ImportDraftRow.NEW:
        classification = ImportDraftRow.POSSIBLE
        justification = ('An identical row (date, amount, reference and narration) appears elsewhere in this statement. '
                         'Confirm that it is a separate bank transaction before importing.')
    row.classification, row.existing_transaction, row.proposed_changes, row.justification = (
        classification, existing, changes, justification)
    row.extraction_error = ''
    row.review_status, row.decision, row.reviewed_at = ImportDraftRow.PENDING, '', None
    if row.verification_status == 'failed':
        row.verification_status, row.verification_note = 'pending', ''
    row.save()
    _refresh_statement_checks(batch)
    _refresh_batch_counts(batch)
    _set_batch_review_status(batch)
    return [field for field in DRAFT_EDIT_FIELDS if before[field] != getattr(row, field)]


def import_draft_rows(request, pk):
    """List the draft rows created by a statement import, with filters and an edit link per open row."""
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    if batch.status == ImportBatch.PENDING and not batch.draft_rows.exists():
        messages.info(request, 'This upload has no draft rows yet. Map the columns and create the review draft first.')
        return redirect('bank:import_batch', batch.pk)
    show = request.GET.get('show') if request.GET.get('show') in ('draft', 'all') else 'draft'
    status = request.GET.get('status', '')
    classification = request.GET.get('classification', '')
    search = (request.GET.get('q') or '').strip()
    open_rows = _open_draft_rows(batch)
    rows = open_rows if show == 'draft' else batch.draft_rows.all()
    if status in dict(ImportDraftRow.REVIEW_STATUSES):
        rows = rows.filter(review_status=status)
    else:
        status = ''
    if classification in dict(ImportDraftRow.CLASSIFICATIONS):
        rows = rows.filter(classification=classification)
    else:
        classification = ''
    if search:
        rows = rows.filter(Q(description__icontains=search) | Q(reference__icontains=search))
    rows = rows.select_related('existing_transaction').order_by('row_number', 'id')
    totals = rows.aggregate(debits=Sum('debit'), credits=Sum('credit'))
    block_reason = _draft_edit_block_reason(batch)
    return render(request, 'bank_management/import_draft_rows.html', {
        'batch': batch, 'rows': rows, 'show': show, 'status': status, 'classification': classification, 'q': search,
        'open_count': open_rows.count(), 'all_count': batch.draft_rows.count(), 'listed_count': rows.count(),
        'listed_debits': q2(totals['debits'] or 0), 'listed_credits': q2(totals['credits'] or 0),
        'review_statuses': ImportDraftRow.REVIEW_STATUSES, 'classifications': ImportDraftRow.CLASSIFICATIONS,
        'open_statuses': DRAFT_OPEN_STATUSES, 'can_edit': not block_reason, 'block_reason': block_reason,
    })


def import_draft_row_edit(request, pk, row_pk):
    """Edit one open draft row; imported/verified rows and closed batches are rejected."""
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    row = get_object_or_404(batch.draft_rows.select_related('existing_transaction'), pk=row_pk)
    list_url = reverse('bank:import_draft_rows', args=[batch.pk])
    reason = _draft_edit_block_reason(batch, row)
    if reason:
        messages.error(request, reason)
        return redirect(list_url)
    form = DraftRowEditForm(request.POST or None, instance=row)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked_batch = ImportBatch.objects.select_for_update().select_related('bank_account').get(pk=batch.pk)
                locked_row = ImportDraftRow.objects.select_for_update().get(pk=row.pk, batch=locked_batch)
                reason = _draft_edit_block_reason(locked_batch, locked_row)
                if reason:
                    raise ValueError(reason)
                changed = _apply_draft_row_edit(locked_batch, locked_row, form.cleaned_data)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(list_url)
        labels = [form.fields[field].label for field in changed]
        messages.success(request, (f'Draft row {locked_row.row_number} saved' +
                                   (f' ({", ".join(labels)} changed)' if labels else ' (no field changed)') +
                                   f'. Comparison refreshed: {locked_row.get_classification_display()}. '
                                   'The row is back to pending review; no bank or accounting record was changed.'))
        return redirect(f'{list_url}#draft-row-{locked_row.pk}')
    open_ids = list(_open_draft_rows(batch).order_by('row_number', 'id').values_list('pk', flat=True))
    position = open_ids.index(row.pk) if row.pk in open_ids else None
    return render(request, 'bank_management/import_draft_row_edit.html', {
        'batch': batch, 'row': row, 'form': form,
        'previous_id': open_ids[position - 1] if position else None,
        'next_id': open_ids[position + 1] if position is not None and position + 1 < len(open_ids) else None,
    })


# ------------------------------------------------------------- delete an unimported draft batch
def _delete_block_reason(batch):
    """Why this import batch cannot be deleted as a draft; '' when deletion is allowed.

    Deletion is only for drafts that never reached the bank register. Annotated counts from the imports list
    (locked_rows, txn_count, version_count) are used when present to avoid per-row queries.
    """
    if batch.status == 'importing':
        return 'Import is in progress. Wait for it to finish before deleting this draft.'
    locked = getattr(batch, 'locked_rows', None)
    if locked is None:
        locked = batch.draft_rows.filter(Q(review_status__in=[ImportDraftRow.IMPORTED, ImportDraftRow.VERIFIED]) |
                                         Q(actual_transaction__isnull=False)).count()
    txns = getattr(batch, 'txn_count', None)
    if txns is None:
        txns = batch.transactions.count()
    if batch.imported or batch.verified or batch.modified or batch.duplicates or locked or txns:
        return ('Rows from this batch have already been imported into or verified against the bank register, so it '
                'cannot be deleted. Use the bank transaction correction or reversal workflow.')
    if batch.status in ('imported', 'completed', 'verification_failed'):
        return 'This batch has entered the import workflow and cannot be deleted as a draft.'
    versions = getattr(batch, 'version_count', None)
    if versions is None:
        versions = batch.versions.count()
    if versions:
        return (f'This is the original upload for {versions} regenerated draft version(s), which share its file. '
                'Delete those versions first.')
    return ''


def _with_delete_flags(batches):
    rows = list(batches)
    for batch in rows:
        batch.delete_block_reason = _delete_block_reason(batch)
    return rows


def _remove_unreferenced_import_files(files):
    """Best-effort removal of statement files no remaining import batch points to."""
    for storage, name in files:
        if ImportBatch.objects.filter(Q(original_file=name) | Q(generated_csv=name)).exists():
            continue
        try:
            if storage.exists(name):
                storage.delete(name)
        except Exception:  # noqa: BLE001 - a leftover file must never undo a committed delete
            pass


@require_POST
def import_delete(request, pk):
    """Permanently delete an unimported draft batch with its draft rows and draft-only files."""
    batch = get_object_or_404(ImportBatch, pk=pk)
    reason = (request.POST.get('delete_reason') or '').strip()
    from_review = request.POST.get('return_to') == 'review'
    try:
        with transaction.atomic():
            locked = ImportBatch.objects.select_for_update().get(pk=batch.pk)
            block = _delete_block_reason(locked)
            if block:
                raise ValueError(block)
            if not reason:
                raise ValueError('Enter a reason for deleting this draft so the audit log explains why it was removed.')
            label = f'batch #{locked.pk}, Draft Version {locked.version_number} ({locked.file_name})'
            row_count = locked.draft_rows.count()
            files = [(f.storage, f.name) for f in (locked.original_file, locked.generated_csv) if f and f.name]
            from audit.recorder import log_manual
            log_manual('bank_management', 'ImportDraftRow', locked.pk, f'Draft rows of import {label}', 'delete',
                       reason, {'draft_rows': [row_count, 0], 'import_batch': [locked.pk, None]})
            locked.delete()  # draft rows cascade; the ImportBatch delete itself is audited by the audit app
            transaction.on_commit(lambda: _remove_unreferenced_import_files(files))
    except (ValueError, ProtectedError) as exc:
        messages.error(request, str(exc) if isinstance(exc, ValueError) else
                       'This draft is still referenced by other records and was not deleted.')
        return redirect('bank:import_review', batch.pk) if from_review else redirect('bank:imports')
    messages.success(request, f'Deleted draft {label} and its {row_count} draft row(s). '
                              'No bank transaction or accounting record was changed.')
    return redirect('bank:imports')
