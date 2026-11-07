from django.urls import path

from . import views

app_name = 'performance_review'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('activity/', views.activity_entry, name='activity'),
    path('records/', views.records, name='records'),
    path('reports/', views.reports, name='reports'),
    path('settings/', views.settings, name='settings'),
    path('settings/stages/<int:pk>/edit/', views.stage_edit, name='stage_edit'),
    path('applications/<int:pk>/edit/', views.application_edit, name='application_edit'),
    path('activities/<int:pk>/edit/', views.activity_edit, name='activity_edit'),
    path('jds/<int:pk>/edit/', views.jd_edit, name='jd_edit'),
]
