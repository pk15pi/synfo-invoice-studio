from django.urls import path

from . import views

app_name = 'reporting'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('section/<slug:slug>/', views.section, name='section'),
    path('r/<slug:key>/', views.report, name='report'),
    path('custom/', views.custom, name='custom'),
    path('fy-workbook/', views.fy_workbook, name='fy_workbook'),
    path('sources/', views.sources, name='sources'),
]
