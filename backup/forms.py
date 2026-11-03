from django import forms


class CreateForm(forms.Form):
    note = forms.CharField(max_length=300, required=False)


class RestoreForm(forms.Form):
    phrase = forms.CharField(max_length=200, label='Type the confirmation phrase')
    understood = forms.BooleanField(label='I understand the current database will be replaced (a pre-restore backup '
                                          'is made first) and that runserver must be restarted afterwards.')
