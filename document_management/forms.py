from django import forms

from .models import Document


class UploadForm(forms.Form):
    category = forms.ChoiceField(choices=[c for c in Document.CATEGORIES if c[0] in Document.COMPANY_CATEGORIES])
    title = forms.CharField(max_length=200)
    sensitivity = forms.ChoiceField(choices=Document.SENSITIVITY, initial=Document.CONFIDENTIAL)
    tags = forms.CharField(max_length=240, required=False)
    file = forms.FileField(help_text='PDF, JPG or PNG, up to 10 MB.')


class EditForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'sensitivity', 'tags']
