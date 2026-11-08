from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Load / refresh Country, State and District lookups from invoice_app/data/geo_seed.json.'

    def handle(self, *args, **opts):
        from invoice_app import geo
        from invoice_app.models import Client, Country, District, State
        c, s, d = geo.load(Country, State, District)
        m = geo.map_clients(Client, Country, State)
        self.stdout.write(f'countries={c} india_states_uts={s} india_districts={d} customers_mapped={m}')