from django.test import TestCase, override_settings


class StaticFilesTests(TestCase):
    def assert_static_assets_are_served(self):
        css = self.client.get('/static/invoice_app/style.css')
        favicon = self.client.get('/static/invoice_app/favicon.ico')

        self.assertEqual(css.status_code, 200)
        self.assertIn(b'.payroll-heading', b''.join(css.streaming_content))
        self.assertEqual(favicon.status_code, 200)
        self.assertEqual(favicon['Content-Type'], 'image/x-icon')
        self.assertTrue(b''.join(favicon.streaming_content))

    @override_settings(DEBUG=False)
    def test_css_and_favicon_are_served_when_debug_is_disabled(self):
        self.assert_static_assets_are_served()

    @override_settings(DEBUG=True)
    def test_css_and_favicon_are_served_when_debug_is_enabled(self):
        self.assert_static_assets_are_served()
