from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache

from .forms import AppAuthenticationForm, UserCreateForm, UserEditForm
from .models import UserProfile
from .permissions import Role, role_for, role_required

User = get_user_model()
ROLE_LABELS = dict(UserProfile.Role.choices)


class AppLoginView(LoginView):
    template_name = 'access_control/login.html'
    authentication_form = AppAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy('home')


class AppLogoutView(LogoutView):
    next_page = reverse_lazy('access_control:login')


@never_cache
@role_required(Role.ADMIN, Role.DIRECTOR)
def user_list(request):
    users = User.objects.select_related('access_profile').order_by('username')
    rows = []
    for user in users:
        role = role_for(user)
        rows.append({
            'user': user,
            'role': role,
            'role_label': ROLE_LABELS.get(role, 'No role'),
            'is_self': user.pk == request.user.pk,
        })
    return render(request, 'access_control/user_list.html', {'rows': rows})


@never_cache
@role_required(Role.ADMIN, Role.DIRECTOR)
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = form.cleaned_data['role']
        profile.save(update_fields=['role'])
        messages.success(request, f'User “{user.get_username()}” created with the '
                                  f'{ROLE_LABELS.get(profile.role, "No role")} role.')
        return redirect('access_control:users')
    return render(request, 'access_control/user_form.html', {'form': form, 'mode': 'create'})


def _other_active_admin_exists(user):
    return (User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(access_profile__role=Role.ADMIN))
            .exclude(pk=user.pk).exists())


@never_cache
@role_required(Role.ADMIN, Role.DIRECTOR)
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    profile, _ = UserProfile.objects.get_or_create(user=target)
    previous_role = profile.role
    form = UserEditForm(request.POST or None, instance=target)
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
        'target_role': ROLE_LABELS.get(profile.role, 'No role'),
    })
