"""Expense Management views. Every view is wrapped by employee_management's local_only guard
(no auth in this app by design: keep it bound to 127.0.0.1)."""
import calendar
from datetime import date

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Min, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from employee_management.models import Department, Employee
from employee_management.security import local_only, private_file_response
from invoice_app.models import BusinessProfile, Client, ProjectTemplate
from invoice_app.table_sorting import apply_queryset_sort, sort_table_values

from . import exports, reports, services
from .forms import (CLAIM_DOC_TYPES, COMPANY_DOC_TYPES, CategoryForm, ClaimForm, CompanyExpenseForm, PaymentForm,
                    QuickVendorForm, ReimbursementForm, SubcategoryForm, VendorForm)
from .fy import PERIODS, fy_choices, months_between, today
from .models import (CompanyExpense, CompanyExpenseAttachment, EmployeeExpenseAttachment, EmployeeExpenseClaim,
                     ExpenseCategory, ExpenseSubcategory, Vendor)

C = EmployeeExpenseClaim


def _errs(e):
    if hasattr(e, 'message_dict'):
        return '; '.join(f'{"" if k == "__all__" else k.replace("_", " ") + ": "}{" ".join(v)}'
                         for k, v in e.message_dict.items())
    return ' '.join(e.messages)


def _filter_ctx(request, f):
    date_bounds = [
        CompanyExpense.objects.aggregate(first=Min('expense_date'), last=Max('expense_date')),
        C.objects.aggregate(first=Min('expense_date'), last=Max('expense_date')),
    ]
    recorded_dates = [value for bounds in date_bounds for value in bounds.values() if value]
    selected_month = request.GET.get('month', '').strip()
    try:
        if selected_month:
            recorded_dates.append(date.fromisoformat(f'{selected_month}-01'))
    except ValueError:
        pass
    first_date = min(recorded_dates, default=today())
    last_date = max([today(), *recorded_dates])
    first_month = first_date.replace(day=1)
    last_month = date(last_date.year + 1, last_date.month, 1)
    month_choices = [
        (f'{year:04d}-{month:02d}', f'{calendar.month_name[month]} {year}')
        for year, month in months_between(first_month, last_month)
    ]
    return dict(f=f, periods=PERIODS, fy_list=fy_choices(), params=request.GET,
                month_choices=month_choices,
                categories=ExpenseCategory.objects.all(), vendors_all=Vendor.objects.all(),
                employees_all=Employee.objects.all(), departments_all=Department.objects.all(),
                clients_all=Client.objects.all(), projects_all=ProjectTemplate.objects.select_related('client'))


# --------------------------------------------------------------------------- dashboard
def _dashboard_period_filter(request):
    """Resolve the shared calendar/FY controls without changing report calculations."""
    from reporting import periods as shared_periods
    period = shared_periods.resolve(request.GET)
    f = reports.parse_filters({'period_mode': 'custom', 'date_from': period.start.isoformat(),
                               'date_to': period.end.isoformat()})
    f['label'] = period.label
    year = int(request.GET.get('calendar_year') or period.start.year)
    return f, year, request.GET.get('calendar_month', ''), request.GET.get('period_type', 'full_fy'), \
        request.GET.get('period_value', ''), ''


@local_only
def dashboard(request):
    f, selected_year, selected_month, selected_duration, selected_quarter, selected_half = _dashboard_period_filter(request)
    ctx = _filter_ctx(request, f)
    recorded = [CompanyExpense.objects.aggregate(first=Min('expense_date'), last=Max('expense_date')),
                C.objects.aggregate(first=Min('expense_date'), last=Max('expense_date'))]
    first_year = min([selected_year] + [bounds['first'].year for bounds in recorded if bounds['first']])
    last_year = max([selected_year, today().year] + [bounds['last'].year for bounds in recorded if bounds['last']])
    ctx.update(year_choices=range(last_year, max(1900, first_year) - 1, -1),
               month_names=[(number, calendar.month_name[number]) for number in range(1, 13)],
               selected_year=selected_year, selected_month=selected_month, selected_duration=selected_duration,
               selected_quarter=selected_quarter, selected_half=selected_half)
    ctx.update(reports.dashboard(f))
    m = ctx['monthly']
    peak = max([r[3] for r in m['rows']] + [1])
    ctx['trend'] = [(r[0], r[1], r[2], r[3], int(r[3] / peak * 100)) for r in m['rows']]
    cat_total = ctx['category']['totals'][2] or 1
    ctx['cat_rows'] = [(r[0], r[1], r[2], int(r[2] / cat_total * 100)) for r in ctx['category']['rows']]
    ctx['recent_expenses'] = CompanyExpense.objects.select_related('vendor', 'category')[:5]
    ctx['recent_claims'] = C.objects.select_related('employee')[:5]
    return render(request, 'expense_management/dashboard.html', ctx)


# --------------------------------------------------------------------------- all expenses
@local_only
def all_expenses(request):
    g = request.GET
    has_dates = any(g.get(k) for k in ('period_mode', 'period', 'fy', 'month', 'date_from', 'date_to'))
    f = reports.parse_filters(g) if has_dates else dict(reports.parse_filters({'period': 'custom', 'date_from': '1900-01-01', 'date_to': '2999-12-31'}), label='All dates')
    kind, q = g.get('type', ''), (g.get('q') or '').strip()
    pay_status, claim_status = g.get('payment_status', ''), g.get('claim_status', '')
    rows = []
    if kind in ('', 'company') and not claim_status:
        qs = reports.company_qs(f, include_cancelled=True, include_draft=True)
        if pay_status == 'draft':
            qs = qs.filter(is_draft=True)
        elif pay_status:
            qs = qs.filter(is_draft=False, payment_status=pay_status)
        if q:
            qs = qs.filter(Q(expense_id__icontains=q) | Q(invoice_number__icontains=q) | Q(vendor__name__icontains=q)
                           | Q(description__icontains=q) | Q(payments__reference__icontains=q)
                           | Q(employee__employee_id__icontains=q) | Q(employee__first_name__icontains=q)
                           | Q(employee__last_name__icontains=q) | Q(remarks__icontains=q)).distinct()
        for e in qs:
            rows.append(dict(kind='company', pk=e.pk, can_edit=e.is_draft and not e.is_cancelled,
                         can_cancel=not e.is_cancelled,
                             id=e.expense_id, date=e.expense_date, category=e.category.name,
                             party=e.vendor.name if e.vendor_id else '-', ref=e.invoice_number or '-',
                             amount=e.total_amount, currency=e.currency,
                             status='Draft' if e.is_draft else e.get_payment_status_display(),
                             status_key='draft' if e.is_draft else e.payment_status,
                             url=reverse('exp:expense_detail', args=[e.pk])))
    if kind in ('', 'claim') and not pay_status:
        qs = reports.claims_qs(f, statuses=None)
        if claim_status:
            qs = qs.filter(status=claim_status)
        if q:
            qs = qs.filter(Q(claim_id__icontains=q) | Q(snap_name__icontains=q) | Q(employee__employee_id__icontains=q)
                           | Q(employee__first_name__icontains=q) | Q(employee__last_name__icontains=q)
                           | Q(description__icontains=q) | Q(reimbursements__reference__icontains=q)
                           | Q(business_purpose__icontains=q)).distinct()
        for c in qs:
            rows.append(dict(kind='claim', pk=c.pk, can_edit=c.status in ('draft', 'submitted', 'under_review'),
                             can_cancel=c.status in ('draft', 'submitted', 'under_review'),
                             id=c.claim_id, date=c.expense_date, category=c.category.name,
                             party=f'{c.employee.employee_id} - {c.employee.full_name}', ref=c.claim_id,
                             amount=c.approved_amount if c.approved_amount is not None else c.amount_claimed,
                             currency=c.currency, status=c.status_label, status_key=c.status,
                             url=reverse('exp:claim_detail', args=[c.pk])))
    sort_fields = {
        'id': ('id', 'text'), 'date': ('date', 'date'), 'type': ('kind', 'text'),
        'category': ('category', 'text'), 'party': ('party', 'text'), 'reference': ('ref', 'text'),
        'amount': ('amount', 'currency'), 'status': ('status', 'status'),
    }
    sort_key = (request.GET.get('sort') or '').strip()
    sort_direction = (request.GET.get('direction') or 'asc').lower()
    if sort_key in sort_fields and sort_direction in {'asc', 'desc'}:
        rows.sort(key=lambda r: (r['kind'], r['pk']))
        field, value_kind = sort_fields[sort_key]
        rows = [r[1] for r in sort_table_values([[r[field], r] for r in rows], 0, value_kind, sort_direction)]
    else:
        rows.sort(key=lambda r: (r['date'], r['id']), reverse=True)
    ctx = _filter_ctx(request, f)
    ctx.update(rows=rows[:500], truncated=len(rows) > 500, total_rows=len(rows), q=q, kind=kind,
               pay_statuses=[('draft', 'Draft'), *CompanyExpense.STATUSES],
               claim_statuses=C.STATUSES, has_dates=has_dates,
               current_url=request.get_full_path())
    return render(request, 'expense_management/all_expenses.html', ctx)


# --------------------------------------------------------------------------- company expenses
def _save_uploads(request, parent, choices):
    files = request.FILES.getlist('attachments')
    doc_type = request.POST.get('doc_type') or choices[0][0]
    if doc_type not in dict(choices):
        doc_type = choices[-1][0]
    for fobj in files:
        services.add_attachment(parent, fobj, doc_type, request.POST.get('attachment_remarks', ''))
    return len(files)


@local_only
def expense_form(request, pk=None):
    exp = get_object_or_404(CompanyExpense, pk=pk) if pk else None
    if exp and exp.is_cancelled:
        messages.error(request, 'Cancelled expenses are read-only.')
        return redirect('exp:expense_detail', exp.pk)
    if exp and not exp.is_draft:
        messages.error(request, 'Only draft expenses can be edited. Return to the expense book to review the record.')
        return redirect('exp:expense_detail', exp.pk)
    initial = {} if exp else {'expense_date': today(), 'currency': 'INR', 'exchange_rate': 1}
    form = CompanyExpenseForm(request.POST or None, instance=exp, initial=initial)
    upload_errors = []
    if request.method == 'POST':
        upload_errors = services.validate_uploads(request.FILES.getlist('attachments'))
        if form.is_valid() and not upload_errors:
            try:
                with transaction.atomic():
                    obj = form.save(commit=False)
                    obj.is_draft = True
                    obj.save()
                    n = _save_uploads(request, obj, COMPANY_DOC_TYPES)
                messages.success(request, f'{obj.expense_id} saved as draft' + (f' with {n} attachment(s).' if n else '.'))
                return redirect('exp:expense_detail', obj.pk)
            except ValidationError as e:
                form.add_error(None, _errs(e))
    return render(request, 'expense_management/expense_form.html', dict(
        form=form, exp=exp, upload_errors=upload_errors, doc_types=COMPANY_DOC_TYPES,
        vendor_form=QuickVendorForm(prefix='qv'),
        projects=ProjectTemplate.objects.select_related('client')))


@local_only
@require_POST
def expense_finalize(request, pk):
    exp = get_object_or_404(CompanyExpense, pk=pk)
    if exp.is_cancelled:
        messages.error(request, 'Cancelled expenses cannot be finalized.')
        return redirect('exp:expense_detail', exp.pk)
    if not exp.is_draft:
        messages.info(request, 'This expense has already been finalized.')
        return redirect('exp:expense_detail', exp.pk)
    try:
        with transaction.atomic():
            exp.is_draft = False
            exp.payment_status = CompanyExpense.UNPAID
            exp.save()
        messages.success(request, f'{exp.expense_id} finalized and posted to the ledger. You can now record payment.')
    except ValidationError as e:
        messages.error(request, _errs(e))
    return redirect('exp:expense_detail', exp.pk)


@local_only
def expense_detail(request, pk):
    exp = get_object_or_404(CompanyExpense.objects.select_related('vendor', 'category', 'subcategory', 'client',
                                                                   'project', 'department', 'employee'), pk=pk)
    return render(request, 'expense_management/expense_detail.html', dict(
        exp=exp, payments=exp.payments.all(), attachments=exp.attachments.all(), doc_types=COMPANY_DOC_TYPES,
        pay_form=PaymentForm(initial={'payment_date': today(), 'amount': exp.outstanding_amount or None})))


@local_only
@require_POST
def expense_payment(request, pk):
    exp = get_object_or_404(CompanyExpense, pk=pk)
    form = PaymentForm(request.POST)
    if form.is_valid():
        try:
            p = services.record_payment(exp, **form.cleaned_data)
            messages.success(request, f'Payment of {p.amount} recorded. Status: {exp.get_payment_status_display()}.')
        except ValidationError as e:
            messages.error(request, _errs(e))
    else:
        messages.error(request, '; '.join(f'{k}: {" ".join(v)}' for k, v in form.errors.items()))
    return redirect('exp:expense_detail', pk)


@local_only
@require_POST
def expense_cancel(request, pk):
    exp = get_object_or_404(CompanyExpense, pk=pk)
    try:
        services.cancel_expense(exp, request.POST.get('reason'))
        messages.success(request, f'{exp.expense_id} cancelled. Its payments and attachments are kept as history.')
    except ValidationError as e:
        messages.error(request, _errs(e))
    return redirect('exp:expense_detail', pk)


@local_only
@require_POST
def expense_attach(request, pk):
    exp = get_object_or_404(CompanyExpense, pk=pk)
    errs = services.validate_uploads(request.FILES.getlist('attachments'))
    if not exp.can_add_attachments:
        messages.error(request, 'This expense is cancelled; documents can no longer be added.')
    elif errs:
        messages.error(request, ' '.join(errs))
    elif not request.FILES.getlist('attachments'):
        messages.error(request, 'Choose at least one file.')
    else:
        n = _save_uploads(request, exp, COMPANY_DOC_TYPES)
        messages.success(request, f'{n} attachment(s) added.')
    return redirect('exp:expense_detail', pk)


# --------------------------------------------------------------------------- attachments (both kinds)
ATT = {'company': (CompanyExpenseAttachment, 'expense', 'exp:expense_detail'),
       'claim': (EmployeeExpenseAttachment, 'claim', 'exp:claim_detail')}


def _att(kind, pk):
    if kind not in ATT:
        raise Http404
    model, parent, back = ATT[kind]
    att = get_object_or_404(model, pk=pk)
    return att, getattr(att, f'{parent}_id'), back


@local_only
def attachment_file(request, kind, pk, mode='view'):
    att, parent_pk, _ = _att(kind, pk)
    ext = att.file.name.rsplit('.', 1)[-1]
    parent = att.expense.expense_id if kind == 'company' else att.claim.claim_id
    name = f'{parent}-{att.doc_type}-{att.pk}.{ext}'
    return private_file_response(att.file, att.content_type or 'application/octet-stream', name, inline=mode == 'view')


@local_only
def attachment_replace(request, kind, pk):
    att, parent_pk, back = _att(kind, pk)
    if not getattr(att, ATT[kind][1]).attachments_editable:
        messages.error(request, 'This record is finalized or closed, so its documents are locked. You can still view or download them.')
        return redirect(back, parent_pk)
    if request.method == 'POST':
        f = request.FILES.get('file')
        if not f:
            messages.error(request, 'Choose a file.')
        else:
            try:
                services.replace_attachment(att, f)
                messages.success(request, 'Attachment replaced.')
                return redirect(back, parent_pk)
            except ValidationError as e:
                messages.error(request, _errs(e))
    return render(request, 'expense_management/attachment_replace.html',
                  dict(att=att, back_url=reverse(back, args=[parent_pk])))


@local_only
def attachment_delete(request, kind, pk):
    att, parent_pk, back = _att(kind, pk)
    if not getattr(att, ATT[kind][1]).attachments_editable:
        messages.error(request, 'This record is finalized or closed, so its documents are locked. You can still view or download them.')
        return redirect(back, parent_pk)
    if request.method == 'POST':
        services.delete_attachment(att)
        messages.success(request, 'Attachment deleted.')
        return redirect(back, parent_pk)
    return render(request, 'expense_management/confirm.html', dict(
        title='Delete attachment', back_url=reverse(back, args=[parent_pk]), button='Yes, delete attachment',
        text=f'Delete "{att.get_doc_type_display()}" ({att.original_filename})? The file is removed permanently; '
             f'the expense record itself is not affected.'))


# --------------------------------------------------------------------------- claims
def _claim_filters(request):
    g = request.GET
    qs = C.objects.select_related('employee', 'department', 'category')
    for k in ('employee', 'department', 'category'):
        if (g.get(k) or '').isdigit():
            qs = qs.filter(**{f'{k}_id': int(g[k])})
    if g.get('status'):
        if g['status'] == 'pending':
            qs = qs.filter(status__in=C.PENDING)
        else:
            qs = qs.filter(status=g['status'])
    from .fy import parse_date
    for fld in ('claim_date', 'expense_date'):
        a, b = parse_date(g.get(f'{fld}_from')), parse_date(g.get(f'{fld}_to'))
        if a:
            qs = qs.filter(**{f'{fld}__gte': a})
        if b:
            qs = qs.filter(**{f'{fld}__lte': b})
    if (g.get('q') or '').strip():
        q = g['q'].strip()
        qs = qs.filter(Q(claim_id__icontains=q) | Q(snap_name__icontains=q) | Q(employee__employee_id__icontains=q)
                       | Q(description__icontains=q))
    return qs


def _claim_list(request, template, title):
    qs = _claim_filters(request)
    if request.GET.get('sort') == 'reimbursed':
        qs = qs.annotate(_reimbursed_sort=Sum('reimbursements__amount'))
    qs, _, _ = apply_queryset_sort(qs, request, {
        'claim': 'claim_id', 'employee': 'snap_name', 'department': 'snap_department',
        'claim_date': 'claim_date', 'expense_date': 'expense_date', 'category': 'category__name',
        'claimed': 'amount_claimed', 'approved': 'approved_amount', 'reimbursed': '_reimbursed_sort',
        'status': 'status', 'expense': 'expense_date',
    })
    ctx = dict(rows=qs[:500], params=request.GET, statuses=C.STATUSES, title=title,
               employees_all=Employee.objects.all(), departments_all=Department.objects.all(),
               categories=ExpenseCategory.objects.all(), next_url=request.get_full_path())
    return render(request, template, ctx)


@local_only
def claims(request):
    return _claim_list(request, 'expense_management/claims.html', 'Employee claims')


@local_only
def review(request):
    if not request.GET:
        request.GET = request.GET.copy()
        request.GET['status'] = 'pending'
    return _claim_list(request, 'expense_management/review.html', 'Review Employee Claim')


@local_only
def claim_form(request, pk=None):
    claim = get_object_or_404(C, pk=pk) if pk else None
    if claim and claim.status not in C.EDITABLE:
        messages.error(request, f'A {claim.get_status_display()} claim cannot be edited. Return it for correction first.')
        return redirect('exp:claim_detail', claim.pk)
    initial = {} if claim else {'claim_date': today(), 'expense_date': today(), 'currency': 'INR', 'exchange_rate': 1,
                                'employee': request.GET.get('employee')}
    form = ClaimForm(request.POST or None, instance=claim, initial=initial)
    upload_errors = []
    if request.method == 'POST':
        upload_errors = services.validate_uploads(request.FILES.getlist('attachments'))
        if form.is_valid() and not upload_errors:
            try:
                with transaction.atomic():
                    obj = form.save()
                    n = _save_uploads(request, obj, CLAIM_DOC_TYPES)
                    if claim:
                        from .models import EmployeeClaimReview
                        EmployeeClaimReview.objects.create(claim=obj, action='edit', from_status=obj.status,
                                                           to_status=obj.status, remarks='Claim details edited')
                    if request.POST.get('submit_now') and obj.status == C.DRAFT:
                        services.submit_claim(obj)
                messages.success(request, f'{obj.claim_id} saved' + (f' with {n} attachment(s).' if n else '.'))
                return redirect('exp:claim_detail', obj.pk)
            except ValidationError as e:
                form.add_error(None, _errs(e))
    emps = Employee.objects.select_related('department')
    return render(request, 'expense_management/claim_form.html', dict(
        form=form, claim=claim, upload_errors=upload_errors, doc_types=CLAIM_DOC_TYPES, employees=emps,
        projects=ProjectTemplate.objects.select_related('client')))


@local_only
def claim_detail(request, pk):
    claim = get_object_or_404(C.objects.select_related('employee', 'employee__department', 'category', 'subcategory',
                                                        'client', 'project'), pk=pk)
    bank = claim.employee.current_bank
    return render(request, 'expense_management/claim_detail.html', dict(
        claim=claim, attachments=claim.attachments.all(), reimbursements=claim.reimbursements.all(),
        reviews=claim.reviews.all(), doc_types=CLAIM_DOC_TYPES, bank=bank,
        reimb_form=ReimbursementForm(initial={'reimbursement_date': today(), 'amount': claim.outstanding_amount or None,
                                              'method': 'bank_transfer',
                                              'bank_account': f'{bank.bank_name} {bank.account_masked}' if bank else ''})))


ACTIONS = {
    'submit': lambda c, p: services.submit_claim(c, p.get('remarks')),
    'start_review': lambda c, p: services.start_review(c, p.get('remarks')),
    'approve': lambda c, p: services.approve_claim(c, None, p.get('remarks')),
    'partial': lambda c, p: services.approve_claim(c, p.get('approved_amount') or '0', p.get('remarks')),
    'reject': lambda c, p: services.reject_claim(c, p.get('reason')),
    'return': lambda c, p: services.return_for_correction(c, p.get('remarks')),
    'cancel': lambda c, p: services.cancel_claim(c, p.get('reason')),
}


@local_only
@require_POST
def claim_action(request, pk, action):
    claim = get_object_or_404(C, pk=pk)
    if action not in ACTIONS:
        raise Http404
    try:
        ACTIONS[action](claim, request.POST)
        claim.refresh_from_db()
        messages.success(request, f'{claim.claim_id}: {claim.status_label}.')
    except ValidationError as e:
        messages.error(request, _errs(e))
    nxt = request.POST.get('next')
    if nxt and nxt.startswith('/expenses/'):
        return redirect(nxt)
    return redirect('exp:claim_detail', pk)


@local_only
@require_POST
def claim_reimburse(request, pk):
    claim = get_object_or_404(C, pk=pk)
    form = ReimbursementForm(request.POST)
    if form.is_valid():
        try:
            r = services.record_reimbursement(claim, **form.cleaned_data)
            messages.success(request, f'Reimbursement of {r.amount} recorded. Outstanding: {claim.outstanding_amount}.')
        except ValidationError as e:
            messages.error(request, _errs(e))
    else:
        messages.error(request, '; '.join(f'{k}: {" ".join(v)}' for k, v in form.errors.items()))
    return redirect('exp:claim_detail', pk)


@local_only
@require_POST
def claim_attach(request, pk):
    claim = get_object_or_404(C, pk=pk)
    files = request.FILES.getlist('attachments')
    errs = services.validate_uploads(files)
    if not claim.can_add_attachments:
        messages.error(request, f'This claim is {claim.get_status_display().lower()}; documents can no longer be added.')
    elif errs or not files:
        messages.error(request, ' '.join(errs) or 'Choose at least one file.')
    else:
        n = _save_uploads(request, claim, CLAIM_DOC_TYPES)
        messages.success(request, f'{n} attachment(s) added.')
    return redirect('exp:claim_detail', pk)


# --------------------------------------------------------------------------- vendors
@local_only
def vendors(request):
    q = (request.GET.get('q') or '').strip()
    qs = Vendor.objects.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(vendor_code__icontains=q) | Q(gstin__icontains=q)
                       | Q(city__icontains=q) | Q(contact_person__icontains=q))
    if request.GET.get('status') in (Vendor.ACTIVE, Vendor.INACTIVE):
        qs = qs.filter(status=request.GET['status'])
    return render(request, 'expense_management/vendors.html', dict(rows=qs, q=q, params=request.GET))


@local_only
def vendor_form(request, pk=None):
    v = get_object_or_404(Vendor, pk=pk) if pk else None
    form = VendorForm(request.POST or None, instance=v)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f'Vendor {obj.vendor_code} saved.')
        return redirect('exp:vendor_detail', obj.pk)
    return render(request, 'expense_management/simple_form.html', dict(
        form=form, title='Edit vendor' if v else 'Add vendor', eyebrow='Vendors',
        back_url=reverse('exp:vendor_detail', args=[v.pk]) if v else reverse('exp:vendors')))


@local_only
def vendor_detail(request, pk):
    v = get_object_or_404(Vendor, pk=pk)
    f = reports.parse_filters({'period': 'custom', 'date_from': '1900-01-01', 'date_to': '2999-12-31'})
    f['vendor'] = v.pk
    rep = reports.vendor(f)
    return render(request, 'expense_management/vendor_detail.html', dict(
        v=v, expenses=v.expenses.select_related('category').all(), summary=rep['totals']))


@local_only
@require_POST
def vendor_quick_add(request):
    form = QuickVendorForm(request.POST, prefix='qv')
    if form.is_valid():
        v = form.save()
        return JsonResponse({'ok': True, 'id': v.pk, 'name': v.name, 'code': v.vendor_code})
    return JsonResponse({'ok': False, 'errors': {k: ' '.join(e) for k, e in form.errors.items()}}, status=400)


@local_only
@require_POST
def vendor_toggle(request, pk):
    v = get_object_or_404(Vendor, pk=pk)
    v.status = Vendor.INACTIVE if v.status == Vendor.ACTIVE else Vendor.ACTIVE
    v.save(update_fields=['status'])
    messages.success(request, f'{v.name} is now {v.get_status_display()}.')
    return redirect('exp:vendors')


# --------------------------------------------------------------------------- categories
@local_only
def categories(request):
    cats = ExpenseCategory.objects.prefetch_related('subcategories')
    return render(request, 'expense_management/categories.html', dict(rows=cats, form=CategoryForm()))


@local_only
def category_form(request, pk=None):
    cat = get_object_or_404(ExpenseCategory, pk=pk) if pk else None
    form = CategoryForm(request.POST or None, instance=cat)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Category saved.')
        return redirect('exp:categories')
    return render(request, 'expense_management/simple_form.html', dict(
        form=form, title='Edit category' if cat else 'Add category', eyebrow='Expense categories',
        back_url=reverse('exp:categories')))


@local_only
def subcategory_form(request, cat_pk, pk=None):
    cat = get_object_or_404(ExpenseCategory, pk=cat_pk)
    sub = get_object_or_404(ExpenseSubcategory, pk=pk, category=cat) if pk else None
    form = SubcategoryForm(request.POST or None, instance=sub)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.category = cat
        try:
            obj.validate_unique()
            obj.save()
            messages.success(request, 'Subcategory saved.')
            return redirect('exp:categories')
        except ValidationError:
            form.add_error('name', 'This subcategory already exists in the category.')
    return render(request, 'expense_management/simple_form.html', dict(
        form=form, title=f'{"Edit" if sub else "Add"} subcategory in {cat.name}', eyebrow='Expense categories',
        back_url=reverse('exp:categories')))


@local_only
@require_POST
def category_toggle(request, kind, pk):
    model = ExpenseCategory if kind == 'category' else ExpenseSubcategory
    obj = get_object_or_404(model, pk=pk)
    obj.active = not obj.active
    obj.save(update_fields=['active'])
    messages.success(request, f'{obj} {"activated" if obj.active else "deactivated"}.')
    return redirect('exp:categories')


@local_only
@require_POST
def category_delete(request, kind, pk):
    model = ExpenseCategory if kind == 'category' else ExpenseSubcategory
    obj = get_object_or_404(model, pk=pk)
    in_use = obj.in_use or (kind == 'category' and any(s.in_use for s in obj.subcategories.all()))
    if in_use:
        messages.error(request, f'"{obj}" is used by existing expense records and cannot be deleted. Deactivate it instead.')
    elif kind == 'category' and obj.subcategories.exists():
        messages.error(request, f'"{obj}" still has subcategories. Delete or move them first, or deactivate it.')
    else:
        obj.delete()
        messages.success(request, f'"{obj}" deleted.')
    return redirect('exp:categories')


# --------------------------------------------------------------------------- reports
@local_only
def report_view(request, key='monthly'):
    if key not in reports.REPORTS:
        raise Http404
    f = reports.parse_filters(request.GET)
    rep = reports.build(key, f)
    fmt = request.GET.get('format')
    if fmt == 'csv':
        return exports.to_csv(rep, f)
    if fmt == 'xlsx':
        return exports.to_xlsx(rep, f)
    if fmt == 'pdf':
        bp = BusinessProfile.objects.first()
        return exports.to_pdf(rep, f, bp.name if bp else '')
    qs = request.GET.copy()
    qs.pop('format', None)
    ctx = _filter_ctx(request, f)
    ctx.update(rep=rep, key=key, report_list=reports.REPORTS.items(), query=qs.urlencode())
    return render(request, 'expense_management/reports.html', ctx)
