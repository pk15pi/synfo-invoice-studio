from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounting import services as A
from accounting.models import JournalLine
from accounting.money import q2
from reporting import periods as rp

from . import importer, services as S
from .forms import BankAccountForm, ManualEntryForm, TransactionForm, TransferForm, UploadForm
from .models import BankAccount, BankTransaction, BankTransfer, ImportBatch, ReconciliationMatch


def _err(e):
    return '; '.join(e.messages) if isinstance(e, ValidationError) else str(e)


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
    ctx.update(b=b, form=form, rows=qs.order_by('-date', '-id')[:1000], status=st, statuses=BankTransaction.STATUSES)
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
        try:
            rows = importer.read_rows(up)
        except Exception as e:  # noqa: BLE001 - surface any parse failure to the user
            messages.error(request, f'Could not read file: {e}')
        else:
            batch = ImportBatch.objects.create(bank_account=b, file_name=up.name[:200], raw_rows=rows,
                                               mapping=importer.guess_mapping(rows[0]))
            return redirect('bank:import_batch', batch.pk)
    return render(request, 'bank_management/import_upload.html', {'b': b, 'form': form})


def import_batch(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related('bank_account'), pk=pk)
    if batch.status != ImportBatch.PENDING:
        return render(request, 'bank_management/import_done.html', {'batch': batch})
    mapping = dict(batch.mapping)
    if request.method == 'POST':
        mapping = {k: request.POST.get(k, '') for k, _, _ in importer.FIELDS}
        mapping['header_row'] = request.POST.get('header_row') or '1'
    rows = None
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'confirm':
                batch = importer.confirm(batch, mapping)
                messages.success(request, f'Imported {batch.imported}, skipped {batch.duplicates} duplicate(s), {batch.errors} error row(s).')
                return redirect('bank:import_batch', batch.pk)
            rows = importer.preview(batch, mapping)
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


def imports(request):
    return render(request, 'bank_management/imports.html', {'rows': ImportBatch.objects.select_related('bank_account')[:200]})


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
    return render(request, 'bank_management/transfers.html', {
        'form': form, 'rows': BankTransfer.objects.select_related('from_account', 'to_account', 'entry')[:200]})


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
