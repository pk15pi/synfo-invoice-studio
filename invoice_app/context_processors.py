from .models import BusinessProfile


def brand_name(request):
    name = BusinessProfile.objects.filter(pk=1).values_list('name', flat=True).first()
    return {'brand_name': name or 'QuantBud'}
