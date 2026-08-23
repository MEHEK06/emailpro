from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.forms import CampaignForm
from core.models import Campaign, CampaignRecipient, Contact
from core.tasks import send_campaign


class SegmentSelectionTests(TestCase):
    def setUp(self):
        Contact.objects.create(email="biz@example.com", category=Contact.Category.BUSINESS)
        Contact.objects.create(email="ind@example.com", category=Contact.Category.INDIVIDUAL)
        Contact.objects.create(email="unc@example.com", category=Contact.Category.UNCLASSIFIED)

    def test_business_segment_selects_only_business(self):
        c = Campaign.objects.create(subject="s", body_html="<p>hi</p>", segment=Campaign.Segment.BUSINESS)
        emails = set(c.queryset_for_segment().values_list("email", flat=True))
        self.assertEqual(emails, {"biz@example.com"})

    def test_individual_segment_selects_only_individual(self):
        c = Campaign.objects.create(subject="s", body_html="<p>hi</p>", segment=Campaign.Segment.INDIVIDUAL)
        emails = set(c.queryset_for_segment().values_list("email", flat=True))
        self.assertEqual(emails, {"ind@example.com"})

    def test_all_segment_selects_everyone(self):
        c = Campaign.objects.create(subject="s", body_html="<p>hi</p>", segment=Campaign.Segment.ALL)
        emails = set(c.queryset_for_segment().values_list("email", flat=True))
        self.assertEqual(emails, {"biz@example.com", "ind@example.com", "unc@example.com"})


class AttachmentValidationTests(TestCase):
    @override_settings(MAX_ATTACHMENT_SIZE_MB=1)
    def test_oversized_attachment_is_rejected(self):
        big_file = SimpleUploadedFile("big.txt", b"x" * (2 * 1024 * 1024))
        form = CampaignForm(
            data={"subject": "s", "body_html": "<p>hi</p>", "body_text": "", "segment": "all"},
            files={"attachment": big_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("attachment", form.errors)

    def test_reasonable_attachment_is_accepted(self):
        small_file = SimpleUploadedFile("small.txt", b"hello")
        form = CampaignForm(
            data={"subject": "s", "body_html": "<p>hi</p>", "body_text": "", "segment": "all"},
            files={"attachment": small_file},
        )
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret")
class CampaignConfirmationFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.user)
        Contact.objects.create(email="a@example.com", category=Contact.Category.BUSINESS)
        Contact.objects.create(email="b@example.com", category=Contact.Category.BUSINESS)

    def test_confirm_requires_explicit_post_and_creates_recipients(self):
        campaign = Campaign.objects.create(
            subject="Hello", body_html="<p>hi</p>", segment=Campaign.Segment.BUSINESS
        )
        # GET should just show the review page, no recipients created yet.
        resp = self.client.get(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 0)

        with patch("core.tasks.send_campaign.delay") as mock_delay:
            resp = self.client.post(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.QUEUED)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 2)
        mock_delay.assert_called_once_with(campaign.pk)

    def test_confirm_blocked_without_gmail_configured(self):
        campaign = Campaign.objects.create(
            subject="Hello", body_html="<p>hi</p>", segment=Campaign.Segment.BUSINESS
        )
        with override_settings(GMAIL_ADDRESS="", GMAIL_APP_PASSWORD=""):
            resp = self.client.post(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 0)

    def test_confirm_twice_does_not_duplicate_recipients(self):
        campaign = Campaign.objects.create(
            subject="Hello", body_html="<p>hi</p>", segment=Campaign.Segment.BUSINESS
        )
        with patch("core.tasks.send_campaign.delay"):
            self.client.post(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 2)

        # Campaign is no longer in DRAFT status, so the confirm view 404s on
        # a second attempt via the URL (get_object_or_404 filters by DRAFT) —
        # this is the primary guard against re-confirming an already-queued
        # campaign from the UI.
        resp = self.client.post(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 2)


class DuplicateSendProtectionTests(TestCase):
    def test_unique_constraint_prevents_duplicate_recipient_rows(self):
        contact = Contact.objects.create(email="a@example.com")
        campaign = Campaign.objects.create(subject="s", body_html="<p>hi</p>")
        CampaignRecipient.objects.create(campaign=campaign, contact=contact, email=contact.email)

        with self.assertRaises(Exception):
            CampaignRecipient.objects.create(campaign=campaign, contact=contact, email=contact.email)


@override_settings(GMAIL_ADDRESS="pilot@example.com", GMAIL_APP_PASSWORD="secret", SEND_RATE_PER_MINUTE=600)
class ThrottledSendTaskTests(TestCase):
    def test_send_campaign_only_processes_queued_recipients_and_skips_others(self):
        c1 = Contact.objects.create(email="one@example.com")
        c2 = Contact.objects.create(email="two@example.com")
        campaign = Campaign.objects.create(
            subject="s", body_html="<p>hi</p>", status=Campaign.Status.QUEUED
        )
        CampaignRecipient.objects.create(campaign=campaign, contact=c1, email=c1.email)
        already_sent = CampaignRecipient.objects.create(
            campaign=campaign, contact=c2, email=c2.email, status=CampaignRecipient.Status.SENT
        )

        with patch("core.services.mailer.send_single_email", return_value=True) as mock_send:
            with patch("core.services.mailer.throttle_delay_seconds", return_value=0):
                send_campaign(campaign.pk)

        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], "one@example.com")

        campaign.refresh_from_db()
        self.assertEqual(campaign.sent_count, 2)  # one new + the pre-existing sent one
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)
