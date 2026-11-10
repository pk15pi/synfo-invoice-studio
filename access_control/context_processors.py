from django.conf import settings

from .permissions import ALL_MODULES, Role, allowed_modules, role_for, role_label


def access_control(request):
    if not settings.ACCESS_CONTROL_ENABLED:
        return {
            'current_role': '',
            'current_role_code': '',
            'allowed_modules': ALL_MODULES,
            'can_manage_users': False,
            'can_manage_roles': False,
        }
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}
    role = role_for(user)
    modules = allowed_modules(user)
    return {
        'current_role': role_label(role),
        'current_role_code': role,
        'allowed_modules': modules,
        'can_manage_users': 'users' in modules,
        'can_manage_roles': role in (Role.ADMIN, Role.DIRECTOR),
    }
