from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from .models import UserProfile

ROLE_CHOICES = [('', 'No role assigned')] + list(UserProfile.Role.choices)


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
    role = forms.ChoiceField(choices=ROLE_CHOICES, required=False,
                             help_text='Controls which modules this user can open.')

    class Meta:
        model = get_user_model()
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {'email': forms.EmailInput(attrs={'autocomplete': 'email'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
