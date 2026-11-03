from django import forms

from accounting.models import Account

from .models import BankAccount


def _style(form):
    for f in form.fields.values():
        if not isinstance(f.widget, (forms.CheckboxInput, forms.RadioSelect)):
            f.widget.attrs.setdefault('class', 'field')


class BankAccountForm(forms.ModelForm):
    ledger_choice = forms.ModelChoiceField(
        queryset=Account.objects.none(), required=False, label='Ledger account',
        empty_label='Create a new sub-account under Cash and Bank automatically')

    class Meta:
        model = BankAccount
        fields = ['kind', 'name', 'bank_name', 'account_number', 'ifsc', 'branch', 'opening_balance', 'opening_date',
                  'active', 'is_default']
        widgets = {'opening_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        used = BankAccount.objects.exclude(pk=self.instance.pk).values_list('ledger_account_id', flat=True)
        self.fields['ledger_choice'].queryset = Account.objects.filter(account_type='asset', is_group=False, active=True).exclude(pk__in=used)
        if self.instance.pk:
            self.fields['ledger_choice'].initial = self.instance.ledger_account_id
            self.fields['ledger_choice'].required = True
            self.fields['ledger_choice'].empty_label = None
        _style(self)


class TransactionForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    value_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    description = forms.CharField(max_length=500)
    reference = forms.CharField(max_length=120, required=False)
    debit = forms.DecimalField(label='Withdrawal', required=False, min_value=0, decimal_places=2, max_digits=14)
    credit = forms.DecimalField(label='Deposit', required=False, min_value=0, decimal_places=2, max_digits=14)
    balance = forms.DecimalField(label='Statement balance', required=False, decimal_places=2, max_digits=14)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)


class TransferForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    from_account = forms.ModelChoiceField(BankAccount.objects.filter(active=True))
    to_account = forms.ModelChoiceField(BankAccount.objects.filter(active=True))
    amount = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=14)
    reference = forms.CharField(max_length=120, required=False)
    narration = forms.CharField(max_length=300, required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _style(self)


class ManualEntryForm(forms.Form):
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    direction = forms.ChoiceField(choices=[('out', 'Money out (e.g. bank charges)'), ('in', 'Money in (e.g. interest)')])
    amount = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=14)
    counter_account = forms.ModelChoiceField(Account.objects.none(), label='Post against account')
    narration = forms.CharField(max_length=300)

    def __init__(self, *a, bank=None, **kw):
        super().__init__(*a, **kw)
        qs = Account.objects.filter(active=True, is_group=False)
        if bank:
            qs = qs.exclude(pk=bank.ledger_account_id)
        self.fields['counter_account'].queryset = qs
        _style(self)


class UploadForm(forms.Form):
    file = forms.FileField(help_text='.csv or .xlsx bank statement')
