from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import PasswordResetOTP, RoleDefinition, UserProfile
from .permissions import role_label


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'channel', 'email', 'created_at', 'expires_at', 'used_at')
    list_filter = ('channel',)
    search_fields = ('user__username', 'email')
    readonly_fields = ('user', 'channel', 'email', 'code_hash', 'created_at', 'expires_at',
                       'attempts', 'used_at')

    def has_add_permission(self, request):
        return False


@admin.register(RoleDefinition)
class RoleDefinitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'read_only', 'created_at')
    list_filter = ('read_only',)
    search_fields = ('name', 'code')


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email')


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Role'
    extra = 0


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]
    list_display = ('username', 'first_name', 'last_name', 'email', 'role', 'is_active', 'last_login')
    list_select_related = ('access_profile',)

    @admin.display(description='Role')
    def role(self, obj):
        try:
            return role_label(obj.access_profile.role)
        except UserProfile.DoesNotExist:
            return 'No role'
