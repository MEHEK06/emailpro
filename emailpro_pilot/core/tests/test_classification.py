from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core.models import Contact
from core.services import gemini_classifier
from core.tasks import run_classification
from core.models import ClassificationRun


class GeminiResponseParsingTests(TestCase):
    def test_parses_well_formed_json(self):
        text = '[{"email": "a@biz.com", "category": "business"}, {"email": "b@gmail.com", "category": "individual"}]'
        result = gemini_classifier._parse_response(text, ["a@biz.com", "b@gmail.com"])
        self.assertEqual(result, {"a@biz.com": "business", "b@gmail.com": "individual"})

    def test_malformed_json_returns_empty(self):
        result = gemini_classifier._parse_response("not json at all", ["a@biz.com"])
        self.assertEqual(result, {})

    def test_non_list_json_returns_empty(self):
        result = gemini_classifier._parse_response('{"email": "a@biz.com"}', ["a@biz.com"])
        self.assertEqual(result, {})

    def test_unknown_email_or_bad_category_is_dropped(self):
        text = (
            '[{"email": "unexpected@biz.com", "category": "business"}, '
            '{"email": "a@biz.com", "category": "not-a-real-category"}]'
        )
        result = gemini_classifier._parse_response(text, ["a@biz.com"])
        self.assertEqual(result, {})

    def test_partial_response_only_confident_entries_included(self):
        text = '[{"email": "a@biz.com", "category": "business"}]'
        result = gemini_classifier._parse_response(text, ["a@biz.com", "b@gmail.com"])
        self.assertEqual(result, {"a@biz.com": "business"})
        self.assertNotIn("b@gmail.com", result)


@override_settings(GEMINI_API_KEY="test-key")
class ClassifyBatchRetryTests(TestCase):
    @patch("core.services.gemini_classifier._get_model")
    @patch("core.services.gemini_classifier.time.sleep", return_value=None)
    def test_transient_errors_are_retried_then_succeed(self, mock_sleep, mock_get_model):
        model = MagicMock()
        good_response = MagicMock()
        good_response.text = '[{"email": "a@biz.com", "category": "business"}]'
        model.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            good_response,
        ]
        mock_get_model.return_value = model

        result = gemini_classifier.classify_batch(["a@biz.com"])
        self.assertEqual(result, {"a@biz.com": "business"})
        self.assertEqual(model.generate_content.call_count, 2)

    @patch("core.services.gemini_classifier._get_model")
    @patch("core.services.gemini_classifier.time.sleep", return_value=None)
    def test_persistent_transient_errors_raise_after_retries(self, mock_sleep, mock_get_model):
        model = MagicMock()
        model.generate_content.side_effect = Exception("timeout")
        mock_get_model.return_value = model

        with self.assertRaises(gemini_classifier.ClassificationError):
            gemini_classifier.classify_batch(["a@biz.com"])
        self.assertEqual(model.generate_content.call_count, gemini_classifier.TRANSIENT_RETRIES)

    @patch("core.services.gemini_classifier._get_model")
    def test_non_transient_error_raises_immediately(self, mock_get_model):
        model = MagicMock()
        model.generate_content.side_effect = Exception("401 invalid api key")
        mock_get_model.return_value = model

        with self.assertRaises(gemini_classifier.ClassificationError):
            gemini_classifier.classify_batch(["a@biz.com"])
        self.assertEqual(model.generate_content.call_count, 1)

    def test_missing_api_key_raises_classification_error(self):
        with override_settings(GEMINI_API_KEY=""):
            with self.assertRaises(gemini_classifier.ClassificationError):
                gemini_classifier.classify_batch(["a@biz.com"])


@override_settings(GEMINI_API_KEY="test-key")
class RunClassificationTaskTests(TestCase):
    def test_confident_contacts_are_classified_and_rest_left_unclassified(self):
        Contact.objects.create(email="biz@company.com")
        Contact.objects.create(email="person@gmail.com")
        Contact.objects.create(email="unsure@example.com")

        run = ClassificationRun.objects.create()

        with patch("core.services.gemini_classifier.classify_batch") as mock_classify:
            mock_classify.return_value = {
                "biz@company.com": "business",
                "person@gmail.com": "individual",
                # unsure@example.com intentionally omitted
            }
            run_classification(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, ClassificationRun.Status.COMPLETED)
        self.assertEqual(
            Contact.objects.get(email="biz@company.com").category, Contact.Category.BUSINESS
        )
        self.assertEqual(
            Contact.objects.get(email="person@gmail.com").category, Contact.Category.INDIVIDUAL
        )
        self.assertEqual(
            Contact.objects.get(email="unsure@example.com").category, Contact.Category.UNCLASSIFIED
        )
        self.assertEqual(run.left_unclassified, 1)

    def test_api_failure_leaves_contacts_unclassified_and_records_error(self):
        Contact.objects.create(email="a@example.com")
        run = ClassificationRun.objects.create()

        with patch("core.services.gemini_classifier.classify_batch") as mock_classify:
            mock_classify.side_effect = gemini_classifier.ClassificationError("boom")
            run_classification(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.api_errors, 1)
        self.assertEqual(
            Contact.objects.get(email="a@example.com").category, Contact.Category.UNCLASSIFIED
        )
        self.assertEqual(run.status, ClassificationRun.Status.FAILED)
