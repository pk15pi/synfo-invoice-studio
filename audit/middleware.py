"""Stores the request path and any submitted reason in the audit contextvar for the duration of a request."""
from .context import _ctx


def _reason_from(request):
    if request.method != 'POST':
        return ''
    try:
        data = request.POST
    except Exception:
        return ''
    for k in data:
        if k == 'reason' or k.endswith('_reason'):
            v = (data.get(k) or '').strip()
            if v:
                return v[:500]
    return ''


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tok = _ctx.set({'path': request.path[:300], 'reason': _reason_from(request), 'source': 'ui'})
        try:
            return self.get_response(request)
        finally:
            _ctx.reset(tok)
