from django.urls import path

from . import views
from . import views_daily_brief

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
    path('daily-brief/', views_daily_brief.daily_brief_records, name='daily_brief_records'),
    path('daily-brief/analytics/', views_daily_brief.daily_brief_analytics_page, name='daily_brief_analytics'),
    path('daily-brief/<int:pk>/', views_daily_brief.daily_brief_detail, name='daily_brief_detail'),
    path('daily-brief/<int:pk>/edit/', views_daily_brief.daily_brief_edit, name='daily_brief_edit'),
]
