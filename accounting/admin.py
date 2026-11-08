"""Django admin for accounting. Models without a hand-written admin get a simple generated one
(see invoice_app/admin_auto.py: sensitive numbers masked, files not linked, history read-only)."""
from invoice_app.admin_auto import register_all

register_all('accounting', read_only=('JournalEntry', 'JournalLine', 'Sequence', 'PeriodEvent',))
