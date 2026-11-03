"""Salary advances & loans screens (Stage 6)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounting.money import ZERO, q2

from . import advances as svc
from .forms import AdvanceReasonForm, AdvanceRepaymentForm, EmployeeAdvanceForm
from .models import Employee, EmployeeAdvance

T = 'employee_management/'


def advances(request):
    qs = EmployeeAdvance.objects.select_related('employee').order_by('-date', '-id')
    st, emp = request.GET.get('status') or '', request.GET.get('employee') or ''
    if st:
        qs = qs.filter(status=st)
    if emp.isdigit():
        qs = qs.filter(employee_id=int(emp))
    rows = [(a, q2(a.outstanding())) for a in qs[:500]]
    return render(request, T + 'advances.html', {
        'rows': rows, 'statuses': EmployeeAdvance.STATUSES, 'st': st, 'emp': emp,
        'employees': Employee.objects.order_by('employee_id'),
        'total': q2(sum((o for a, o in rows if a.status == a.ACTIVE), ZERO))})


def advance_new(request):
    today = timezone.localdate()
    nxt = (today.year + (today.month // 12), today.month % 12 + 1)
    initial = {'start_year': nxt[0], 'start_month': nxt[1], 'date': today}
    if request.GET.get('employee', '').isdigit():
        initial['employee'] = int(request.GET['employee'])
    form = EmployeeAdvanceForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            a = svc.disburse(form.save(commit=False))
        except ValidationError as ex:
            form.add_error(None, '; '.join(ex.messages))
        else:
            messages.success(request, f'{a} recorded and posted (Dr Employee Advances / Cr bank).')
            return redirect('em:advance_detail', a.pk)
    return render(request, T + 'advance_form.html', {'form': form, 'back_url': reverse('em:advances')})


def advance_detail(request, pk):
    a = get_object_or_404(EmployeeAdvance.objects.select_related('employee', 'bank_account'), pk=pk)
    return render(request, T + 'advance_detail.html', {
        'a': a, 'outstanding': q2(a.outstanding()), 'rows': svc.statement(a),
        'schedule': svc.projected_schedule(a), 'drafts': svc.pending_draft_recoveries(a),
        'repayments': a.repayments.all(), 'repay_form': AdvanceRepaymentForm(initial={'date': timezone.localdate()}),
        'reason_form': AdvanceReasonForm(), 'can_cancel': a.status == a.ACTIVE and not svc.has_recovery(a)})


@require_POST
def advance_action(request, pk, action):
    a = get_object_or_404(EmployeeAdvance, pk=pk)
    try:
        if action == 'repay':
            f = AdvanceRepaymentForm(request.POST)
            if not f.is_valid():
                raise ValidationError([f'{k}: {", ".join(v)}' for k, v in f.errors.items()])
            d = f.cleaned_data
            svc.repay(a, d['amount'], d['date'], d['bank_account'], d['reference'])
            messages.success(request, 'Repayment recorded (Dr bank / Cr Employee Advances).')
        elif action == 'write_off':
            svc.write_off(a, request.POST.get('reason'))
            messages.success(request, 'Outstanding balance written off.')
        elif action == 'cancel':
            svc.cancel(a, request.POST.get('reason'))
            messages.success(request, 'Advance cancelled; disbursement entry reversed.')
        else:
            messages.error(request, 'Unknown action.')
    except ValidationError as ex:
        messages.error(request, '; '.join(ex.messages))
    return redirect('em:advance_detail', a.pk)


def employee_ledger(request, pk):
    """All advances of one employee plus the party-tagged Employee Advances ledger lines."""
    from accounting.models import JournalLine
    from accounting.postings import m
    from accounting.services import BOOKED
    e = get_object_or_404(Employee, pk=pk)
    advs = [(a, q2(a.outstanding())) for a in e.advances.all()]
    acct = m('payroll.advance')
    lines = (JournalLine.objects.filter(account=acct, party_type='employee', party_id=str(e.pk),
                                     entry__status__in=BOOKED)
             .select_related('entry').order_by('entry__date', 'entry_id', 'id'))
    bal, rows = ZERO, []
    for ln in lines:
        bal += ln.debit - ln.credit
        rows.append({'date': ln.entry.date, 'entry': ln.entry, 'description': ln.description, 'debit': ln.debit,
                     'credit': ln.credit, 'balance': q2(bal)})
    return render(request, T + 'advance_ledger.html', {'e': e, 'advs': advs, 'rows': rows, 'account': acct,
                                                      'balance': q2(bal)})
