from django.urls import path

from . import views

app_name = 'financial_reporting'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('catalogue/', views.catalogue, name='catalogue'),
]
