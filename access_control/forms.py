import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import RoleDefinition, UserProfile
from .permissions import MODULE_LABELS, SELF_SERVICE, SHARED, USERS

CUSTOM_ROLE_EXCLUDED_MODULES = frozenset({SHARED, SELF_SERVICE, USERS})


def role_choices(allowed_roles=None):
    rows = [('', 'No role assigned')]
    rows += [(value, label) for value, label in UserProfile.Role.choices
             if allowed_roles is None or value in allowed_roles]
    rows += [(role.code, role.name) for role in RoleDefinition.objects.order_by('name')
             if allowed_roles is None or role.code in allowed_roles]
    return rows


def _role_code(name):
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:32] or 'role'


def _unique_role_code(name):
    base = _role_code(name)
    code, counter = base, 1
    while code in UserProfile.Role.values or RoleDefinition.objects.filter(code=code).exists():
        counter += 1
        suffix = f'_{counter}'
        code = base[:32 - len(suffix)] + suffix
    return code


class AppAuthenticationForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={
        'autofocus': True, 'autocomplete': 'username', 'placeholder': 'Username',
    }))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={
        'autocomplete': 'current-password', 'placeholder': 'Password',
    }))
    error_messages = {
        'invalid_login': 'Username or password is incorrect.',
        'inactive': 'This account is inactive. Contact an administrator.',
    }


class BaseUserForm(forms.ModelForm):
    role = forms.ChoiceField(choices=[], required=False,
                             help_text='Controls which modules this user can open.')

    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {'email': forms.EmailInput(attrs={'autocomplete': 'email'})}

    def __init__(self, *args, allowed_roles=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_roles = set(allowed_roles) if allowed_roles is not None else None
        self.fields['role'].choices = role_choices(self.allowed_roles)
        if self.instance and self.instance.pk:
            try:
                self.fields['role'].initial = self.instance.access_profile.role
            except UserProfile.DoesNotExist:
                pass
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                css = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f'{css} field'.strip()
        self.order_fields(['username', 'first_name', 'last_name', 'email', 'role', 'is_active'])


class UserCreateForm(BaseUserForm):
    password = forms.CharField(min_length=8, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password_confirm = forms.CharField(label='Confirm password', widget=forms.PasswordInput(
        attrs={'autocomplete': 'new-password'}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('password') != cleaned.get('password_confirm'):
            self.add_error('password_confirm', 'The two passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if commit:
            user.save()
        return user


class RoleForm(forms.ModelForm):
    modules = forms.MultipleChoiceField(
        label='Modules this role can open', widget=forms.CheckboxSelectMultiple(attrs={'class': 'module-checks'}),
        help_text='The role always keeps access to Shared pages.')

    class Meta:
        model = RoleDefinition
        fields = ['name', 'modules', 'read_only']
        labels = {'read_only': 'Read-only role'}
        help_texts = {'read_only': 'Block add, edit and delete actions for this role.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['modules'].choices = [
            (code, label) for code, label in MODULE_LABELS.items()
            if code not in CUSTOM_ROLE_EXCLUDED_MODULES]
        if self.instance and self.instance.pk:
            offered = {code for code, _ in self.fields['modules'].choices}
            self.fields['modules'].initial = [
                code for code in self.instance.modules if code in offered]
        for name, field in self.fields.items():
            if not isinstance(field.widget, forms.CheckboxInput) and not isinstance(
                    field.widget, forms.CheckboxSelectMultiple):
                css = field.widget.attrs.get('class', '')
                field.widget.attrs['class'] = f'{css} field'.strip()

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if _role_code(name) in UserProfile.Role.values:
            raise forms.ValidationError('That name is reserved for a built-in role.')
        return name

    def clean_modules(self):
        modules = self.cleaned_data['modules']
        if not modules:
            raise forms.ValidationError('Choose at least one module.')
        return modules

    def save(self, commit=True):
        role = super().save(commit=False)
        role.modules = list(self.cleaned_data['modules'])
        if not role.pk:
            role.code = _unique_role_code(self.cleaned_data['name'])
        if commit:
            role.save()
        return role


class UserEditForm(BaseUserForm):
    new_password = forms.CharField(required=False, min_length=8, label='New password',
                                   widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
                                   help_text='Leave blank to keep the current password.')
    new_password_confirm = forms.CharField(required=False, label='Confirm new password',
                                           widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_active'].help_text = 'Inactive users cannot sign in.'
        self.order_fields(['username', 'first_name', 'last_name', 'email', 'role', 'is_active',
                           'new_password', 'new_password_confirm'])

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('new_password') != cleaned.get('new_password_confirm'):
            self.add_error('new_password_confirm', 'The two passwords do not match.')
        return cleaned


class ResetIdentifierForm(forms.Form):
    identifier = forms.CharField(
        label='Username or email', max_length=254,
        widget=forms.TextInput(attrs={
            'autofocus': True, 'autocomplete': 'username',
            'placeholder': 'Username or work/personal email',
        }))

    def clean_identifier(self):
        return self.cleaned_data['identifier'].strip()


class ResetChannelForm(forms.Form):
    channel = forms.ChoiceField(label='Send the code to', widget=forms.RadioSelect, choices=[])

    def __init__(self, *args, channels=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['channel'].choices = [(item['code'], item['label']) for item in channels]


class ResetPasswordForm(forms.Form):
    code = forms.CharField(
        label='Verification code', min_length=6, max_length=6,
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code', 'inputmode': 'numeric',
            'placeholder': '6-digit code',
        }))
    password = forms.CharField(
        label='New password', min_length=8,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password_confirm = forms.CharField(
        label='Confirm new password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = f"{field.widget.attrs.get('class', '')} field".strip()

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not code.isdigit():
            raise forms.ValidationError('Enter the 6-digit code from the email.')
        return code

    def clean(self):
        cleaned = super().clean()
        password, confirm = cleaned.get('password'), cleaned.get('password_confirm')
        if password and confirm != password:
            self.add_error('password_confirm', 'The two passwords do not match.')
        if password and self.user:
            try:
                validate_password(password, self.user)
            except ValidationError as exc:
                self.add_error('password', exc)
        return cleaned
