import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import Contact, ImportBatch
from core.services import csv_import


def make_csv(rows, header="email"):
    content = header + "\n" + "\n".join(rows)
    return SimpleUploadedFile("contacts.csv", content.encode("utf-8"), content_type="text/csv")


class CSVValidationTests(TestCase):
    def test_valid_rows_are_counted_and_normalized(self):
        f = make_csv(["  Jane@Example.com  ", "bob@example.com"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.total_rows, 2)
        self.assertEqual(result.valid_count, 2)
        self.assertIn("jane@example.com", result.created_emails)
        self.assertIn("bob@example.com", result.created_emails)

    def test_header_is_case_insensitive(self):
        f = make_csv(["a@example.com"], header="EMAIL")
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 1)

    def test_missing_email_header_is_reported(self):
        f = make_csv(["a@example.com"], header="address")
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 0)
        self.assertTrue(result.invalid_samples)

    def test_invalid_and_empty_rows_are_rejected(self):
        f = make_csv(["not-an-email", "", "ok@example.com"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.invalid_count, 2)
        self.assertEqual(result.valid_count, 1)

    def test_duplicates_within_file_are_deduped(self):
        f = make_csv(["dup@example.com", "DUP@example.com", "dup@example.com "])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.duplicate_in_file_count, 2)

    def test_duplicates_against_existing_contacts_are_detected(self):
        Contact.objects.create(email="existing@example.com")
        f = make_csv(["existing@example.com", "new@example.com"])
        result = csv_import.parse_and_validate(f)
        self.assertEqual(result.duplicate_existing_count, 1)
        self.assertEqual(result.valid_count, 1)
        self.assertIn("new@example.com", result.created_emails)

    def test_persist_new_contacts_creates_rows_linked_to_batch(self):
        batch = ImportBatch.objects.create(file=make_csv(["x@example.com"]))
        f = make_csv(["x@example.com", "y@example.com"])
        result = csv_import.parse_and_validate(f)
        created = csv_import.persist_new_contacts(result, batch)
        self.assertEqual(created, 2)
        self.assertEqual(Contact.objects.filter(source_batch=batch).count(), 2)
        for c in Contact.objects.filter(source_batch=batch):
            self.assertEqual(c.category, Contact.Category.UNCLASSIFIED)

    def test_reimporting_same_file_yields_all_duplicates(self):
        batch = ImportBatch.objects.create(file=make_csv(["z@example.com"]))
        first = csv_import.parse_and_validate(make_csv(["z@example.com"]))
        csv_import.persist_new_contacts(first, batch)

        second = csv_import.parse_and_validate(make_csv(["z@example.com"]))
        self.assertEqual(second.valid_count, 0)
        self.assertEqual(second.duplicate_existing_count, 1)
