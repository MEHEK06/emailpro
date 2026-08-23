"""
End-to-end acceptance test mirroring the manual pilot walkthrough:
upload a small consented CSV, classify it, send a campaign to a test
inbox, and verify the resulting campaign report — all with Gemini and
Gmail mocked at their service boundaries (no real network calls).
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    Campaign,
    CampaignRecipient,
    ClassificationRun,
    Contact,
    ImportBatch,
)
from core.tasks import process_import_batch, run_classification, send_campaign


@override_settings(
    GEMINI_API_KEY="test-key",
    GMAIL_ADDRESS="pilot@example.com",
    GMAIL_APP_PASSWORD="secret",
    SEND_RATE_PER_MINUTE=600,
)
class EndToEndAcceptanceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_login(self.user)

    def test_full_pilot_workflow(self):
        # 1) Upload a small, consented pilot CSV.
        csv_content = (
            "email\n"
            "owner@acme-consulting.com\n"
            "jane.doe83@gmail.com\n"
            "not-a-valid-address\n"
            "owner@acme-consulting.com\n"  # duplicate within file
        )
        upload = SimpleUploadedFile("pilot.csv", csv_content.encode("utf-8"), content_type="text/csv")

        with patch("core.tasks.process_import_batch.delay") as mock_delay:
            resp = self.client.post(reverse("core:upload_csv"), {"file": upload})
        self.assertEqual(resp.status_code, 302)
        batch = ImportBatch.objects.latest("uploaded_at")
        mock_delay.assert_called_once_with(batch.pk)

        # Run the import task synchronously (as Celery eager mode would).
        process_import_batch(batch.pk)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatch.Status.COMPLETED)
        self.assertEqual(batch.total_rows, 4)
        self.assertEqual(batch.valid_count, 2)
        self.assertEqual(batch.invalid_count, 1)
        self.assertEqual(batch.duplicate_in_file_count, 1)
        self.assertEqual(batch.new_contacts_count, 2)
        self.assertEqual(Contact.objects.count(), 2)
        self.assertTrue(
            Contact.objects.filter(category=Contact.Category.UNCLASSIFIED).count() == 2
        )

        # 2) Classify the imported contacts.
        with patch("core.tasks.run_classification.delay") as mock_classify_delay:
            resp = self.client.post(reverse("core:start_classification"))
        self.assertEqual(resp.status_code, 302)
        run = ClassificationRun.objects.latest("created_at")
        mock_classify_delay.assert_called_once_with(run.pk)

        with patch("core.services.gemini_classifier.classify_batch") as mock_classify:
            mock_classify.return_value = {
                "owner@acme-consulting.com": "business",
                "jane.doe83@gmail.com": "individual",
            }
            run_classification(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, ClassificationRun.Status.COMPLETED)
        self.assertEqual(
            Contact.objects.get(email="owner@acme-consulting.com").category,
            Contact.Category.BUSINESS,
        )
        self.assertEqual(
            Contact.objects.get(email="jane.doe83@gmail.com").category,
            Contact.Category.INDIVIDUAL,
        )
        self.assertEqual(Contact.objects.filter(category=Contact.Category.UNCLASSIFIED).count(), 0)

        # 3) Compose a campaign to the "individual" segment (our test inbox).
        resp = self.client.post(
            reverse("core:campaign_create"),
            {
                "subject": "Welcome to the pilot",
                "body_html": "<p>Hello from the EmailPro pilot!</p>",
                "body_text": "",
                "segment": Campaign.Segment.INDIVIDUAL,
            },
        )
        self.assertEqual(resp.status_code, 302)
        campaign = Campaign.objects.latest("created_at")
        self.assertEqual(campaign.status, Campaign.Status.DRAFT)
        self.assertEqual(campaign.recipient_count, 1)

        # Review screen should show the correct preview count before send.
        resp = self.client.get(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertContains(resp, "1")

        # 4) Confirm and send — Gmail SMTP is mocked at the service boundary.
        with patch("core.tasks.send_campaign.delay") as mock_send_delay:
            resp = self.client.post(reverse("core:campaign_confirm", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 302)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.QUEUED)
        self.assertEqual(CampaignRecipient.objects.filter(campaign=campaign).count(), 1)
        mock_send_delay.assert_called_once_with(campaign.pk)

        with patch("core.services.mailer.send_single_email", return_value=True) as mock_send:
            send_campaign(campaign.pk)

        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args.kwargs["to_email"], "jane.doe83@gmail.com")

        # 5) Verify the campaign report.
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(campaign.failed_count, 0)

        recipient = CampaignRecipient.objects.get(campaign=campaign)
        self.assertEqual(recipient.status, CampaignRecipient.Status.SENT)
        self.assertIsNotNone(recipient.sent_at)

        resp = self.client.get(reverse("core:campaign_detail", args=[campaign.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "jane.doe83@gmail.com")
        self.assertContains(resp, "Sent")
