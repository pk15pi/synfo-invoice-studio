from django.db.models import Q
from django.shortcuts import render

from employee_management.security import local_only

from .models import Asset, AssetAssignment, AssetCategory, AssetLease, AssetMaintenance


@local_only
def dashboard(request):
    return render(request, 'asset_management/dashboard.html', {
        'asset_count': Asset.objects.count(),
        'available_count': Asset.objects.filter(current_status=Asset.AVAILABLE).count(),
        'assigned_count': AssetAssignment.objects.filter(status=AssetAssignment.ACTIVE).count(),
        'maintenance_count': AssetMaintenance.objects.filter(status__in=['reported', 'in_progress']).count(),
        'category_count': AssetCategory.objects.filter(is_active=True).count(),
    })


@local_only
def register(request):
    assets = Asset.objects.select_related('category', 'current_employee', 'current_location')
    search = request.GET.get('q', '').strip()[:100]
    if search:
        assets = assets.filter(
            Q(asset_code__icontains=search) | Q(name__icontains=search)
            | Q(serial_number__icontains=search) | Q(brand__icontains=search)
        )
    status = request.GET.get('status', '')
    if status in dict(Asset.STATUSES):
        assets = assets.filter(current_status=status)
    return render(request, 'asset_management/register.html', {'assets': assets, 'search': search})


@local_only
def placeholder(request, section):
    titles = {
        'new': 'Register an asset',
        'assignments': 'Asset assignments',
        'leases': 'Asset leases',
        'maintenance': 'Asset maintenance',
    }
    return render(request, 'asset_management/placeholder.html', {'title': titles[section]})
