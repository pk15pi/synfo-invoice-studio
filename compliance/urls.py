from django.urls import path

from . import views

app_name = 'compliance'
urlpatterns = [
    path('', views.home, name='home'),
    path('rules/', views.rules, name='rules'),
    path('rules/new/', views.rule_edit, name='rule_new'),
    path('rules/<int:pk>/', views.rule_edit, name='rule_edit'),
    path('check/', views.check, name='check'),
    path('gst/output/', views.gst_output, name='gst_output'),
    path('gst/input/', views.gst_input, name='gst_input'),
    path('gst/input/<int:pk>/<str:action>/', views.gst_input_toggle, name='gst_input_toggle'),
    path('tds/', views.tds, name='tds'),
    path('payroll/', views.payroll_stat, name='payroll'),
    path('payments/', views.payments, name='payments'),
    path('payments/<int:pk>/cancel/', views.payment_cancel, name='payment_cancel'),
    path('due-dates/', views.due_dates, name='due_dates'),
    path('due-dates/<int:pk>/', views.due_dates, name='due_date_edit'),
]
