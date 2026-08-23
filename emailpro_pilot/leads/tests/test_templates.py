from django.test import RequestFactory, TestCase

from leads.models import Lead
from leads.services import template_render, unsubscribe


class TemplateRenderingTests(TestCase):
    def test_all_documented_placeholders_are_substituted(self):
        html = (
            "<p>Hi {{ownerName}} from {{businessName}}, "
            "call {{whatsappNumber}} or {{unsubscribeUrl}}</p>"
        )
        out = template_render.render_template(
            html,
            owner_name="Jane",
            business_name="Acme",
            whatsapp_number="+1234",
            unsubscribe_url="https://example.com/u/abc",
        )
        self.assertIn("Jane", out)
        self.assertIn("Acme", out)
        self.assertIn("+1234", out)
        self.assertIn("https://example.com/u/abc", out)
        self.assertNotIn("{{", out)

    def test_undocumented_variable_is_left_untouched(self):
        html = "<p>{{ownerName}} {{someOtherVar}}</p>"
        out = template_render.render_template(
            html, owner_name="Jane", business_name="", whatsapp_number="", unsubscribe_url=""
        )
        self.assertIn("Jane", out)
        self.assertIn("{{someOtherVar}}", out)  # not a documented var — left as-is

    def test_lead_data_is_html_escaped(self):
        html = "<p>{{businessName}}</p>"
        out = template_render.render_template(
            html,
            owner_name="",
            business_name="<script>alert(1)</script>",
            whatsapp_number="",
            unsubscribe_url="",
        )
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_unsubscribe_url_is_not_escaped(self):
        # A real URL with & (e.g. query params) should not become &amp;
        html = "<a href='{{unsubscribeUrl}}'>Unsubscribe</a>"
        url = "https://example.com/u/abc?x=1&y=2"
        out = template_render.render_template(
            html, owner_name="", business_name="", whatsapp_number="", unsubscribe_url=url
        )
        self.assertIn(url, out)

    def test_plain_fallback_strips_tags(self):
        html = "<p>Hello</p><p>World</p><br>Extra"
        text = template_render.render_plain_fallback(html)
        self.assertNotIn("<", text)
        self.assertIn("Hello", text)
        self.assertIn("World", text)


class UnsubscribeTokenTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.lead = Lead.objects.create(email="a@example.com")

    def test_token_round_trips_to_correct_lead_id(self):
        token = unsubscribe.generate_unsubscribe_token(self.lead.pk)
        recovered_id = unsubscribe.verify_unsubscribe_token(token)
        self.assertEqual(recovered_id, self.lead.pk)

    def test_tampered_token_is_rejected(self):
        token = unsubscribe.generate_unsubscribe_token(self.lead.pk)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        with self.assertRaises(unsubscribe.InvalidUnsubscribeToken):
            unsubscribe.verify_unsubscribe_token(tampered)

    def test_build_url_with_request_uses_absolute_uri(self):
        request = self.factory.get("/")
        url = unsubscribe.build_unsubscribe_url(self.lead.pk, request=request)
        self.assertTrue(url.startswith("http://testserver/"))
        self.assertIn("/unsubscribe/", url)

    def test_build_url_without_request_uses_site_base_url_setting(self):
        url = unsubscribe.build_unsubscribe_url(self.lead.pk)
        self.assertIn("/unsubscribe/", url)
