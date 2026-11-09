"""Email invoices page (mirrors employee_management.views_email)."""
from urllib.parse import urlencode

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from employee_management import email_config as EC
from . import invoice_email as IE
from .models import Invoice, InvoiceEmailLog


def invoice_email_list(request):
    if not IE.has_period_params(request.GET):
        params = IE.previous_month_params()
        for key in ('q', 'page_size'):
            if request.GET.get(key):
                params[key] = request.GET.get(key)
        return redirect(f"{reverse('invoice_email')}?{urlencode(params)}")
    period = IE.resolve_page_period(request.GET)
    search = (request.GET.get('q') or '').strip()
    rows = list(IE.invoices_for_period(period, search=search))
    queue = IE.bulk_queue(rows)
    pub = EC.public_config()
    return render(request, 'invoice_app/invoice_email.html', {
        'period': period,
        'rows': rows,
        'search': search,
        'smtp_ready': pub['password_set'],
        'send_gap_seconds': pub['send_gap_seconds'],
        'bulk_pending_ids': queue['pending_ids'],
        'bulk_skipped_sent': queue['skipped_already_sent'],
        'bulk_skipped_email': queue['skipped_no_email'],
        'bulk_total': queue['total'],
        'send_json_url_template': reverse('invoice_email_send_json', args=[0]).replace('/0/', '/__ID__/'),
    })


@require_POST
def invoice_email_send(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related('client'),
        pk=pk, status=Invoice.FINALIZED, cancelled=False,
    )
    log = IE.send_invoice(invoice)
    if log.success:
        messages.success(request, f'Sent invoice {invoice.invoice_number} to {log.recipient_email}.')
        try:
            from audit.recorder import log_manual
            log_manual(
                'invoice_app', 'InvoiceEmailLog', str(log.pk),
                invoice.invoice_number, 'create',
                f'Invoice emailed to {log.recipient_email}',
                {'success': [None, True]},
            )
        except Exception:
            pass
    else:
        messages.error(request, f'Could not send invoice {invoice.invoice_number}: {log.error}')
    nxt = request.POST.get('next') or reverse('invoice_email')
    if not nxt.startswith('/'):
        nxt = reverse('invoice_email')
    return redirect(nxt)


@require_POST
def invoice_email_send_json(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related('client'),
        pk=pk, status=Invoice.FINALIZED, cancelled=False,
    )
    skip_if_sent = request.POST.get('skip_if_sent') == '1'
    if skip_if_sent and InvoiceEmailLog.objects.filter(invoice=invoice, success=True).exists():
        return JsonResponse({
            'ok': True, 'status': 'skipped', 'reason': 'already_sent',
            'code': invoice.invoice_number,
        })
    if not IE.customer_email(invoice.client):
        log = IE.send_invoice(invoice)
        return JsonResponse({
            'ok': False, 'status': 'skipped', 'reason': 'no_email',
            'code': invoice.invoice_number, 'error': log.error,
        })
    log = IE.send_invoice(invoice)
    if log.success:
        try:
            from audit.recorder import log_manual
            log_manual(
                'invoice_app', 'InvoiceEmailLog', str(log.pk),
                invoice.invoice_number, 'create',
                f'Invoice emailed to {log.recipient_email}',
                {'success': [None, True]},
            )
        except Exception:
            pass
        return JsonResponse({
            'ok': True, 'status': 'sent',
            'code': invoice.invoice_number, 'email': log.recipient_email,
        })
    return JsonResponse({
        'ok': False, 'status': 'failed',
        'code': invoice.invoice_number, 'error': log.error,
    })
