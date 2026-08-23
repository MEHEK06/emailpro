"""
Gemini-based contact classification.

Given only an email address (no name, no company data), asks Gemini to
judge whether the address looks like a business/role address (e.g.
sales@acme.com, info@company.co) or a personal/individual address (e.g.
john.smith83@gmail.com). Requires a strict structured JSON response;
anything that doesn't parse cleanly is treated as "leave unclassified"
rather than guessed.
"""
import json
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"business", "individual"}

TRANSIENT_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

_SYSTEM_INSTRUCTION = (
    "You classify email addresses only, with no other context, as either "
    "'business' (role accounts, company domains, generic addresses like "
    "info@, sales@, contact@, or clearly corporate domains) or "
    "'individual' (personal-looking addresses, typically on consumer "
    "webmail domains or containing a personal name). "
    "Respond ONLY with strict JSON: a list of objects, each with exactly "
    "two keys: \"email\" (the address, unchanged) and \"category\" (either "
    "\"business\" or \"individual\"). If you are not reasonably confident "
    "for a given address, omit it from the list entirely rather than "
    "guessing. Do not include any text outside the JSON list."
)


class ClassificationError(Exception):
    """Raised for non-transient failures (bad API key, bad request, etc)."""


class TransientClassificationError(Exception):
    """Raised for retryable failures (timeouts, 429/5xx)."""


def _get_model():
    if not settings.GEMINI_API_KEY:
        raise ClassificationError("GEMINI_API_KEY is not configured.")

    import google.generativeai as genai

    genai.configure(api_key=settings.GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        system_instruction=_SYSTEM_INSTRUCTION,
        generation_config={"response_mime_type": "application/json"},
    )


def _call_gemini(model, emails):
    prompt = "Classify these email addresses:\n" + "\n".join(emails)
    try:
        response = model.generate_content(prompt)
    except Exception as exc:  # noqa: BLE001 - vendor SDK raises broad errors
        message = str(exc).lower()
        if any(code in message for code in ("429", "500", "502", "503", "504", "timeout", "deadline")):
            raise TransientClassificationError(str(exc)) from exc
        raise ClassificationError(str(exc)) from exc
    return response


def _parse_response(response_text, expected_emails):
    """
    Parse Gemini's JSON list into {email: category}. Malformed entries,
    unknown emails, or invalid categories are silently dropped (left
    unclassified) rather than causing the whole batch to fail.
    """
    results = {}
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Gemini returned non-JSON output; treating batch as unclassified.")
        return results

    if not isinstance(data, list):
        logger.warning("Gemini JSON was not a list; treating batch as unclassified.")
        return results

    expected_set = set(expected_emails)
    for item in data:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email", "")).strip().lower()
        category = str(item.get("category", "")).strip().lower()
        if email in expected_set and category in VALID_CATEGORIES:
            results[email] = category

    return results


def classify_batch(emails):
    """
    Classify a batch of normalized email addresses via Gemini.

    Returns a dict mapping email -> "business"|"individual" for every
    address Gemini was confident about. Addresses missing from the
    returned dict should be left unclassified.

    Raises ClassificationError for non-transient failures after retries
    are exhausted for transient ones.
    """
    if not emails:
        return {}

    model = _get_model()

    last_error = None
    for attempt in range(1, TRANSIENT_RETRIES + 1):
        try:
            response = _call_gemini(model, emails)
            return _parse_response(response.text, emails)
        except TransientClassificationError as exc:
            last_error = exc
            logger.warning(
                "Transient Gemini error on attempt %s/%s: %s", attempt, TRANSIENT_RETRIES, exc
            )
            if attempt < TRANSIENT_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        except ClassificationError:
            raise

    raise ClassificationError(f"Gemini classification failed after retries: {last_error}")
