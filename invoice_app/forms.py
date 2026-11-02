from django import forms
from .models import BusinessProfile, Client, ProjectTemplate
class StyledForm(forms.ModelForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class','field')
class BusinessProfileForm(StyledForm):
    class Meta:
        model=BusinessProfile
        exclude=[]
        widgets={'address':forms.Textarea(attrs={'rows':3}), 'primary_color':forms.TextInput(attrs={'type':'color'}), 'alternate_row_color':forms.TextInput(attrs={'type':'color'})}
class ClientForm(StyledForm):
    class Meta:
        model=Client
        fields='__all__'
        widgets={'address':forms.Textarea(attrs={'rows':3}),'notes':forms.Textarea(attrs={'rows':2})}
class ProjectTemplateForm(StyledForm):
    class Meta:
        model=ProjectTemplate
        exclude=['client']
