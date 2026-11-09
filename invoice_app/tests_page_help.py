from django.test import TestCase
from django.urls import reverse

from .page_help import NON_PAGE_ROUTES, PAGE_GUIDES


class ApplicationPageHelpTests(TestCase):
    def test_page_guides_are_complete_and_non_generic(self):
        self.assertGreaterEqual(len(PAGE_GUIDES), 100)
        for route_name, guide in PAGE_GUIDES.items():
            with self.subTest(route=route_name):
                self.assertTrue(guide["title"])
                self.assertTrue(guide["overview"])
                self.assertTrue(guide["purpose"])
                self.assertTrue(guide["steps"])
        self.assertNotEqual(PAGE_GUIDES["bank:imports"]["overview"], PAGE_GUIDES["clients"]["overview"])

    def test_every_named_application_route_is_a_page_guide_or_documented_non_page(self):
        from django.urls import URLPattern, URLResolver, get_resolver

        def route_names(patterns, namespace=""):
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    child_namespace = pattern.namespace or pattern.app_name or namespace
                    yield from route_names(pattern.url_patterns, child_namespace)
                elif isinstance(pattern, URLPattern) and pattern.name:
                    name = f"{namespace}:{pattern.name}" if namespace else pattern.name
                    yield name

        app_routes = {name for name in route_names(get_resolver().url_patterns) if not name.startswith("admin:")}
        missing = app_routes - set(PAGE_GUIDES) - NON_PAGE_ROUTES
        unknown_guides = set(PAGE_GUIDES) - app_routes

        self.assertEqual(missing, set())
        self.assertEqual(unknown_guides, set())

    def test_shared_shell_renders_route_specific_manual_and_modal(self):
        response = self.client.get(reverse("client_add"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_help_key"], "client_add")
        self.assertFalse(response.context["page_help_missing"])
        self.assertEqual(response.context["page_help_guide"], PAGE_GUIDES["client_add"])
        self.assertContains(response, 'id="qb-page-help"')
        self.assertContains(response, "Available buttons and actions")
        self.assertContains(response, ".qb-help-dialog")

    def test_each_manual_is_safely_serialized_for_the_frontend(self):
        response = self.client.get(reverse("bank:imports"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_help_key"], "bank:imports")
        body = response.content.decode()
        self.assertIn('id="qb-page-help-data"', body)
        self.assertIn("Extraction and bank import are separate stages", body)
        self.assertIn("possible duplicates and modifications need review before approval", body)
        self.assertIn("use Regenerate Draft to reprocess that exact retained upload", body)
        self.assertIn("Use Cancel Draft for an unimported version", body)

    def test_django_admin_pages_use_the_same_help_dialog(self):
        response = self.client.get("/admin/login/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_help_key"], "admin:login")
        self.assertEqual(response.context["page_help_guide"]["title"], "Administrator sign-in")
        self.assertContains(response, 'id="qb-page-help"')
        self.assertContains(response, "Available buttons and actions")

    def test_unknown_page_does_not_receive_another_pages_manual(self):
        from django.test import RequestFactory
        from invoice_app.context_processors import page_help

        request = RequestFactory().get("/not-registered/")
        request.resolver_match = type("ResolverMatch", (), {"view_name": "unknown:page"})()
        context = page_help(request)

        self.assertTrue(context["page_help_missing"])
        self.assertIsNone(context["page_help_guide"])


class ExpenseClaimHelpTitleTests(TestCase):
    def test_claim_add_and_review_help_titles(self):
        self.assertEqual(PAGE_GUIDES["exp:claim_new"]["title"], "Add Employee Claim")
        self.assertEqual(PAGE_GUIDES["exp:claim_edit"]["title"], "Add Employee Claim")
        self.assertEqual(PAGE_GUIDES["exp:review"]["title"], "Review Employee Claim")
