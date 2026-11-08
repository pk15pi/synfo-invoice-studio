"""Django admin for invoice_app. Models without a hand-written admin get a simple generated one
(see invoice_app/admin_auto.py: sensitive numbers masked, files not linked, history read-only)."""
from invoice_app.admin_auto import register_all

register_all('invoice_app', read_only=())
