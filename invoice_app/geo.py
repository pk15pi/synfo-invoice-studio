"""Load Country / State / District lookup rows from invoice_app/data/geo_seed.json and map legacy
Client.country text to a Country row. Idempotent: safe to run again (used by migration 0018 and
`manage.py load_geo`)."""
import json
import re
from pathlib import Path

SEED = Path(__file__).resolve().parent / 'data' / 'geo_seed.json'

ALIASES = {
    'IN': {'india', 'in', 'ind', 'bharat', 'republic of india'},
    'US': {'usa', 'us', 'u.s.a', 'u.s', 'united states', 'united states of america', 'america'},
    'GB': {'uk', 'u.k', 'united kingdom', 'great britain', 'england', 'britain'},
    'AE': {'uae', 'u.a.e', 'united arab emirates'},
}


def _norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[.]+$', '', (text or '').strip().lower().replace('.', '.'))).strip(' .')


def load(Country, State, District):
    data = json.loads(SEED.read_text(encoding='utf-8'))
    by_iso = {}
    for c in data['countries']:
        obj, _ = Country.objects.update_or_create(iso2=c['iso2'], defaults={'iso3': c['iso3'], 'name': c['name']})
        by_iso[c['iso2']] = obj
    in_states = {}
    for s in data['states']:
        country = by_iso.get(s['country'])
        if not country:
            continue
        obj, _ = State.objects.update_or_create(country=country, name=s['name'], defaults={
            'code': s.get('code', '')[:20], 'lgd_code': s.get('lgd_code', ''), 'kind': s.get('type', '')[:60]})
        if s.get('lgd_code'):
            in_states[s['lgd_code']] = obj
    nd = 0
    for d in data['districts']:
        st = in_states.get(d['state_lgd'])
        if st:
            District.objects.update_or_create(state=st, name=d['name'], defaults={'lgd_code': d['lgd_code']})
            nd += 1
    return len(by_iso), len(in_states), nd


def match_country(Country, text):
    t = _norm(text)
    if not t:
        return None
    for iso, names in ALIASES.items():
        if t in names:
            return Country.objects.filter(iso2=iso).first()
    return (Country.objects.filter(name__iexact=t).first() or Country.objects.filter(iso2__iexact=t).first()
            or Country.objects.filter(iso3__iexact=t).first())


def map_clients(Client, Country, State):
    """Fill country_ref (and state_ref for India from the GST state name) where the text clearly matches.
    Never blanks or rewrites any other customer data; the legacy text stays as it was."""
    mapped = 0
    for c in Client.objects.filter(country_ref__isnull=True):
        country = match_country(Country, c.country)
        if not country and (c.gstin or c.state_code):
            country = Country.objects.filter(iso2='IN').first()  # GSTIN / Indian state code => India
        if not country:
            continue
        upd = {'country_ref': country}
        if country.iso2 == 'IN' and c.state_name and not c.state_ref_id:
            st = State.objects.filter(country=country, name__iexact=c.state_name.strip()).first()
            if st:
                upd['state_ref'] = st
        Client.objects.filter(pk=c.pk).update(**upd)  # queryset update: no save() side effects
        mapped += 1
    return mapped