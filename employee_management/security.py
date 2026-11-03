"""Local-only guard. This app has NO authentication (by request); every employee and
payroll view refuses requests that do not come from this computer. That is a
safety net, not real access control: do not expose the app to a network."""
from functools import wraps

from django.http import HttpResponseForbidden

LOCAL = {'127.0.0.1', '::1'}


def local_only(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        if request.META.get('REMOTE_ADDR') not in LOCAL:
            return HttpResponseForbidden('Employee and payroll data is only available from this computer.')
        resp = view(request, *a, **kw)
        resp.setdefault('Cache-Control', 'no-store, private')
        resp.setdefault('X-Content-Type-Options', 'nosniff')
        resp.setdefault('Referrer-Policy', 'same-origin')  # 'no-referrer' makes browsers send Origin: null on POST -> CSRF failure
        return resp
    return wrapper


def private_file_response(fieldfile, content_type, download_name, inline=True):
    from django.http import FileResponse, Http404
    try:
        fh = fieldfile.open('rb')
    except (FileNotFoundError, ValueError, OSError):
        raise Http404('File is missing from private storage.')
    resp = FileResponse(fh, content_type=content_type, as_attachment=not inline, filename=download_name)
    resp['Cache-Control'] = 'no-store, private'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Content-Security-Policy'] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    return resp
