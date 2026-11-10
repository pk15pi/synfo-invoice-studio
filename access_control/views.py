from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache

from .forms import AppAuthenticationForm, RoleForm, UserCreateForm, UserEditForm
from .models import RoleDefinition, UserProfile
from .permissions import (MODULE_LABELS, READ_ONLY_ROLES, ROLE_MODULES, SHARED,
                          USER_MANAGEMENT_ROLES, Role, role_for, role_label, role_required)

User = get_user_model()
SENIOR_ROLES = frozenset({Role.ADMIN, Role.DIRECTOR})
ROLES_REQUIRED = (Role.ADMIN, Role.DIRECTOR)


def _assignable_roles(request):
    """IT administrators can manage accounts but cannot mint Admin/Director access."""
    if role_for(request.user) == Role.IT_ADMIN:
        assignable = frozenset(Role.values) - SENIOR_ROLES
        return assignable | frozenset(RoleDefinition.objects.values_list('code', flat=True))
    return None


def _module_labels(codes):
    labels = [(MODULE_LABELS.get(code, code), code) for code in codes if code != SHARED]
    return [label for label, _ in sorted(labels)]


def _guard_target(request, target):
    if role_for(request.user) != Role.IT_ADMIN:
        return
    try:
        target_role = target.access_profile.role
    except UserProfile.DoesNotExist:
        target_role = ''
    if target.is_superuser or target_role in SENIOR_ROLES:
        raise PermissionDenied('Admin and Director accounts can only be managed by an Admin or Director.')


class AppLoginView(LoginView):
    template_name = 'access_control/login.html'
    authentication_form = AppAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('home')


class AppLogoutView(LogoutView):
    next_page = reverse_lazy('access_control:login')


@never_cache
@role_required(*USER_MANAGEMENT_ROLES)
def user_list(request):
    users = User.objects.select_related('access_profile').order_by('username')
    caller_is_it = role_for(request.user) == Role.IT_ADMIN
    rows = []
    for user in users:
        role = role_for(user)
        rows.append({
            'user': user,
            'role': role,
            'role_label': role_label(role),
            'is_self': user.pk == request.user.pk,
            'can_edit': not caller_is_it or (not user.is_superuser and role not in SENIOR_ROLES),
        })
    return render(request, 'access_control/user_list.html', {'rows': rows})


@never_cache
@role_required(*USER_MANAGEMENT_ROLES)
def user_create(request):
    form = UserCreateForm(request.POST or None, allowed_roles=_assignable_roles(request))
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = form.cleaned_data['role']
        profile.save(update_fields=['role'])
        messages.success(request, f'User “{user.get_username()}” created with the '
                                  f'{role_label(profile.role)} role.')
        return redirect('access_control:users')
    return render(request, 'access_control/user_form.html', {'form': form, 'mode': 'create'})


def _other_active_admin_exists(user):
    return (User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(access_profile__role=Role.ADMIN))
            .exclude(pk=user.pk).exists())


@never_cache
@role_required(*USER_MANAGEMENT_ROLES)
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    _guard_target(request, target)
    profile, _ = UserProfile.objects.get_or_create(user=target)
    previous_role = profile.role
    form = UserEditForm(request.POST or None, instance=target,
                        allowed_roles=_assignable_roles(request))
    if request.method == 'POST' and form.is_valid():
        new_role = form.cleaned_data['role']
        new_active = form.cleaned_data['is_active']
        if target.pk == request.user.pk and not new_active:
            form.add_error('is_active', 'You cannot deactivate your own account.')
        elif (previous_role == Role.ADMIN and (new_role != Role.ADMIN or not new_active)
              and not target.is_superuser and not _other_active_admin_exists(target)):
            form.add_error('role', 'At least one active Admin must remain.')
        else:
            user = form.save()
            profile.role = new_role
            profile.save(update_fields=['role'])
            if form.cleaned_data.get('new_password'):
                user.set_password(form.cleaned_data['new_password'])
                user.save(update_fields=['password'])
                if user.pk == request.user.pk:
                    update_session_auth_hash(request, user)
                messages.success(request, f'Password updated for “{user.get_username()}”.')
            messages.success(request, f'User “{user.get_username()}” updated.')
            return redirect('access_control:users')
    return render(request, 'access_control/user_form.html', {
        'form': form, 'mode': 'edit', 'target': target,
        'target_role': role_label(profile.role),
    })


@never_cache
@role_required(*ROLES_REQUIRED)
def role_list(request):
    built_in_rows = [{
        'name': label,
        'code': code,
        'modules': _module_labels(ROLE_MODULES.get(code, frozenset())),
        'read_only': code in READ_ONLY_ROLES,
        'built_in': True,
    } for code, label in Role.choices]
    custom_rows = [{
        'name': role.name,
        'code': role.code,
        'modules': _module_labels(frozenset(role.modules)),
        'read_only': role.read_only,
        'built_in': False,
        'pk': role.pk,
    } for role in RoleDefinition.objects.all()]
    return render(request, 'access_control/role_list.html', {
        'built_in_rows': built_in_rows,
        'custom_rows': custom_rows,
    })


@never_cache
@role_required(*ROLES_REQUIRED)
def role_create(request):
    form = RoleForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        role = form.save()
        messages.success(request, f'Role “{role.name}” created. Assign it from Users & roles.')
        return redirect('access_control:roles')
    return render(request, 'access_control/role_form.html', {'form': form, 'mode': 'create'})


@never_cache
@role_required(*ROLES_REQUIRED)
def role_edit(request, pk):
    role = get_object_or_404(RoleDefinition, pk=pk)
    form = RoleForm(request.POST or None, instance=role)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Role “{role.name}” updated.')
        return redirect('access_control:roles')
    return render(request, 'access_control/role_form.html', {
        'form': form, 'mode': 'edit', 'role': role,
    })
