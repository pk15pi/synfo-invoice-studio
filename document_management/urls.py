from django.urls import path

from . import views

app_name = 'docs'
urlpatterns = [
    path('', views.doc_list, name='list'),
    path('upload/', views.upload, name='upload'),
    path('check/', views.check, name='check'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/download/', views.serve, name='serve'),
]
