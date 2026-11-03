from django.urls import path

from employee_management.security import local_only

from . import views

app_name = 'acc'


def p(route, view, name):
    return path(route, local_only(view), name=name)


urlpatterns = [
    p('', views.home, 'home'),
    p('accounts/', views.accounts, 'accounts'),
    p('accounts/new/', views.account_form, 'account_new'),
    p('accounts/<int:pk>/edit/', views.account_form, 'account_edit'),
    p('accounts/<int:pk>/ledger/', views.ledger_view, 'ledger'),
    p('ledger/', views.ledger_view, 'ledger_pick'),
    p('journal/', views.journal_list, 'journal'),
    p('journal/new/', views.journal_new, 'journal_new'),
    p('journal/<int:pk>/', views.journal_detail, 'journal_detail'),
    p('journal/<int:pk>/post/', views.journal_post, 'journal_post'),
    p('journal/<int:pk>/reverse/', views.journal_reverse, 'journal_reverse'),
    p('journal/<int:pk>/delete/', views.journal_delete, 'journal_delete'),
    p('periods/', views.periods, 'periods'),
    p('periods/<int:pk>/<str:action>/', views.period_action, 'period_action'),
    p('mappings/', views.mappings, 'mappings'),
]
