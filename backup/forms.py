from django import forms


class CreateForm(forms.Form):
    note = forms.CharField(max_length=300, required=False)


class RestoreForm(forms.Form):
    phrase = forms.CharField(max_length=200, label='Type the confirmation phrase')
    understood = forms.BooleanField(label='I understand the current database will be replaced (a pre-restore backup '
                                          'is made first) and that runserver must be restarted afterwards.')


class ImportPackageForm(forms.Form):
    package = forms.FileField(label='Export package (.zip)')

    def clean_package(self):
        package = self.cleaned_data['package']
        from .consolidation import MAX_UPLOAD_BYTES
        if not package.name.lower().endswith('.zip'):
            raise forms.ValidationError('Choose a .zip export package.')
        if package.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(f'Package must be no larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.')
        return package


class MergeConfirmForm(forms.Form):
    confirm = forms.BooleanField(
        label='I reviewed the preview and confirm this additive merge. Existing data will not be overwritten.',
    )
    keep_destination = forms.BooleanField(
        required=False,
        label='For changed source records, retain the destination version and leave their incoming versions in this package.',
    )


class PgSettingsForm(forms.Form):
    host = forms.CharField(max_length=255, label='Host')
    port = forms.IntegerField(min_value=1, max_value=65535, initial=5432, label='Port')
    name = forms.CharField(max_length=128, label='Database name')
    user = forms.CharField(max_length=128, label='User')
    password = forms.CharField(max_length=512, required=False, label='Password',
                               widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
                               help_text='Leave empty to keep the saved password.')
    clear_password = forms.BooleanField(required=False, label='Clear saved password')
    sslmode = forms.ChoiceField(label='SSL mode', initial='prefer',
                                choices=[(m, m) for m in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full')])


class PgSyncForm(forms.Form):
    phrase = forms.CharField(max_length=40, label='Type the confirmation phrase')
    understood = forms.BooleanField(label='I understand the destination will be overwritten.')
    replace_live = forms.BooleanField(required=False, label='Also replace the live SQLite database with the clone '
                                      '(a verified ZIP backup is taken first; restart run_windows.bat afterwards)')
