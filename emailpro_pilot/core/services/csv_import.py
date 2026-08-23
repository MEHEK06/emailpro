"""
CSV import pipeline: parse an email-only CSV, normalize/validate every
address, deduplicate inside the file and against existing contacts, then
persist new Contact rows.

The uploaded file must have exactly one relevant column named "email"
(case-insensitive, surrounding whitespace ignored). Any other columns are
ignored rather than rejected, so a slightly messier export still works.
"""
import csv
import io
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from core.models import Contact

_email_validator = EmailValidator()

MAX_ERROR_SAMPLE = 25


@dataclass
class ImportResult:
    total_rows: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    duplicate_in_file_count: int = 0
    duplicate_existing_count: int = 0
    new_contacts_count: int = 0
    invalid_samples: list = field(default_factory=list)
    created_emails: list = field(default_factory=list)


def _find_email_column(fieldnames):
    if not fieldnames:
        return None
    for name in fieldnames:
        if name and name.strip().lower() == "email":
            return name
    return None


def _normalize(raw_email):
    """Trim whitespace and lowercase for consistent dedup/storage."""
    return (raw_email or "").strip().lower()


def parse_and_validate(file_obj):
    """
    Read the uploaded CSV, validate/normalize/dedupe rows.

    Returns an ImportResult. Does NOT touch the database — callers decide
    whether/when to persist (kept separate so tests can check validation
    logic without a DB round trip, and so the Celery task can persist
    inside its own transaction).
    """
    result = ImportResult()

    raw = file_obj.read()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    else:
        text = raw

    reader = csv.DictReader(io.StringIO(text))
    email_col = _find_email_column(reader.fieldnames)

    if email_col is None:
        result.invalid_samples.append(
            "No 'email' column found in the CSV header row."
        )
        return result

    seen_in_file = set()
    existing_emails = set(Contact.objects.values_list("email", flat=True))

    for row_num, row in enumerate(reader, start=2):  # header is row 1
        result.total_rows += 1
        raw_value = row.get(email_col, "")
        normalized = _normalize(raw_value)

        if not normalized:
            result.invalid_count += 1
            _add_sample(result, row_num, raw_value, "empty")
            continue

        try:
            _email_validator(normalized)
        except ValidationError:
            result.invalid_count += 1
            _add_sample(result, row_num, raw_value, "invalid format")
            continue

        if normalized in seen_in_file:
            result.duplicate_in_file_count += 1
            continue
        seen_in_file.add(normalized)

        if normalized in existing_emails:
            result.duplicate_existing_count += 1
            continue

        result.valid_count += 1
        result.created_emails.append(normalized)

    result.new_contacts_count = len(result.created_emails)
    return result


def _add_sample(result, row_num, raw_value, reason):
    if len(result.invalid_samples) < MAX_ERROR_SAMPLE:
        result.invalid_samples.append(f"row {row_num}: '{raw_value}' ({reason})")


def persist_new_contacts(result, batch):
    """Bulk-create Contact rows for the emails collected in an ImportResult."""
    objs = [
        Contact(email=email, source_batch=batch, category=Contact.Category.UNCLASSIFIED)
        for email in result.created_emails
    ]
    Contact.objects.bulk_create(objs, ignore_conflicts=True)
    return len(objs)
