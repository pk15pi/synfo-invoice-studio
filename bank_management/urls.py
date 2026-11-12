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
    p('imports/<int:pk>/review/', views.import_review, 'import_review'),
    p('imports/<int:pk>/accounting-review/', views.import_migration, 'import_migration'),
    p('imports/<int:pk>/csv/', views.import_csv_download, 'import_csv_download'),
    p('imports/<int:pk>/source/', views.import_source_download, 'import_source_download'),
    p('imports/<int:pk>/regenerate/', views.import_regenerate, 'import_regenerate'),
    p('imports/<int:pk>/cancel/', views.import_cancel, 'import_cancel'),
    p('imports/<int:pk>/delete/', views.import_delete, 'import_delete'),
    p('imports/<int:pk>/draft-rows/', views.import_draft_rows, 'import_draft_rows'),
    p('imports/<int:pk>/draft-rows/<int:row_pk>/edit/', views.import_draft_row_edit, 'import_draft_row_edit'),
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
