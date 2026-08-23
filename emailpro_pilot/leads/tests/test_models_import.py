from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from leads.models import Lead
from leads.services import csv_import


def make_csv(header, rows):
    content = header + "\n" + "\n".join(rows)
    return SimpleUploadedFile("leads.csv", content.encode("utf-8"), content_type="text/csv")


class LeadCSVImportTests(TestCase):
    def test_valid_import_with_all_columns(self):
        f = make_csv(
            "business,owner,email,phone,country,source,score",
            ["Acme Co,Jane Doe,jane@acme.com,+1234567890,USA,business_website,90"],
        )
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.new_leads_count, 1)
        row = result.rows_to_create[0]
        self.assertEqual(row["email"], "jane@acme.com")
        self.assertEqual(row["business_name"], "Acme Co")
        self.assertEqual(row["owner_name"], "Jane Doe")
        self.assertEqual(row["score"], 90)

    def test_header_synonyms_are_recognized(self):
        f = make_csv(
            "company,contact,e-mail",
            ["Beta LLC,Bob,bob@beta.com"],
        )
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.rows_to_create[0]["business_name"], "Beta LLC")

    def test_missing_email_column_reports_invalid(self):
        f = make_csv("business,owner", ["Acme,Jane"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 0)
        self.assertTrue(result.invalid_samples)

    def test_missing_email_value_in_row_is_skipped(self):
        f = make_csv("email,business", [",Acme", "ok@example.com,Beta"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(result.valid_count, 1)

    def test_malformed_email_is_rejected(self):
        f = make_csv("email", ["not-an-email"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.invalid_count, 1)
        self.assertEqual(result.valid_count, 0)

    def test_duplicate_within_file_is_deduped(self):
        f = make_csv("email", ["dup@example.com", "DUP@example.com"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.duplicate_in_file_count, 1)

    def test_duplicate_against_existing_lead_is_detected(self):
        Lead.objects.create(email="existing@example.com")
        f = make_csv("email", ["existing@example.com", "new@example.com"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.duplicate_existing_count, 1)
        self.assertEqual(result.valid_count, 1)

    def test_persist_creates_leads_with_defaults(self):
        f = make_csv("email", ["x@example.com"])
        result = csv_import.parse_and_validate(f)
        created = csv_import.persist_new_leads(result)
        self.assertEqual(created, 1)
        lead = Lead.objects.get(email="x@example.com")
        self.assertFalse(lead.contacted)
        self.assertFalse(lead.unsubscribed)
        self.assertEqual(lead.source, "csv_import")

    def test_reimport_preserves_unsubscribed_status(self):
        lead = Lead.objects.create(email="opted-out@example.com")
        lead.mark_unsubscribed()

        f = make_csv("email,business", ["opted-out@example.com,New Business Name"])
        result = csv_import.parse_and_validate(f)
        csv_import.persist_new_leads(result)

        lead.refresh_from_db()
        self.assertTrue(lead.unsubscribed)
        # The re-import should not have created a duplicate or overwritten
        # the existing row — it's counted as a duplicate against existing.
        self.assertEqual(result.duplicate_existing_count, 1)
        self.assertEqual(Lead.objects.filter(email="opted-out@example.com").count(), 1)
        self.assertEqual(lead.business_name, "")  # untouched by the re-import
