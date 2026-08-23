import smtplib
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core.models import Campaign, CampaignRecipient, Contact
from core.services import mailer
from core.tasks import send_campaign


class MailerConfigurationTests(TestCase):
    @override_settings(GMAIL_ADDRESS="", GMAIL_APP_PASSWORD="")
    def test_not_configured_raises(self):
        with self.assertRaises(mailer.MailerNotConfigured):
            mailer.send_single_email("s", "<p>hi</p>", "", "a@example.com")

    @override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
    def test_configured_returns_true(self):
        self.assertTrue(mailer.is_configured())


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class SendSingleEmailRetryTests(TestCase):
    @patch("core.services.mailer.EmailMultiAlternatives")
    @patch("core.services.mailer.time.sleep", return_value=None)
    def test_transient_failure_then_success(self, mock_sleep, mock_message_cls):
        message = MagicMock()
        message.send.side_effect = [smtplib.SMTPServerDisconnected("dropped"), None]
        mock_message_cls.return_value = message

        result = mailer.send_single_email("s", "<p>hi</p>", "hi", "a@example.com")
        self.assertTrue(result)
        self.assertEqual(message.send.call_count, 2)

    @patch("core.services.mailer.EmailMultiAlternatives")
    def test_permanent_failure_raises_immediately(self, mock_message_cls):
        message = MagicMock()
        message.send.side_effect = smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")})
        mock_message_cls.return_value = message

        with self.assertRaises(smtplib.SMTPRecipientsRefused):
            mailer.send_single_email("s", "<p>hi</p>", "hi", "a@example.com")
        self.assertEqual(message.send.call_count, 1)

    @patch("core.services.mailer.EmailMultiAlternatives")
    @patch("core.services.mailer.time.sleep", return_value=None)
    def test_exhausted_retries_raise_last_error(self, mock_sleep, mock_message_cls):
        message = MagicMock()
        message.send.side_effect = smtplib.SMTPServerDisconnected("dropped")
        mock_message_cls.return_value = message

        with self.assertRaises(smtplib.SMTPServerDisconnected):
            mailer.send_single_email("s", "<p>hi</p>", "hi", "a@example.com")
        self.assertEqual(message.send.call_count, mailer.TRANSIENT_RETRIES)


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret", SEND_RATE_PER_MINUTE=600)
class CampaignReportAccuracyTests(TestCase):
    def test_mixed_success_and_failure_produce_accurate_report_totals(self):
        contacts = [Contact.objects.create(email=f"c{i}@example.com") for i in range(4)]
        campaign = Campaign.objects.create(subject="s", body_html="<p>hi</p>", status=Campaign.Status.QUEUED)
        for c in contacts:
            CampaignRecipient.objects.create(campaign=campaign, contact=c, email=c.email)

        def fake_send(**kwargs):
            if kwargs["to_email"] in ("c0@example.com", "c2@example.com"):
                return True
            raise smtplib.SMTPRecipientsRefused({kwargs["to_email"]: (550, b"no")})

        with patch("core.services.mailer.send_single_email", side_effect=fake_send):
            with patch("core.services.mailer.throttle_delay_seconds", return_value=0):
                send_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.sent_count, 2)
        self.assertEqual(campaign.failed_count, 2)
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)

        failed_emails = set(
            campaign.recipients.filter(status=CampaignRecipient.Status.FAILED).values_list(
                "email", flat=True
            )
        )
        self.assertEqual(failed_emails, {"c1@example.com", "c3@example.com"})
        for r in campaign.recipients.filter(status=CampaignRecipient.Status.FAILED):
            self.assertTrue(r.error_message)
