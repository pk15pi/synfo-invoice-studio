from django.urls import path

from employee_management.security import local_only

from . import views

app_name = 'bank'


def p(route, view, name):
    return path(route, local_only(view), name=name)


urlpatterns = [
    p('', views.accounts, 'accounts'),
    p('accounts/new/', views.account_form, 'account_new'),
    p('accounts/<int:pk>/', views.account_detail, 'account_detail'),
    p('accounts/<int:pk>/edit/', views.account_form, 'account_edit'),
    p('accounts/<int:pk>/transactions/', views.transactions, 'transactions'),
    p('accounts/<int:pk>/import/', views.import_upload, 'import_upload'),
    p('accounts/<int:pk>/entry/', views.manual_entry, 'manual_entry'),
    p('accounts/<int:pk>/reconciliation/', views.reconciliation, 'reconciliation'),
    p('imports/', views.imports, 'imports'),
    p('imports/<int:pk>/', views.import_batch, 'import_batch'),
    p('transactions/<int:pk>/', views.txn_detail, 'txn_detail'),
    p('transactions/<int:pk>/ignore/', views.txn_ignore, 'txn_ignore'),
    p('matches/<int:pk>/unmatch/', views.unmatch, 'unmatch'),
    p('transfers/', views.transfers, 'transfers'),
    p('book/<str:kind>/', views.book, 'book'),
    p('legacy-preview/', views.legacy_preview, 'legacy_preview'),
]
