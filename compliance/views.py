from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounting.postings import PostingError
from employee_management.security import local_only

from . import checks, registers
from . import services as S
from .forms import DueDateForm, PaymentForm, ReasonForm, RuleForm
from .models import VERIFY_WARNING, DueDateEntry, GstInputClaim, StatutoryPayment, StatutoryRule


def _range(request):
    if request.GET.get('period_mode'):
        from reporting import periods as report_periods
        period = report_periods.resolve(request.GET)
        return period.start, period.end
    today = timezone.localdate()
    fy = today.year if today.month >= 4 else today.year - 1

    def parse(value, fallback):
        try:
            return date.fromisoformat(value) if value else fallback
        except ValueError:
            return fallback

    start = parse(request.GET.get('start'), date(fy, 4, 1))
    end = parse(request.GET.get('end'), date(fy + 1, 3, 31))
    if end < start:
        start, end = end, start
    return start, end


def _render(request, tpl, **ctx):
    ctx.setdefault('warning', VERIFY_WARNING)
    return render(request, f'compliance/{tpl}', ctx)


def _err(request, ex):
    messages.error(request, '; '.join(ex.messages) if isinstance(ex, ValidationError) else str(ex))


@local_only
def home(request):
    today = timezone.localdate()
    return _render(request, 'home.html', rules=StatutoryRule.objects.all(),
                   active_count=StatutoryRule.objects.filter(active=True).count(),
                   example_count=StatutoryRule.objects.filter(is_example=True).count(),
                   due=S.due_calendar(date(today.year, today.month, 1), 2)[:12])


# --------------------------------------------------------------------------- rules
@local_only
def rules(request):
    kind = request.GET.get('kind', '')
    qs = StatutoryRule.objects.all()
    if kind:
        qs = qs.filter(kind=kind)
    return _render(request, 'rules.html', rules=qs, kinds=StatutoryRule.KINDS, kind=kind)


@local_only
def rule_edit(request, pk=None):
    obj = get_object_or_404(StatutoryRule, pk=pk) if pk else None
    form = RuleForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        try:
            form.save()
        except ValidationError as ex:
            for f, errs in ex.message_dict.items():
                form.add_error(f if f in form.fields else None, errs)
        else:
            messages.success(request, 'Rule saved. Make sure it has been verified by you or your CA.')
            return redirect('compliance:rules')
    return _render(request, 'rule_form.html', form=form, obj=obj, help=StatutoryRule.PARAM_HELP,
                   key_labels=StatutoryRule.KEY_LABEL, kinds=dict(StatutoryRule.KINDS))


# --------------------------------------------------------------------------- check page (read-only)
@local_only
def check(request):
    start, end = _range(request)
    which = request.GET.getlist('which') if request.GET.get('filters') else ['invoices', 'payslips']
    status = request.GET.get('status', '')
    rows, summary = checks.run(start, end, which)
    if status:
        rows = [r for r in rows if r['status'] == status]
    return _render(request, 'check.html', rows=rows, summary=summary, start=start, end=end, which=which,
                   status=status, labels=checks.LABELS)


# --------------------------------------------------------------------------- registers
@local_only
def gst_output(request):
    start, end = _range(request)
    rows, tot = registers.output_gst(start, end)
    return _render(request, 'gst_output.html', rows=rows, tot=tot, start=start, end=end)


@local_only
def gst_input(request):
    start, end = _range(request)
    status = request.GET.get('status', '')
    rows, tot = registers.input_gst(start, end, status)
    return _render(request, 'gst_input.html', rows=rows, tot=tot, start=start, end=end, status=status,
                   statuses=GstInputClaim.STATUSES)


@local_only
@require_POST
def gst_input_toggle(request, pk, action):
    from expense_management.models import CompanyExpense
    if action not in ('mark', 'unmark'):
        raise Http404
    e = get_object_or_404(CompanyExpense, pk=pk)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'A reason is required.')
    else:
        try:
            if action == 'mark':
                S.mark_eligible(e, form.cleaned_data['reason'])
                messages.success(request, f'{e.expense_id}: input GST marked eligible and reclassified.')
            else:
                S.unmark(e, form.cleaned_data['reason'])
                messages.success(request, f'{e.expense_id}: input GST unmarked; reclass reversed.')
        except (ValidationError, PostingError) as ex:
            _err(request, ex)
    target = request.POST.get('next')
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect('compliance:gst_input')


@local_only
def tds(request):
    start, end = _range(request)
    return _render(request, 'tds.html', r=registers.tds_register(start, end), start=start, end=end)


@local_only
def payroll_stat(request):
    start, end = _range(request)
    rows, heads, ledger = registers.payroll_statutory(start, end)
    return _render(request, 'payroll_stat.html', rows=rows, heads=heads, ledger=ledger, start=start, end=end)


# --------------------------------------------------------------------------- statutory payments
@local_only
def payments(request):
    today = timezone.localdate()
    form = PaymentForm(request.POST or None, initial={'date': today, 'period_year': today.year})
    if request.method == 'POST' and form.is_valid():
        try:
            S.record_payment(form.save(commit=False))
        except Exception as ex:
            _err(request, ex)
        else:
            messages.success(request, 'Statutory payment recorded and posted (Dr payable / Cr bank).')
            return redirect('compliance:payments')
    return _render(request, 'payments.html', form=form,
                   payments=StatutoryPayment.objects.select_related('bank_account'))


@local_only
@require_POST
def payment_cancel(request, pk):
    p = get_object_or_404(StatutoryPayment, pk=pk, cancelled=False)
    try:
        S.cancel_payment(p, request.POST.get('reason'))
        messages.success(request, 'Payment cancelled and its posting reversed.')
    except Exception as ex:
        _err(request, ex)
    return redirect('compliance:payments')


# --------------------------------------------------------------------------- due dates
@local_only
def due_dates(request, pk=None):
    obj = get_object_or_404(DueDateEntry, pk=pk) if pk else None
    form = DueDateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        e = form.save(commit=False)
        if e.is_example and form.changed_data:
            e.is_example = False
        e.save()
        messages.success(request, 'Due date saved.')
        return redirect('compliance:due_dates')
    today = timezone.localdate()
    return _render(request, 'due_dates.html', form=form, obj=obj, entries=DueDateEntry.objects.all(),
                   cal=S.due_calendar(date(today.year, today.month, 1), 3))
