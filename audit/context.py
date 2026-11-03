"""Per-request / per-command audit context (request path, reason, source) kept in a contextvar."""
import contextvars
import sys
from contextlib import contextmanager

_ctx = contextvars.ContextVar('qb_audit_ctx', default=None)


def _default_source():
    argv = sys.argv or []
    if argv and argv[0].endswith('manage.py') and len(argv) > 1 and argv[1] not in ('runserver', 'test'):
        return 'command'
    return 'system'


def current():
    c = _ctx.get()
    if c is None:
        return {'path': '', 'reason': '', 'source': _default_source()}
    return c


@contextmanager
def audit_context(source='system', path='', reason=''):
    tok = _ctx.set({'path': path or '', 'reason': (reason or '')[:500], 'source': source})
    try:
        yield
    finally:
        _ctx.reset(tok)


def set_reason(reason):
    c = dict(current())
    c['reason'] = (reason or '')[:500]
    _ctx.set(c)
