"""Signal handlers that turn model saves/deletes into AuditEvents."""
import hashlib
import json
import logging

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.fields.files import FieldFile

from . import context
from .masking import HASH_FIELDS, mask_value, never_log, scrub_text

log = logging.getLogger(__name__)

# status value -> action
STATUS_ACTIONS = {'posted': 'post', 'reversed': 'reverse', 'finalized': 'finalize', 'cancelled': 'cancel',
                  'closed': 'close'}
CREATE_ACTIONS = {}   # 'app.Model' -> action used on create (e.g. bank ImportBatch -> 'import')
_old = {}


def _plain(v):
    if isinstance(v, FieldFile):
        return v.name or ''
    try:
        return json.loads(json.dumps(v, cls=DjangoJSONEncoder))
    except (TypeError, ValueError):
        return str(v)


def snapshot(obj):
    out = {}
    for f in obj._meta.concrete_fields:
        if never_log(f.name):
            continue
        value = _plain(getattr(obj, f.attname, None))
        if f.name in HASH_FIELDS:
            serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
            value = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        out[f.attname] = value
    return out


def _key(obj):
    return (obj._meta.label, obj.pk, id(obj))


def pre_save(sender, instance, raw=False, **kw):
    if raw or instance.pk is None:
        return
    row = sender._default_manager.filter(pk=instance.pk).first()
    if row is not None:
        _old[_key(instance)] = snapshot(row)


def _action(label, old, new, created):
    st_old, st_new = (old or {}).get('status'), new.get('status')
    if created:
        if label in CREATE_ACTIONS:
            return CREATE_ACTIONS[label]
        if label == 'accounting.JournalEntry' and st_new == 'posted':
            return 'post'
        return 'create'
    if st_old != st_new and st_new is not None:
        if label == 'accounting.JournalEntry' and st_new == 'posted' and new.get('reversal_of_id'):
            return 'reverse'
        if st_new in STATUS_ACTIONS:
            return STATUS_ACTIONS[st_new]
        if st_old == 'closed' and st_new == 'open':
            return 'reopen'
    if old and not old.get('cancelled_at') and new.get('cancelled_at'):
        return 'cancel'
    return 'update'


def _reason(action, new, changes):
    c = context.current()
    if c.get('reason'):
        return c['reason']
    pref = {'close': 'close_reason', 'reopen': 'reopen_reason', 'reverse': 'reversal_reason',
            'cancel': 'cancel_reason'}.get(action)
    if pref and new.get(pref):
        return str(new[pref])
    for k, (o, n) in changes.items():
        if k.endswith('reason') and n:
            return str(n)
    return ''


def write(obj, action, changes, reason=None):
    from .models import AuditEvent
    c = context.current()
    masked = {k: [mask_value(k, o), mask_value(k, n)] for k, (o, n) in changes.items() if not never_log(k)}
    try:
        rep = scrub_text(str(obj))[:300]
    except Exception:
        rep = ''
    AuditEvent.objects.create(
        app_label=obj._meta.app_label, model=obj._meta.object_name, object_id=str(obj.pk or ''),
        object_repr=rep, action=action, changes=masked,
        reason=scrub_text(reason if reason is not None else _reason(action, snapshot(obj), changes))[:500],
        request_path=c.get('path', '')[:300], source=c.get('source', 'system'))


def post_save(sender, instance, created=False, raw=False, **kw):
    if raw:
        return
    old = _old.pop(_key(instance), None)
    new = snapshot(instance)
    if created or old is None:
        changes = {k: [None, v] for k, v in new.items() if v not in (None, '', [], {})}
        action = _action(sender._meta.label, None, new, True)
    else:
        changes = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
        if not changes:
            return
        action = _action(sender._meta.label, old, new, False)
    write(instance, action, changes, reason=_reason(action, new, changes))


def post_delete(sender, instance, **kw):
    snap = snapshot(instance)
    write(instance, 'delete', {k: [v, None] for k, v in snap.items() if v not in (None, '')})


def log_manual(app_label, model, object_id, object_repr, action, reason='', changes=None):
    """Write an event that is not tied to a model save (backup, restore...)."""
    from .models import AuditEvent
    c = context.current()
    masked = {k: [mask_value(k, o), mask_value(k, n)] for k, (o, n) in (changes or {}).items() if not never_log(k)}
    return AuditEvent.objects.create(app_label=app_label, model=model, object_id=str(object_id), action=action,
                                     object_repr=scrub_text(object_repr)[:300], changes=masked,
                                     reason=scrub_text(reason)[:500], request_path=c.get('path', '')[:300],
                                     source=c.get('source', 'system'))
