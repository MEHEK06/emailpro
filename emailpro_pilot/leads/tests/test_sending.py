import smtplib
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from leads.models import Campaign, EmailDelivery, EmailTemplate, Lead
from leads.services import mailer
from leads.tasks import send_lead_delivery


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class MailerRetryTests(TestCase):
    @patch("leads.services.mailer.EmailMultiAlternatives")
    @patch("leads.services.mailer.time.sleep", return_value=None)
    def test_transient_failure_then_success(self, mock_sleep, mock_message_cls):
        message = MagicMock()
        message.send.side_effect = [smtplib.SMTPServerDisconnected("dropped"), None]
        mock_message_cls.return_value = message

        result = mailer.send_lead_email("s", "<p>hi</p>", "hi", "a@example.com")
        self.assertTrue(result)
        self.assertEqual(message.send.call_count, 2)

    @patch("leads.services.mailer.EmailMultiAlternatives")
    def test_permanent_failure_raises_immediately(self, mock_message_cls):
        message = MagicMock()
        message.send.side_effect = smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")})
        mock_message_cls.return_value = message

        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            mailer.send_lead_email("s", "<p>hi</p>", "hi", "a@example.com")
        self.assertEqual(message.send.call_count, 1)

    @override_settings(GMAIL_ADDRESS="", GMAIL_APP_PASSWORD="")
    def test_not_configured_raises(self):
        with self.assertRaises(mailer.MailerNotConfigured):
            mailer.send_lead_email("s", "<p>hi</p>", "", "a@example.com")


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class SendLeadDeliveryTaskTests(TestCase):
    def setUp(self):
        self.template = EmailTemplate.get_current()
        self.template.subject = "Hello"
        self.template.html_body = "<p>Hi {{ownerName}}, unsub: {{unsubscribeUrl}}</p>"
        self.template.save()

    def _make_campaign_and_delivery(self, lead, is_bulk=True):
        campaign = Campaign.objects.create(
            subject=self.template.subject,
            html_body=self.template.html_body,
            is_bulk=is_bulk,
            recipient_count=1,
            status=Campaign.Status.QUEUED,
        )
        delivery = EmailDelivery.objects.create(campaign=campaign, lead=lead, email=lead.email)
        return campaign, delivery

    def test_successful_send_marks_delivery_sent_and_lead_contacted(self):
        lead = Lead.objects.create(email="a@example.com", owner_name="Jane")
        campaign, delivery = self._make_campaign_and_delivery(lead)

        with patch("leads.services.mailer.send_lead_email", return_value=True) as mock_send:
            send_lead_delivery(delivery.pk)

        mock_send.assert_called_once()
        delivery.refresh_from_db()
        lead.refresh_from_db()
        campaign.refresh_from_db()

        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)
        self.assertIsNotNone(delivery.sent_at)
        self.assertTrue(lead.contacted)
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)

    def test_failed_send_marks_delivery_failed_and_records_error(self):
        lead = Lead.objects.create(email="a@example.com")
        campaign, delivery = self._make_campaign_and_delivery(lead)

        with patch(
            "leads.services.mailer.send_lead_email",
            side_effect=smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")}),
        ):
            send_lead_delivery(delivery.pk)

        delivery.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)
        self.assertTrue(delivery.error_message)
        self.assertEqual(campaign.failed_count, 1)
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)

    def test_unsubscribed_lead_is_skipped_even_if_queued(self):
        lead = Lead.objects.create(email="a@example.com", unsubscribed=True)
        campaign, delivery = self._make_campaign_and_delivery(lead)

        with patch("leads.services.mailer.send_lead_email") as mock_send:
            send_lead_delivery(delivery.pk)

        mock_send.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)

    def test_already_processed_delivery_is_not_resent(self):
        lead = Lead.objects.create(email="a@example.com")
        campaign, delivery = self._make_campaign_and_delivery(lead)
        delivery.mark_sent()

        with patch("leads.services.mailer.send_lead_email") as mock_send:
            send_lead_delivery(delivery.pk)

        mock_send.assert_not_called()


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class IndividualSendViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.user)
        template = EmailTemplate.get_current()
        template.subject = "Hi"
        template.html_body = "<p>Hi {{ownerName}}</p>"
        template.save()

    def test_send_individual_creates_single_recipient_campaign(self):
        lead = Lead.objects.create(email="a@example.com")
        with patch("leads.tasks.send_lead_delivery.delay") as mock_delay:
            resp = self.client.post(reverse("leads:send_individual", args=[lead.pk]))
        self.assertEqual(resp.status_code, 302)
        campaign = Campaign.objects.latest("created_at")
        self.assertFalse(campaign.is_bulk)
        self.assertEqual(campaign.recipient_count, 1)
        self.assertEqual(EmailDelivery.objects.filter(campaign=campaign).count(), 1)
        mock_delay.assert_called_once()

    def test_send_individual_blocked_for_unsubscribed_lead(self):
        lead = Lead.objects.create(email="a@example.com", unsubscribed=True)
        with patch("leads.tasks.send_lead_delivery.delay") as mock_delay:
            resp = self.client.post(reverse("leads:send_individual", args=[lead.pk]))
        self.assertEqual(resp.status_code, 302)
        mock_delay.assert_not_called()
        self.assertEqual(Campaign.objects.count(), 0)

    def test_send_individual_requires_post(self):
        lead = Lead.objects.create(email="a@example.com")
        resp = self.client.get(reverse("leads:send_individual", args=[lead.pk]))
        self.assertEqual(resp.status_code, 405)


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class BulkCampaignConfirmationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.user)
        template = EmailTemplate.get_current()
        template.subject = "Hi"
        template.html_body = "<p>Hi {{ownerName}}</p>"
        template.save()

        self.sendable = Lead.objects.create(email="ok@example.com", contacted=False, unsubscribed=False)
        self.contacted = Lead.objects.create(email="already@example.com", contacted=True)
        self.unsubscribed = Lead.objects.create(email="opted-out@example.com", unsubscribed=True)

    def test_get_preview_does_not_create_campaign(self):
        resp = self.client.get(reverse("leads:campaign_preview"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Campaign.objects.count(), 0)
        # Default target excludes already-contacted and unsubscribed leads.
        self.assertEqual(resp.context["recipient_count"], 1)

    def test_post_confirms_and_creates_campaign_with_deliveries(self):
        with patch("leads.tasks.send_lead_delivery.delay") as mock_delay:
            resp = self.client.post(reverse("leads:campaign_preview"))
        self.assertEqual(resp.status_code, 302)
        campaign = Campaign.objects.latest("created_at")
        self.assertTrue(campaign.is_bulk)
        self.assertEqual(campaign.recipient_count, 1)
        self.assertEqual(
            list(EmailDelivery.objects.filter(campaign=campaign).values_list("email", flat=True)),
            ["ok@example.com"],
        )
        mock_delay.assert_called_once()

    def test_explicit_selection_still_excludes_unsubscribed(self):
        lead_ids = f"{self.sendable.pk},{self.unsubscribed.pk}"
        resp = self.client.get(reverse("leads:campaign_preview"), {"lead_ids": lead_ids})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["recipient_count"], 1)

    def test_bulk_send_blocked_without_smtp_configured(self):
        with override_settings(GMAIL_ADDRESS="", GMAIL_APP_PASSWORD=""):
            resp = self.client.post(reverse("leads:campaign_preview"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Campaign.objects.count(), 0)


class UnsubscribeExclusionTests(TestCase):
    def test_default_bulk_target_excludes_unsubscribed_and_contacted(self):
        from leads.views import _default_bulk_target

        Lead.objects.create(email="a@example.com")
        Lead.objects.create(email="b@example.com", unsubscribed=True)
        Lead.objects.create(email="c@example.com", contacted=True)

        target_emails = set(_default_bulk_target().values_list("email", flat=True))
        self.assertEqual(target_emails, {"a@example.com"})
