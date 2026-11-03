from django.urls import path

from . import views

app_name = 'audit'
urlpatterns = [
    path('', views.log, name='log'),
    path('export.csv', views.export_csv, name='export'),
    path('object/<str:app_label>/<str:model>/<str:object_id>/', views.history, name='history'),
]
