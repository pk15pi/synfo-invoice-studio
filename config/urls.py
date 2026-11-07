from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from . import views as home_views

urlpatterns = [path('admin/', admin.site.urls), path('', home_views.home, name='home'), path('reports/', include('reporting.urls')), path('accounting/', include('accounting.urls')), path('bank/', include('bank_management.urls')), path('compliance/', include('compliance.urls')), path('doc-index/', include('document_management.urls')), path('audit/', include('audit.urls')), path('backups/', include('backup.urls')), path('assets/', include('asset_management.urls')), path('financial-reports/', include('financial_reporting.urls')), path('documents/', include('document_generation.urls')), path('performance/', include('performance_review.urls')), path('', include('employee_management.urls')), path('', include('attendance.urls')), path('', include('expense_management.urls')), path('', include('invoice_app.urls'))]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
