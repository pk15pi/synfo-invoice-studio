from django.urls import path

from . import views

app_name = 'backup'
urlpatterns = [
    path('', views.home, name='home'),
    path('create/', views.create, name='create'),
    path('retention/', views.retention, name='retention'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/verify/', views.verify, name='verify'),
]
