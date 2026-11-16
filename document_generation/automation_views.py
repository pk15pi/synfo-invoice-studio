"""Automation, notification and configurable document compliance management pages."""
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.pagination import paginate as qb_paginate
from invoice_app.table_sorting import apply_queryset_sort
from .automation_services import (
    ensure_default_rules, evaluate_compliance, record_corrective_action, run_document_automation,
)
from .forms import (
    DocumentAutomationRuleForm, DocumentComplianceRequirementForm,
    DocumentCorrectiveActionForm, DocumentNotificationPreferenceForm, RenewalUpdateForm,
)
from .lifecycle_services import can_view_all_documents, is_document_manager
from .models import (
    DocumentApproval, DocumentAutomationExecution, DocumentAutomationRule,
    DocumentComplianceRequirement, DocumentComplianceRequirementEvent, DocumentComplianceReview, DocumentCorrectiveAction,
    DocumentCorrectiveActionEvent, DocumentNotification, DocumentNotificationPreference,
    DocumentRenewal, DocumentShare, DocumentShareSignatory, GeneratedDocument,
)
from .sharing_services import update_renewal


def _require_manager(user):
    if not is_document_manager(user):
        raise PermissionDenied('Only authorised document managers can change automation or compliance settings.')


def _require_oversight(user):
    if not can_view_all_documents(user):
        raise PermissionDenied('This view is available to authorised management and audit roles.')


@local_only
def automation_dashboard(request):
    ensure_default_rules()
    today = timezone.localdate()
    end = today + timedelta(days=30)
    documents = GeneratedDocument.objects.all()
    if not can_view_all_documents(request.user):
        if not request.user.is_authenticated:
            documents = documents.none()
        else:
            documents = documents.filter(Q(created_by=request.user) | Q(approvals__approver=request.user)).distinct()
    approval_rule = DocumentAutomationRule.objects.filter(code='approval-reminders').first()
    overdue_days = approval_rule.escalation_after_days if approval_rule and approval_rule.escalation_after_days is not None else 7
    approvals = DocumentApproval.objects.filter(status='pending', generated_document__status='pending_approval',
                                                 generated_document__in=documents)
    if request.user.is_authenticated and not is_document_manager(request.user):
        approvals = approvals.filter(approver=request.user)
    shares = DocumentShare.objects.filter(revoked_at__isnull=True,
                                          generated_document__in=documents).select_related('generated_document')
    pending_ack = shares.filter(acknowledgement_status='pending')
    pending_sign = [share for share in shares.filter(signature_required=True).prefetch_related('signatories')
                    if share.signature_status in {'pending', 'partially_signed'}]
    cards = {
        'approval_reminders': approvals.count(),
        'overdue_approvals': approvals.filter(requested_at__date__lte=today - timedelta(days=overdue_days)).count(),
        'pending_signatures': len(pending_sign),
        'pending_acknowledgements': pending_ack.count(),
        'expiring_soon': documents.filter(
            status__in=('issued', 'sent', 'signed', 'acknowledged'), expiry_date__gte=today,
            expiry_date__lte=end).count(),
        'expired': documents.filter(status='expired').count(),
        'renewals_due': DocumentRenewal.objects.filter(status__in=('due', 'overdue'),
                                                       generated_document__in=documents).count(),
        'failed_tasks': DocumentAutomationExecution.objects.filter(status='failed').count()
        if can_view_all_documents(request.user) else 0,
        'pending_notifications': DocumentNotification.objects.filter(read_at__isnull=True).count()
        if can_view_all_documents(request.user) else (DocumentNotification.objects.filter(
            read_at__isnull=True, user=request.user).count() if request.user.is_authenticated else 0),
    }
    executions = DocumentAutomationExecution.objects.select_related('rule', 'generated_document')
    if not can_view_all_documents(request.user):
        executions = executions.filter(generated_document__in=documents)
    executions = list(executions[:50])
    for execution in executions:
        party = execution.generated_document.parties.first() if execution.generated_document_id else None
        execution.recipient_label = party.display_name if party else ''
        execution.next_action_date = execution.rule.next_run_at
    return render(request, 'document_generation/automation_dashboard.html', {
        'cards': cards, 'executions': executions, 'rules': DocumentAutomationRule.objects.order_by('name'),
        'can_manage': is_document_manager(request.user),
    })


@local_only
def automation_settings(request):
    _require_manager(request.user)
    rules = ensure_default_rules()
    if request.method == 'POST':
        rule = get_object_or_404(DocumentAutomationRule, pk=request.POST.get('rule_id'))
        form = DocumentAutomationRuleForm(request.POST, instance=rule)
        if form.is_valid():
            before = {'enabled': rule.enabled, 'intervals_days': rule.intervals_days,
                      'escalation_after_days': rule.escalation_after_days,
                      'escalation_recipients': list(rule.escalation_recipients.values_list('pk', flat=True))}
            saved = form.save(commit=False)
            saved.updated_by = request.user
            saved.save()
            form.save_m2m()
            from .models import DocumentAutomationRuleEvent
            DocumentAutomationRuleEvent.objects.create(
                rule=saved, action='enabled' if saved.enabled and not before['enabled'] else
                ('disabled' if before['enabled'] and not saved.enabled else 'updated'), actor=request.user,
                details={'before': before, 'after': {'enabled': saved.enabled,
                         'intervals_days': saved.intervals_days,
                         'escalation_after_days': saved.escalation_after_days,
                         'escalation_recipients': list(saved.escalation_recipients.values_list('pk', flat=True))}},
            )
            messages.success(request, f'{saved.name} updated.')
            return redirect('document_generation:automation_settings')
        selected_rule = rule.pk
        bound_form = form
    else:
        selected_rule = None
        bound_form = None
    rule_forms = [(rule, bound_form if rule.pk == selected_rule else DocumentAutomationRuleForm(instance=rule))
                  for rule in rules]
    return render(request, 'document_generation/automation_settings.html', {
        'rule_forms': rule_forms, 'selected_rule': selected_rule,
        'last_runs': DocumentAutomationExecution.objects.select_related('rule')[:30],
    })


@local_only
def notification_history(request):
    notifications = DocumentNotification.objects.select_related('user', 'generated_document',
                                                                'generated_document__document_type')
    if not can_view_all_documents(request.user):
        if not request.user.is_authenticated:
            notifications = notifications.none()
        else:
            notifications = notifications.filter(user=request.user)
    action = request.GET.get('type', '').strip()
    status = request.GET.get('status', '').strip()
    q = request.GET.get('q', '').strip()[:100]
    if action:
        notifications = notifications.filter(action=action)
    if status:
        notifications = notifications.filter(delivery_status=status)
    if q:
        notifications = notifications.filter(
            Q(generated_document__document_number__icontains=q) | Q(message__icontains=q)
            | Q(user__username__icontains=q) | Q(subject__icontains=q)
        )
    notifications, _, _ = apply_queryset_sort(notifications, request, {
        'created': 'created_at', 'type': 'action', 'status': 'delivery_status',
        'recipient': 'user__username', 'number': 'generated_document__document_number',
    })
    page = qb_paginate(request, notifications)
    return render(request, 'document_generation/notification_history.html', {
        'page': page, 'type': action, 'status': status, 'q': q,
        'actions': DocumentNotification.objects.values_list('action', flat=True).distinct().order_by('action'),
        'statuses': DocumentNotification.DELIVERY_STATUSES,
        'can_manage': is_document_manager(request.user),
    })


@local_only
@require_POST
def notification_mark_read(request, notification_id):
    notification = DocumentNotification.objects.filter(pk=notification_id).first()
    if not notification:
        from django.http import Http404
        raise Http404
    if notification.user_id != getattr(request.user, 'pk', None) and not is_document_manager(request.user):
        raise PermissionDenied
    if not notification.read_at:
        notification.read_at = timezone.now()
        notification.save(update_fields=['read_at'])
    next_url = request.POST.get('next', '')
    return redirect(next_url if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ) else 'document_generation:notification_history')


@local_only
def notification_preferences(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    preference, _ = DocumentNotificationPreference.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        form = DocumentNotificationPreferenceForm(request.POST, instance=preference)
        if form.is_valid():
            saved = form.save(commit=False)
            if is_document_manager(request.user):
                saved.approval_reminders = True
                saved.expiry_reminders = True
                saved.compliance_alerts = True
            saved.save()
            messages.success(request, 'Notification preferences saved. Management and compliance alerts remain enabled.')
            return redirect('document_generation:notification_preferences')
    else:
        form = DocumentNotificationPreferenceForm(instance=preference)
    return render(request, 'document_generation/notification_preferences.html', {'form': form})


@local_only
def compliance_dashboard(request):
    _require_oversight(request.user)
    latest_reviews = DocumentComplianceReview.objects.select_related(
        'requirement__document_type', 'generated_document', 'party', 'reviewed_by',
    ).order_by('-checked_at')
    latest = {}
    for review in latest_reviews:
        key = (review.requirement_id, review.party_id)
        latest.setdefault(key, review)
    reviews = list(latest.values())
    cards = {status: sum(1 for review in reviews if review.status == status)
             for status, _ in DocumentComplianceReview.STATUSES}
    upcoming = DocumentComplianceReview.objects.filter(
        due_date__gte=timezone.localdate(), due_date__lte=timezone.localdate() + timedelta(days=30),
    ).select_related('requirement', 'generated_document').order_by('due_date')[:20]
    cards.update({
        'missing': sum(1 for review in reviews if 'missing' in review.reason.lower()),
        'expired': sum(1 for review in reviews if 'expired' in review.reason.lower()),
        'signatures': sum(1 for review in reviews if 'signed copies' in review.reason.lower()),
        'acknowledgements': sum(1 for review in reviews if 'acknowledgement' in review.reason.lower()),
        'approvals': sum(1 for review in reviews if 'approval' in review.reason.lower()),
        'upcoming': DocumentComplianceReview.objects.filter(
            due_date__gte=timezone.localdate(), due_date__lte=timezone.localdate() + timedelta(days=30),
        ).count(),
    })
    return render(request, 'document_generation/compliance_dashboard.html', {
        'reviews': reviews[:200], 'cards': cards, 'upcoming': upcoming,
        'requirements': DocumentComplianceRequirement.objects.filter(is_active=True).count(),
        'can_manage': is_document_manager(request.user),
    })


@local_only
@require_POST
def compliance_run(request):
    _require_manager(request.user)
    reviews = evaluate_compliance(force=True, actor=request.user)
    messages.success(request, f'Compliance check completed for {len(reviews)} known linked recipient record(s).')
    return redirect('document_generation:compliance_dashboard')


@local_only
def compliance_requirements(request):
    _require_manager(request.user)
    edit_id = request.GET.get('edit') or request.POST.get('requirement_id')
    instance = get_object_or_404(DocumentComplianceRequirement, pk=edit_id) if edit_id else None
    form = DocumentComplianceRequirementForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        fields = ('name', 'document_type_id', 'recipient_type', 'mandatory', 'validity_period_days',
                  'approval_required', 'signature_required', 'acknowledgement_required',
                  'responsible_person_id', 'review_frequency_days', 'is_active')
        before = {field: getattr(instance, field) for field in fields} if instance else {}
        requirement = form.save(commit=False)
        requirement.created_by = requirement.created_by or request.user
        requirement.save()
        after = {field: getattr(requirement, field) for field in fields}
        DocumentComplianceRequirementEvent.objects.create(
            requirement=requirement, action='updated' if instance else 'created', actor=request.user,
            details={'before': before, 'after': after},
        )
        messages.success(request, 'Compliance requirement saved. It reflects company policy and is not legal certification.')
        return redirect('document_generation:compliance_requirements')
    requirements = DocumentComplianceRequirement.objects.select_related(
        'document_type', 'responsible_person').prefetch_related('history__actor')
    return render(request, 'document_generation/compliance_requirements.html', {
        'form': form, 'requirements': requirements,
    })


@local_only
@require_POST
def compliance_requirement_toggle(request, requirement_id):
    _require_manager(request.user)
    requirement = get_object_or_404(DocumentComplianceRequirement, pk=requirement_id)
    old = requirement.is_active
    requirement.is_active = not old
    requirement.save(update_fields=['is_active'])
    DocumentComplianceRequirementEvent.objects.create(
        requirement=requirement, action='enabled' if requirement.is_active else 'disabled',
        actor=request.user, details={'before': {'is_active': old},
                                    'after': {'is_active': requirement.is_active}},
    )
    messages.success(request, 'Compliance requirement status updated.')
    return redirect('document_generation:compliance_requirements')


@local_only
def compliance_history(request):
    _require_oversight(request.user)
    reviews = DocumentComplianceReview.objects.select_related(
        'requirement__document_type', 'generated_document', 'party', 'reviewed_by',
    )
    status = request.GET.get('status', '').strip()
    if status:
        reviews = reviews.filter(status=status)
    reviews, _, _ = apply_queryset_sort(reviews, request, {'checked': 'checked_at', 'status': 'status'})
    page = qb_paginate(request, reviews)
    return render(request, 'document_generation/compliance_history.html', {
        'page': page, 'status': status, 'statuses': DocumentComplianceReview.STATUSES,
    })


@local_only
def corrective_actions(request):
    _require_manager(request.user)
    edit_id = request.GET.get('edit')
    instance = get_object_or_404(DocumentCorrectiveAction, pk=edit_id) if edit_id else None
    form = DocumentCorrectiveActionForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        action = form.save(commit=False)
        old_status = instance.status if instance else None
        if action.status in {'resolved', 'closed'} and not action.completed_at:
            action.completed_at = timezone.now()
        action.created_by = action.created_by or request.user
        action.save()
        record_corrective_action(action, request.user, old_status)
        messages.success(request, 'Corrective action saved. Resolution requires management notes and review.')
        return redirect('document_generation:corrective_actions')
    actions = DocumentCorrectiveAction.objects.select_related('requirement', 'generated_document',
                                                               'responsible_person').prefetch_related('history')
    status = request.GET.get('status', '').strip()
    if status:
        actions = actions.filter(status=status)
    overdue_only = request.GET.get('overdue') == '1'
    if overdue_only:
        actions = actions.filter(target_date__lt=timezone.localdate()).exclude(status__in=('resolved', 'closed'))
    page = qb_paginate(request, actions)
    return render(request, 'document_generation/corrective_actions.html', {
        'form': form, 'page': page, 'status': status, 'overdue_only': overdue_only,
        'statuses': DocumentCorrectiveAction.STATUSES,
    })


@local_only
def automation_history(request):
    executions = DocumentAutomationExecution.objects.select_related('rule', 'generated_document')
    status = request.GET.get('status', '').strip()
    rule = request.GET.get('rule', '').strip()
    if status:
        executions = executions.filter(status=status)
    if rule.isdigit():
        executions = executions.filter(rule_id=int(rule))
    executions, _, _ = apply_queryset_sort(executions, request, {
        'started': 'started_at', 'rule': 'rule__name', 'status': 'status',
    })
    page = qb_paginate(request, executions)
    return render(request, 'document_generation/automation_history.html', {
        'page': page, 'status': status, 'rule_id': rule,
        'statuses': DocumentAutomationExecution.STATUSES,
        'rules': DocumentAutomationRule.objects.order_by('name'),
        'can_manage': is_document_manager(request.user),
    })


@local_only
@require_POST
def automation_run(request):
    _require_manager(request.user)
    result = run_document_automation(actor=request.user)
    if result['locked']:
        messages.warning(request, 'Another automation run currently holds the execution lock.')
    else:
        failed = sum(1 for execution in result['executions'] if execution.status == 'failed')
        messages.success(request, f"Automation run finished: {len(result['executions'])} rules; {failed} failed.")
    return redirect('document_generation:automation_history')


@local_only
@require_POST
def automation_retry(request, rule_id):
    _require_manager(request.user)
    rule = get_object_or_404(DocumentAutomationRule, pk=rule_id)
    result = run_document_automation(actor=request.user, rule_ids=[rule.pk])
    if result['locked']:
        messages.warning(request, 'Another automation run currently holds the execution lock.')
    else:
        messages.success(request, f'Retry executed for {rule.name}. Review its new execution record.')
    return redirect('document_generation:automation_history')


@local_only
def renewal_update(request, document_id):
    from .models import GeneratedDocument, DocumentRenewal
    from .sharing_services import can_manage_share
    document = get_object_or_404(GeneratedDocument.objects.select_related('document_type'), pk=document_id)
    if not can_manage_share(request.user, document):
        raise PermissionDenied
    renewal = DocumentRenewal.objects.filter(generated_document=document).first()
    initial = {'lead_time_days': renewal.lead_time_days if renewal else document.document_type.renewal_lead_time_days,
               'status': renewal.status if renewal and renewal.status in {'initiated', 'in_progress', 'declined'} else 'initiated'}
    form = RenewalUpdateForm(request.POST or None, document=document, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            update_renewal(document, request.user, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Renewal tracking updated; no document was generated or issued automatically.')
            return redirect('document_generation:document_detail', document_id=document.pk)
    return render(request, 'document_generation/renewal_form.html', {
        'document': document, 'form': form, 'renewal': renewal,
    })
