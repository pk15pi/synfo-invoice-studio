"""Idempotent document automation and rule-based compliance evaluation."""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from access_control.permissions import Role, role_for
from .models import (
    DocumentApproval, DocumentAutomationExecution, DocumentAutomationLock,
    DocumentAutomationRule, DocumentAutomationRuleEvent, DocumentComplianceRequirement,
    DocumentComplianceReview, DocumentCorrectiveAction, DocumentCorrectiveActionEvent,
    DocumentNotification, DocumentNotificationPreference, DocumentRenewal,
    DocumentShare, DocumentShareEvent, DocumentShareSignatory, GeneratedDocument,
)
from .lifecycle_services import evaluate_expired_documents
from .sharing_services import create_renewal_tracking

DEFAULT_RULES = [
    ('approval-reminders', 'Pending approval reminders', 'approval', [1, 3, 5], 7),
    ('document-expiry', 'Document expiry notifications', 'expiry', [90, 60, 30, 15, 7, 0], None),
    ('document-renewal', 'Renewal reminders', 'renewal', [90, 60, 30, 15, 7, 0], None),
    ('signature-reminders', 'Pending signature reminders', 'signature', [3, 7, 14], 21),
    ('acknowledgement-reminders', 'Pending acknowledgement reminders', 'acknowledgement', [3, 7, 14], 21),
    ('compliance-reviews', 'Recurring compliance review', 'compliance', [30], None),
]
MANAGEMENT_ROLES = {Role.ADMIN, Role.DIRECTOR, Role.HEAD_HR, Role.HR, Role.DOCUMENT_CONTROLLER}


def ensure_default_rules():
    made = []
    for code, name, rule_type, intervals, escalation in DEFAULT_RULES:
        rule, created = DocumentAutomationRule.objects.get_or_create(
            code=code,
            defaults={'name': name, 'rule_type': rule_type, 'intervals_days': intervals,
                       'escalation_after_days': escalation, 'enabled': True},
        )
        if created:
            DocumentAutomationRuleEvent.objects.create(rule=rule, action='created',
                                                        details={'source': 'safe_default'})
        made.append(rule)
    return made


def _allows_preference(user, rule_type):
    if not user or not user.is_active:
        return False
    preference = DocumentNotificationPreference.objects.filter(user=user).first()
    if not preference:
        return True
    return bool(getattr(preference, {
        'approval': 'approval_reminders', 'expiry': 'expiry_reminders', 'renewal': 'renewal_reminders',
        'signature': 'signature_reminders', 'acknowledgement': 'acknowledgement_reminders',
        'compliance': 'compliance_alerts',
    }.get(rule_type, 'compliance_alerts')))


def _notify(rule, document, user, event_key, message, *, trigger):
    if not rule.in_app_enabled or not _allows_preference(user, rule.rule_type):
        return False
    dedupe = f'{rule.code}:{event_key}:{user.pk}'[:180]
    notification, created = DocumentNotification.objects.get_or_create(
        dedupe_key=dedupe,
        defaults={
            'user': user, 'generated_document': document, 'action': rule.rule_type + '_reminder',
            'subject': rule.name, 'message': message[:240], 'trigger_event': trigger,
            'delivery_method': 'in_app', 'delivery_status': 'sent',
        },
    )
    return created


def _threshold(intervals, elapsed):
    due = [day for day in intervals if day <= elapsed]
    return max(due) if due else None


def _days_between(start, end, calendar_days=True):
    if calendar_days:
        return (end - start).days
    if end < start:
        return -_days_between(end, start, calendar_days=False)
    count = 0
    day = start + timedelta(days=1)
    while day <= end:
        if day.weekday() < 5:
            count += 1
        day += timedelta(days=1)
    return count


def _management_users():
    User = get_user_model()
    return [user for user in User.objects.filter(is_active=True)
            if user.is_superuser or role_for(user) in MANAGEMENT_ROLES]


def _approval_reminders(rule):
    today = timezone.localdate()
    sent = 0
    approvals = DocumentApproval.objects.filter(
        status=DocumentApproval.PENDING,
        generated_document__status='pending_approval',
    ).select_related('generated_document', 'approver')
    for approval in approvals:
        elapsed = max(0, _days_between(approval.requested_at.date(), today, rule.calendar_days))
        threshold = _threshold(rule.intervals_days, elapsed)
        if threshold is None:
            continue
        key = f'approval:{approval.pk}:{threshold}'
        if approval.approver and _notify(rule, approval.generated_document, approval.approver, key,
                                         f'{approval.generated_document.document_number} is awaiting your approval (level {approval.level}).',
                                         trigger='approval_pending'):
            sent += 1
        if rule.escalation_after_days is not None and elapsed >= rule.escalation_after_days:
            configured = list(rule.escalation_recipients.filter(is_active=True))
            for manager in (configured or _management_users()):
                if _notify(rule, approval.generated_document, manager, key + ':escalation',
                           f'Approval for {approval.generated_document.document_number} is overdue at level {approval.level}.',
                           trigger='approval_escalation'):
                    sent += 1
    return sent


def _expiry_reminders(rule):
    today = timezone.localdate()
    sent = 0
    documents = GeneratedDocument.objects.filter(
        status__in=('issued', 'sent', 'acknowledged', 'signed', 'expired'), expiry_date__isnull=False,
    ).select_related('created_by', 'issued_by')
    for document in documents:
        days = _days_between(today, document.expiry_date, rule.calendar_days)
        if days not in rule.intervals_days and not (days < 0 and 0 in rule.intervals_days):
            continue
        threshold = 0 if days < 0 else days
        user = document.created_by or document.issued_by
        message = (f'{document.document_number} expired on {document.expiry_date:%d %b %Y}.' if days < 0 else
                   f'{document.document_number} expires in {days} day(s), on {document.expiry_date:%d %b %Y}.')
        if user and _notify(rule, document, user, f'expiry:{document.pk}:{threshold}', message,
                            trigger='expiry_threshold'):
            sent += 1
        for manager in _management_users():
            if manager == user:
                continue
            if _notify(rule, document, manager, f'expiry:{document.pk}:{threshold}', message,
                       trigger='expiry_threshold'):
                sent += 1
    # An actual expiry is handled by Phase 4 lifecycle; this sends on the date, never cancels/deletes.
    return sent


def _renewal_reminders(rule):
    today = timezone.localdate()
    sent = 0
    documents = GeneratedDocument.objects.filter(
        document_type__renewal_enabled=True, expiry_date__isnull=False,
    ).select_related('document_type', 'created_by', 'issued_by')
    for document in documents:
        renewal = create_renewal_tracking(document)
        if not renewal or renewal.status in {'initiated', 'in_progress', 'renewed', 'declined'}:
            continue
        days = (document.expiry_date - today).days
        if days not in rule.intervals_days and days != renewal.lead_time_days and days > 0:
            continue
        user = document.created_by or document.issued_by
        threshold = 0 if days <= 0 else days
        if user and _notify(rule, document, user, f'renewal:{document.pk}:{threshold}',
                            f'Renewal for {document.document_number} is {"overdue" if days < 0 else "due"}; expiry date {document.expiry_date:%d %b %Y}.',
                            trigger='renewal_due'):
            sent += 1
    return sent


def _share_reminders(rule, acknowledgement=False):
    today = timezone.localdate()
    sent = 0
    shares = DocumentShare.objects.filter(revoked_at__isnull=True).select_related(
        'generated_document', 'shared_by',
    ).prefetch_related('signatories')
    for share in shares:
        if share.generated_document.status not in {'issued', 'sent', 'signed', 'acknowledged'}:
            continue
        pending = (share.acknowledgement_status == 'pending') if acknowledgement else (
            share.signature_required and share.signature_status in {'pending', 'partially_signed'}
        )
        if not pending or not share.shared_by:
            continue
        elapsed = max(0, _days_between(share.shared_at.date(), today, rule.calendar_days))
        threshold = _threshold(rule.intervals_days, elapsed)
        if threshold is None:
            continue
        action = 'acknowledgement' if acknowledgement else 'signature'
        key = f'{action}:{share.pk}:{threshold}'
        label = 'acknowledgement' if acknowledgement else 'signed copy'
        if _notify(rule, share.generated_document, share.shared_by, key,
                   f'{share.generated_document.document_number}: {label} is still pending from {share.recipient_name}.',
                   trigger=action + '_pending'):
            sent += 1
        if not DocumentShareEvent.objects.filter(share=share, action='automated_reminder',
                                                details__dedupe_key=key).exists():
            DocumentShareEvent.objects.create(
                share=share, action='automated_reminder', actor_label='Document automation',
                details={'dedupe_key': key, 'action': action, 'threshold_days': threshold},
            )
    return sent


def _party_recipient_type(party):
    role = (party.role or '').lower()
    if role in {'employee', 'candidate', 'freelancer', 'consultant'}:
        return 'employee'
    if role in {'client', 'vendor'}:
        return 'client'
    if role in {'issuer', 'asset'}:
        return 'other'
    return 'external'


def _evaluate_one(requirement, party, force=False, actor=None):
    latest_review = DocumentComplianceReview.objects.filter(
        requirement=requirement, party=party,
    ).order_by('-checked_at').first()
    if not force and latest_review and (timezone.now() - latest_review.checked_at).days < requirement.review_frequency_days:
        return None
    matching_parties = type(party).objects.filter(
        content_type_id=party.content_type_id, object_id=party.object_id,
    ).select_related('generated_document__document_type')
    documents = GeneratedDocument.objects.filter(
        parties__in=matching_parties, document_type=requirement.document_type,
    ).exclude(status__in=('cancelled', 'superseded')).order_by('-version_number', '-created_at')
    document = documents.first()
    reasons = []
    due_date = document.expiry_date if document else None
    status = 'compliant'
    if not document:
        status = 'non_compliant' if requirement.mandatory else 'pending'
        reasons.append('Required document is missing.' if requirement.mandatory else 'Optional document is not present.')
    else:
        if document.status == 'expired' or (document.expiry_date and document.expiry_date < timezone.localdate()):
            status = 'non_compliant'
            reasons.append('Document has expired.')
        if requirement.validity_period_days and document.official_issue_date:
            calculated_expiry = document.official_issue_date + timedelta(days=requirement.validity_period_days)
            due_date = min(due_date, calculated_expiry) if due_date else calculated_expiry
            if calculated_expiry < timezone.localdate():
                status = 'non_compliant'
                reasons.append('Configured validity period has elapsed.')
        if requirement.approval_required and document.status in {'draft', 'pending_approval', 'revision_requested', 'rejected'}:
            status = 'pending' if document.status in {'draft', 'pending_approval'} else 'non_compliant'
            reasons.append('Required approval is incomplete.')
        shares = list(document.shares.filter(party=party, revoked_at__isnull=True))
        if requirement.acknowledgement_required and not any(s.acknowledgement_status == 'acknowledged' for s in shares):
            status = 'pending' if status == 'compliant' else status
            reasons.append('Required acknowledgement is not recorded.')
        if requirement.signature_required and not any(s.signature_status == 'fully_signed' for s in shares):
            status = 'pending' if status == 'compliant' else status
            reasons.append('Required signed copies are not all verified.')
    return DocumentComplianceReview.objects.create(
        requirement=requirement, party=party, generated_document=document, status=status,
        reason=' '.join(reasons), reviewed_by=actor, due_date=due_date,
    )


def evaluate_compliance(requirements=None, *, force=False, actor=None):
    requirements = requirements or DocumentComplianceRequirement.objects.filter(is_active=True)
    results = []
    if hasattr(requirements, 'select_related'):
        requirements = requirements.select_related('document_type', 'responsible_person')
    for requirement in requirements:
        # Include known linked recipients even when they have no document of this required type.
        from .models import DocumentParty
        party_links = DocumentParty.objects.filter(content_type__isnull=False).values(
            'content_type_id', 'object_id',
        ).distinct()
        for link in party_links.iterator():
            party = DocumentParty.objects.filter(
                content_type_id=link['content_type_id'], object_id=link['object_id'],
            ).order_by('id').first()
            if not party:
                continue
            if _party_recipient_type(party) != requirement.recipient_type:
                continue
            review = _evaluate_one(requirement, party, force=force, actor=actor)
            if review:
                results.append(review)
                if review.status == 'non_compliant' and requirement.responsible_person:
                    # The notification is deduplicated per review record; frequency governs the next review.
                    user = requirement.responsible_person
                    DocumentNotification.objects.get_or_create(
                        dedupe_key=f'compliance:{review.pk}:{user.pk}',
                        defaults={'user': user, 'generated_document': review.generated_document
                                  or GeneratedDocument.objects.order_by('pk').first(),
                                  'action': 'compliance_alert', 'subject': requirement.name,
                                  'message': f'{requirement.name}: {review.reason}'[:240],
                                  'trigger_event': 'compliance_review', 'delivery_method': 'in_app',
                                  'delivery_status': 'sent'},
                    ) if review.generated_document else None
    return results


def _run_rule(rule):
    started = timezone.now()
    execution = DocumentAutomationExecution.objects.create(
        rule=rule, idempotency_key=f'{rule.code}:{started.isoformat()}:{uuid.uuid4().hex}',
        started_at=started, status='running',
    )
    try:
        if rule.rule_type == 'approval':
            count = _approval_reminders(rule)
        elif rule.rule_type == 'expiry':
            count = _expiry_reminders(rule)
        elif rule.rule_type == 'renewal':
            count = _renewal_reminders(rule)
        elif rule.rule_type == 'signature':
            count = _share_reminders(rule, acknowledgement=False)
        elif rule.rule_type == 'acknowledgement':
            count = _share_reminders(rule, acknowledgement=True)
        else:
            count = len(evaluate_compliance())
        execution.status = 'completed'
        execution.result_count = count
        execution.details = {'notifications_or_reviews': count}
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'result_count', 'details', 'completed_at'])
        rule.last_run_at = execution.completed_at
        rule.last_error = ''
        rule.next_run_at = execution.completed_at + timedelta(hours=1)
        rule.save(update_fields=['last_run_at', 'last_error', 'next_run_at', 'updated_at'])
    except Exception as error:
        execution.status = 'failed'
        execution.error = str(error)[:4000]
        execution.completed_at = timezone.now()
        execution.save(update_fields=['status', 'error', 'completed_at'])
        rule.last_run_at = execution.completed_at
        rule.last_error = execution.error
        rule.next_run_at = execution.completed_at + timedelta(hours=1)
        rule.save(update_fields=['last_run_at', 'last_error', 'next_run_at', 'updated_at'])
    return execution


def run_document_automation(*, actor=None, rule_ids=None):
    ensure_default_rules()
    rules = (list(DocumentAutomationRule.objects.filter(pk__in=rule_ids)) if rule_ids
             else list(DocumentAutomationRule.objects.all()))
    token = uuid.uuid4().hex
    now = timezone.now()
    lock, _ = DocumentAutomationLock.objects.get_or_create(name='document-automation')
    claimed = DocumentAutomationLock.objects.filter(name=lock.name).filter(
        Q(locked_until__isnull=True) | Q(locked_until__lt=now),
    ).update(owner_token=token, locked_until=now + timedelta(minutes=30))
    if not claimed:
        return {'locked': True, 'executions': []}
    results = []
    try:
        evaluate_expired_documents()
        for rule in rules:
            if not rule.enabled:
                continue
            results.append(_run_rule(rule))
    finally:
        DocumentAutomationLock.objects.filter(name=lock.name, owner_token=token).update(
            owner_token='', locked_until=None,
        )
    return {'locked': False, 'executions': results}


def record_corrective_action(action, actor, previous_status=None):
    DocumentCorrectiveActionEvent.objects.create(
        corrective_action=action, action='updated' if previous_status else 'created', actor=actor,
        details={'previous_status': previous_status or '', 'status': action.status,
                 'target_date': action.target_date.isoformat(), 'problem': action.problem},
    )
