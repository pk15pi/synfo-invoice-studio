"""Business logo + brand name (t63u) and customer brand-name alias."""
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from invoice_app.forms import BusinessProfileForm, ClientForm
from invoice_app.models import BusinessProfile, Client, Country

TMP = tempfile.mkdtemp(prefix='qb-logo-test-')


def png(name='logo.png'):
    buf = BytesIO()
    Image.new('RGBA', (8, 8), (11, 83, 148, 255)).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=TMP)
class BusinessLogoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def post_profile(self, files=None, **extra):
        p = BusinessProfile.current()
        data = {k: ('' if v is None else v) for k, v in BusinessProfileForm(instance=p).initial.items()
                if k not in ('signature_image', 'business_logo', 'id') and (not isinstance(v, bool) or v)}
        data.update(extra)
        data.update(files or {})
        return self.client.post(reverse('profile_edit'), data)

    def test_no_logo_means_no_img(self):
        r = self.client.get(reverse('home'))
        self.assertEqual(r.context['business_logo_url'], '')
        self.assertNotContains(r, 'class="brand-logo"')
        self.assertEqual(self.client.get(reverse('business_logo')).status_code, 404)

    def test_profile_saves_logo_and_brand_and_header_shows_it(self):
        r = self.post_profile({'business_logo': png()}, brand_name='QuantBud')
        self.assertRedirects(r, reverse('profile_edit'))
        p = BusinessProfile.current()
        self.assertEqual(p.brand_name, 'QuantBud')
        self.assertTrue(p.business_logo.name.startswith('branding/'))
        page = self.client.get(reverse('clients'))
        url = page.context['business_logo_url']
        self.assertTrue(url.startswith(reverse('business_logo')))
        self.assertContains(page, f'<img class="brand-logo" src="{url}"', html=False)
        logo = self.client.get(url)
        self.assertEqual((logo.status_code, logo['Content-Type']), (200, 'image/png'))
        # Saving the profile again without a new file keeps the logo.
        self.post_profile(brand_name='QB')
        self.assertTrue(BusinessProfile.current().business_logo)

    def test_rejects_bad_files(self):
        for f in [SimpleUploadedFile('x.gif', b'GIF89a....'), SimpleUploadedFile('x.png', b'not an image'),
                  SimpleUploadedFile('x.svg', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
                  SimpleUploadedFile('x.svg', b'<svg onload="alert(1)"></svg>'),
                  SimpleUploadedFile('big.png', b'\x89PNG\r\n\x1a\n' + b'0' * (3 * 1024 * 1024 + 1))]:
            r = self.post_profile({'business_logo': f})
            self.assertEqual(r.status_code, 200, f.name)
            self.assertIn('business_logo', r.context['form'].errors, f.name)

    def test_safe_svg_accepted(self):
        svg = SimpleUploadedFile('l.svg', b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#0B5394"/></svg>')
        self.assertRedirects(self.post_profile({'business_logo': svg}), reverse('profile_edit'))
        r = self.client.get(reverse('business_logo'))
        self.assertEqual(r['Content-Type'], 'image/svg+xml')
        self.assertIn("default-src 'none'", r['Content-Security-Policy'])


class ClientBrandAliasTests(TestCase):
    def setUp(self):
        self.us = Country.objects.get(iso2='US')

    def test_brand_name_saves_and_shows_as_alias(self):
        f = ClientForm(data={'name': 'Acme Industries Pvt Ltd', 'brand_name': 'Acme', 'country_ref': self.us.pk,
                             'billing_type': 'monthly_prorated', 'terms_mode': 'custom'})
        self.assertTrue(f.is_valid(), f.errors)
        c = f.save()
        self.assertEqual((c.name, c.brand_name, str(c)), ('Acme Industries Pvt Ltd', 'Acme', 'Acme Industries Pvt Ltd (Acme)'))
        lst = self.client.get(reverse('clients'))
        self.assertContains(lst, 'aka Acme')  # in the row text, so the type-to-filter search finds it
        self.assertContains(self.client.get(reverse('client_detail', args=[c.pk])), 'Brand name: <b>Acme</b>', html=False)
        self.assertContains(self.client.get(reverse('client_edit', args=[c.pk])), 'value="Acme"')

    def test_customer_without_brand_name_unchanged(self):
        c = Client.objects.create(name='Plain Co', country_ref=self.us)
        self.assertEqual((str(c), c.display_name), ('Plain Co', 'Plain Co'))
        r = self.client.get(reverse('client_detail', args=[c.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Brand name:')
        self.assertNotContains(self.client.get(reverse('clients')), 'aka ')