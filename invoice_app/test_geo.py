"""Customer address lookups (t59u): Country / State / District tables, India default, cascade rules."""
from django.test import TestCase
from django.urls import reverse

from invoice_app import geo
from invoice_app.forms import ClientForm
from invoice_app.models import Client, Country, District, State


def base(**kw):
    d = {'name': 'Geo Co', 'address': '1 Road', 'billing_type': 'monthly_prorated', 'terms_mode': 'custom',
         'tax_treatment': 'export'}
    d.update(kw)
    return d


class GeoSeedTests(TestCase):
    def test_seeded_counts_and_known_rows(self):
        self.assertGreaterEqual(Country.objects.count(), 249)
        india = Country.objects.get(iso2='IN')
        self.assertEqual(india.name, 'India')
        self.assertEqual(State.objects.filter(country=india).count(), 36)
        self.assertGreaterEqual(District.objects.filter(state__country=india).count(), 780)
        hr = State.objects.get(country=india, name='Haryana')
        self.assertTrue(District.objects.filter(state=hr, name='Gurugram').exists())
        self.assertTrue(Country.objects.filter(iso2='US').exists())
        self.assertTrue(State.objects.filter(country__iso2='US', name='California').exists())

    def test_load_is_idempotent(self):
        before = (Country.objects.count(), State.objects.count(), District.objects.count())
        geo.load(Country, State, District)
        self.assertEqual(before, (Country.objects.count(), State.objects.count(), District.objects.count()))


class ClientAddressFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.india = Country.objects.get(iso2='IN')
        cls.us = Country.objects.get(iso2='US')
        cls.hr = State.objects.get(country=cls.india, name='Haryana')
        cls.gurugram = District.objects.get(state=cls.hr, name='Gurugram')
        cls.ca = State.objects.get(country=cls.us, name='California')

    def test_add_defaults_to_india_and_page_renders(self):
        self.assertEqual(ClientForm().initial.get('country_ref'), self.india.pk)
        r = self.client.get(reverse('client_add'))
        self.assertContains(r, f'<option value="{self.india.pk}" selected>India</option>', html=False)
        self.assertContains(r, 'data-combo="country"')
        self.assertContains(r, 'class="customer-address-block"')
        self.assertContains(r, 'Haryana')           # India's states are offered on Add
        self.assertNotContains(r, '>California<')   # other countries' states are not

    def test_country_required(self):
        f = ClientForm(data=base(state_ref=self.hr.pk))
        self.assertFalse(f.is_valid())
        self.assertIn('country_ref', f.errors)

    def test_state_required_for_india_only(self):
        f = ClientForm(data=base(country_ref=self.india.pk))
        self.assertFalse(f.is_valid())
        self.assertIn('state_ref', f.errors)
        f = ClientForm(data=base(country_ref=self.us.pk))
        self.assertTrue(f.is_valid(), f.errors)

    def test_district_and_pin_optional_and_saved(self):
        f = ClientForm(data=base(country_ref=self.india.pk, state_ref=self.hr.pk))
        self.assertTrue(f.is_valid(), f.errors)
        f = ClientForm(data=base(country_ref=self.india.pk, state_ref=self.hr.pk, district_ref=self.gurugram.pk,
                                 pin_code=' 122001 '))
        self.assertTrue(f.is_valid(), f.errors)
        c = f.save()
        self.assertEqual((c.country, c.state_ref, c.district_ref, c.pin_code), ('India', self.hr, self.gurugram, '122001'))
        # PIN format is a hint only; a non-6-digit value does not block save.
        self.assertTrue(ClientForm(data=base(country_ref=self.india.pk, state_ref=self.hr.pk, pin_code='12AB')).is_valid())

    def test_invalid_or_mismatched_values_rejected(self):
        for data, field in [
            (base(country_ref='99999', state_ref=self.hr.pk), 'country_ref'),
            (base(country_ref='Wakanda'), 'country_ref'),
            (base(country_ref=self.india.pk, state_ref='Atlantis'), 'state_ref'),
            (base(country_ref=self.india.pk, state_ref=self.ca.pk), 'state_ref'),
            (base(country_ref=self.us.pk, state_ref=self.ca.pk, district_ref=self.gurugram.pk), 'district_ref'),
            (base(country_ref=self.india.pk, state_ref=self.hr.pk, district_ref='Nowhere'), 'district_ref'),
            (base(country_ref=self.us.pk, district_ref=self.gurugram.pk), 'district_ref'),
        ]:
            f = ClientForm(data=data)
            self.assertFalse(f.is_valid(), data)
            self.assertIn(field, f.errors, data)

    def test_cascade_endpoints(self):
        r = self.client.get(reverse('geo_states'), {'country': self.india.pk}).json()['options']
        self.assertIn([self.hr.pk, 'Haryana'], r)
        r = self.client.get(reverse('geo_districts'), {'state': self.hr.pk}).json()['options']
        self.assertIn([self.gurugram.pk, 'Gurugram'], r)
        self.assertEqual(self.client.get(reverse('geo_states'), {'country': 'x'}).json()['options'], [])

    def test_edit_page_shows_saved_values(self):
        c = Client.objects.create(name='E', country_ref=self.india, state_ref=self.hr, district_ref=self.gurugram)
        r = self.client.get(reverse('client_edit', args=[c.pk]))
        self.assertContains(r, f'<option value="{self.gurugram.pk}" selected>Gurugram</option>', html=False)
        self.assertContains(self.client.get(reverse('client_detail', args=[c.pk])), 'Gurugram')


class LegacyCountryMappingTests(TestCase):
    def test_mapping_keeps_records(self):
        a = Client.objects.create(name='A', address='Line 1\nLine 2', country='IND', notes='keep', phone='123')
        b = Client.objects.create(name='B', country='U.S.A.')
        c = Client.objects.create(name='C', country='Narnia')
        d = Client.objects.create(name='D', country='', gstin='06AABFQ2999K1ZQ')
        Client.objects.filter(pk__in=[a.pk, b.pk, c.pk, d.pk]).update(country_ref=None)
        geo.map_clients(Client, Country, State)
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db(); d.refresh_from_db()
        self.assertEqual(a.country_ref.iso2, 'IN')
        self.assertEqual((a.address, a.notes, a.phone, a.country), ('Line 1\nLine 2', 'keep', '123', 'IND'))
        self.assertEqual(b.country_ref.iso2, 'US')
        self.assertIsNone(c.country_ref)
        self.assertEqual(c.country, 'Narnia')
        self.assertEqual(d.country_ref.iso2, 'IN')
        self.assertEqual(d.state_ref.name, 'Haryana')  # from the GSTIN state name