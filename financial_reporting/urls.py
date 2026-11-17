from django.urls import path

from . import views

app_name = 'financial_reporting'
urlpatterns = [
    # --- existing ---
    path('', views.dashboard, name='dashboard'),
    path('catalogue/', views.catalogue, name='catalogue'),

    # --- report runs ---
    path('runs/', views.report_run_list, name='report_run_list'),
    path('runs/new/', views.run_report, name='run_report'),
    path('runs/<int:pk>/', views.report_run_detail, name='report_run_detail'),
    path('runs/<int:pk>/download/', views.report_run_download, name='report_run_download'),

    # --- export packages ---
    path('packages/', views.package_list, name='package_list'),
    path('packages/<int:pk>/', views.package_detail, name='package_detail'),
    path('packages/<int:pk>/generate/', views.package_generate, name='package_generate'),
    path('packages/<int:pk>/download/', views.package_download, name='package_download'),

    # --- reconciliations ---
    path('reconciliations/', views.reconciliation_list, name='reconciliation_list'),

    # --- audit checklist ---
    path('audit-checklist/', views.audit_checklist, name='audit_checklist'),

    # --- compliance submissions ---
    path('compliance/', views.compliance_submissions, name='compliance_submissions'),
    path('compliance/<int:pk>/edit/', views.compliance_submission_edit, name='compliance_submission_edit'),
]
