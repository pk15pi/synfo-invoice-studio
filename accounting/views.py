import calendar
from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Max, Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from reporting import periods as rp

from . import services as S
from .forms import AccountForm
from .models import Account, AccountMapping, FinancialPeriod, FinancialYear, JournalEntry, ImmutableError
from .money import q2

MAX_LINES = 10


def _month_choices(params):
    bounds = JournalEntry.objects.aggregate(first=Min('date'), last=Max('date'))
    dates = [value for value in bounds.values() if value]
    selected = (params.get('month') or '').strip()
    try:
        if selected:
            dates.append(date.fromisoformat(f'{selected}-01'))
    except ValueError:
        pass
    first = min(dates, default=rp.today()).replace(day=1)
    last_date = max([rp.today(), *dates])
    last = date(last_date.year + 1, last_date.month, 1)
    year, month = first.year, first.month
    choices = []
    while (year, month) <= (last.year, last.month):
        choices.append((f'{year:04d}-{month:02d}', f'{calendar.month_name[month]} {year}'))
        year, month = rp.add_months(year, month, 1)
    return choices


def _err(e):
    return '; '.join(e.messages) if isinstance(e, ValidationError) else str(e)


def home(request):
    return render(request, 'accounting/home.html', {
        'n_accounts': Account.objects.count(), 'n_posted': JournalEntry.objects.exclude(status='draft').count(),
        'n_drafts': JournalEntry.objects.filter(status='draft').count(),
        'current': S.period_for(timezone.localdate()), 'n_unmapped': AccountMapping.objects.filter(account=None).count()})


def accounts(request):
    rows = list(Account.objects.select_related('parent'))
    used = set(Account.objects.filter(lines__isnull=False).values_list('pk', flat=True).distinct())
    for a in rows:
        a.used = a.pk in used
        a.depth = 0
        p = a.parent
        while p is not None and a.depth < 6:
            a.depth += 1
            p = next((x.parent for x in rows if x.pk == p.pk), None)
        a.indent = a.depth * 18
    return render(request, 'accounting/accounts.html', {'rows': rows, 'types': Account.TYPES})


def account_form(request, pk=None):
    obj = get_object_or_404(Account, pk=pk) if pk else None
    form = AccountForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        try:
            a = form.save()
        except ValidationError as e:
            messages.error(request, _err(e))
        else:
            messages.success(request, f'Saved account {a}.')
            return redirect('acc:accounts')
    return render(request, 'accounting/account_form.html', {'form': form, 'obj': obj})


def _line_rows(post=None):
    rows = []
    for i in range(MAX_LINES):
        g = (lambda k: (post.get(f'{k}_{i}') or '').strip()) if post else (lambda k: '')
        rows.append({'i': i, 'account': g('account'), 'debit': g('debit'), 'credit': g('credit'), 'description': g('description')})
    return rows


def journal_new(request):
    accts = Account.objects.filter(active=True, is_group=False)
    rows = _line_rows(request.POST if request.method == 'POST' else None)
    ctx = {'accounts': accts, 'rows': rows, 'date': request.POST.get('date') or timezone.localdate().isoformat(),
           'narration': request.POST.get('narration', '')}
    if request.method == 'POST':
        try:
            d = date.fromisoformat(ctx['date'])
            narr = ctx['narration'].strip()
            if not narr:
                raise ValidationError('Narration is required.')
            lines = [{'account': int(r['account']), 'debit': r['debit'] or 0, 'credit': r['credit'] or 0,
                      'description': r['description']} for r in rows if r['account'] or r['debit'] or r['credit']]
            if any(not l['account'] for l in lines):
                raise ValidationError('Every line with an amount needs an account.')
            if request.POST.get('action') == 'post':
                e = S.create_and_post(d, narr, lines)
                messages.success(request, f'Posted {e.number}.')
            else:
                e = S.create_draft(d, narr, lines)
                messages.success(request, 'Saved as draft.')
            return redirect('acc:journal_detail', e.pk)
        except (ValidationError, ValueError, Account.DoesNotExist) as e:
            messages.error(request, _err(e))
    return render(request, 'accounting/journal_form.html', ctx)


def journal_list(request):
    qs = JournalEntry.objects.annotate(amount=Sum('lines__debit'))
    f = rp.resolve(request.GET, default='current_fy')
    qs = qs.filter(date__gte=f.start, date__lte=f.end)
    st, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if st:
        qs = qs.filter(status=st)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(narration__icontains=q) | Q(source_key__icontains=q))
    page = Paginator(qs.order_by('-date', '-id'), 100).get_page(request.GET.get('page'))
    return render(request, 'accounting/journal_list.html', {'page': page, 'f': f, 'status': st, 'q': q,
                  'statuses': JournalEntry.STATUSES, 'params': request.GET, 'periods': rp.OPTIONS, 'fy_list': rp.fy_choices(),
                  'month_choices': _month_choices(request.GET)})


def journal_detail(request, pk):
    e = get_object_or_404(JournalEntry, pk=pk)
    lines = list(e.lines.select_related('account'))
    return render(request, 'accounting/journal_detail.html', {
        'e': e, 'lines': lines, 'dr': q2(sum(l.debit for l in lines)), 'cr': q2(sum(l.credit for l in lines)),
        'today': timezone.localdate().isoformat(), 'reversal': getattr(e, 'reversed_by', None) if e.pk else None})


@require_POST
def journal_post(request, pk):
    e = get_object_or_404(JournalEntry, pk=pk)
    try:
        e = S.post(e)
        messages.success(request, f'Posted {e.number}.')
    except ValidationError as ex:
        messages.error(request, _err(ex))
    return redirect('acc:journal_detail', pk)


@require_POST
def journal_reverse(request, pk):
    e = get_object_or_404(JournalEntry, pk=pk)
    try:
        d = date.fromisoformat(request.POST.get('date') or timezone.localdate().isoformat())
        rev = S.reverse(e, request.POST.get('reason', ''), d)
        messages.success(request, f'Reversed by {rev.number}.')
        return redirect('acc:journal_detail', rev.pk)
    except (ValidationError, ValueError) as ex:
        messages.error(request, _err(ex))
    return redirect('acc:journal_detail', pk)


@require_POST
def journal_delete(request, pk):
    e = get_object_or_404(JournalEntry, pk=pk)
    try:
        e.delete()
        messages.success(request, 'Draft deleted.')
        return redirect('acc:journal')
    except ImmutableError as ex:
        messages.error(request, _err(ex))
    return redirect('acc:journal_detail', pk)


def ledger_view(request, pk=None):
    pk = pk or request.GET.get('account')
    acct = get_object_or_404(Account, pk=pk) if pk else None
    f = rp.resolve(request.GET, default='current_fy')
    data = S.ledger(acct, f.start, f.end) if acct and not acct.is_group else None
    return render(request, 'accounting/ledger.html', {
        'acct': acct, 'data': data, 'f': f, 'accounts': Account.objects.filter(is_group=False),
        'periods': rp.OPTIONS, 'fy_list': rp.fy_choices(), 'fy_parts': rp.FY_PARTS, 'params': request.GET,
        'month_choices': _month_choices(request.GET)})


def periods(request):
    if request.method == 'POST':
        try:
            y = int(request.POST.get('start_year'))
            S.ensure_financial_year(y)
            messages.success(request, f'{rp.fy_label(y)} periods ready.')
        except (TypeError, ValueError):
            messages.error(request, 'Choose a financial year.')
        return redirect('acc:periods')
    S.period_for(timezone.localdate())  # current FY always exists
    return render(request, 'accounting/periods.html', {
        'years': FinancialYear.objects.prefetch_related('periods__events'), 'fy_list': rp.fy_choices(back=5, forward=1)})


@require_POST
def period_action(request, pk, action):
    p = get_object_or_404(FinancialPeriod, pk=pk)
    try:
        if action == 'close':
            S.close_period(p, request.POST.get('reason'))
        elif action == 'reopen':
            S.reopen_period(p, request.POST.get('reason'))
        else:
            raise ValidationError('Unknown action.')
        messages.success(request, f'{p.label} {action}d.' if action == 'close' else f'{p.label} reopened.')
    except ValidationError as e:
        messages.error(request, _err(e))
    return redirect('acc:periods')


def mappings(request):
    rows = AccountMapping.objects.select_related('account')
    accts = Account.objects.filter(is_group=False, active=True)
    if request.method == 'POST':
        valid = {str(a.pk): a for a in Account.objects.filter(is_group=False)}
        n = 0
        for m in rows:
            v = request.POST.get(f'm_{m.pk}', '')
            new = valid.get(v) if v else None
            if v and new is None:
                messages.error(request, f'{m.key}: invalid account.')
                continue
            if (new.pk if new else None) != m.account_id:
                m.account = new
                m.save()
                n += 1
        messages.success(request, f'{n} mapping(s) updated.')
        return redirect('acc:mappings')
    return render(request, 'accounting/mappings.html', {'rows': rows, 'accounts': accts})
