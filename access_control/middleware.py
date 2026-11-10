from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import render

from .permissions import (MODULE_LABELS, allowed_modules, is_exempt_path, is_read_only_role,
                          module_for_path, role_for)

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


class AccessControlMiddleware:
    """Require sign-in and enforce the role module matrix for every request.

    Disabled while running the legacy test suite through ACCESS_CONTROL_ENABLED;
    the dedicated access-control tests turn it back on with override_settings.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.ACCESS_CONTROL_ENABLED:
            return self.get_response(request)
        path = request.path_info
        if is_exempt_path(path):
            return self.get_response(request)
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

        module = module_for_path(path)
        if module is None:
            return self.get_response(request)
        if module not in allowed_modules(request.user):
            return render(request, 'access_control/denied.html', {
                'denied_module': MODULE_LABELS.get(module, 'this area'),
                'denied_role': role_for(request.user),
            }, status=403)
        if is_read_only_role(role_for(request.user)) and request.method not in SAFE_METHODS:
            return render(request, 'access_control/denied.html', {
                'denied_module': MODULE_LABELS.get(module, 'this area'),
                'denied_role': role_for(request.user),
                'read_only_block': True,
            }, status=403)
        return self.get_response(request)
