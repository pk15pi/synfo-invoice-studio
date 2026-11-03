"""Global localhost-only guard (every page, invoices included). This app has no login by design; this is a
safety net, not access control. Do not expose the app to a network."""
from django.http import HttpResponseForbidden

from employee_management.security import LOCAL


class LocalOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.META.get('REMOTE_ADDR') not in LOCAL:
            return HttpResponseForbidden('QuantBud Invoice Studio is only available from this computer.')
        return self.get_response(request)
