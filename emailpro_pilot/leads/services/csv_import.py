"""
CSV import for leads: auto-detects common header synonyms for each field
(so slightly different exports from different scrapers/tools still work),
requires only "email" to be present and valid, deduplicates by normalized
email against both the file itself and existing leads, and preserves
unsubscribed status on re-import (a previously-unsubscribed lead's opt-out
is never silently reset by a fresh CSV).
"""
import csv
import io
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from leads.models import Lead

_email_validator = EmailValidator()

MAX_ERROR_SAMPLE = 25

# Header synonyms, in priority order, for each logical field. First match
# wins if a CSV happens to have more than one candidate column.
HEADER_SYNONYMS = {
    "business_name": ["business", "business_name", "company", "company_name", "business name"],
    "owner_name": ["owner", "owner_name", "contact", "contact_name", "name", "owner name"],
    "email": ["email", "email_address", "e-mail"],
    "phone": ["phone", "phone_number", "whatsapp", "whatsapp_number", "mobile"],
    "country": ["country"],
    "source": ["source", "lead_source"],
    "score": ["score", "lead_score"],
}


@dataclass
class LeadImportResult:
    total_rows: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    duplicate_in_file_count: int = 0
    duplicate_existing_count: int = 0
    new_leads_count: int = 0
    invalid_samples: list = field(default_factory=list)
    rows_to_create: list = field(default_factory=list)  # list of dicts ready for Lead(**row)


def _normalize_header(name):
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


def _map_columns(fieldnames):
    """Return {logical_field: actual_csv_column_name} for whatever matches."""
    normalized_lookup = {_normalize_header(f): f for f in (fieldnames or [])}
    mapping = {}
    for logical_field, synonyms in HEADER_SYNONYMS.items():
        for synonym in synonyms:
            key = _normalize_header(synonym)
            if key in normalized_lookup:
                mapping[logical_field] = normalized_lookup[key]
                break
    return mapping


def _normalize_email(raw_email):
    return (raw_email or "").strip().lower()


def _safe_int(value, default=0):
    try:
        return max(0, int(str(value).strip()))
    except (ValueError, TypeError):
        return default


def parse_and_validate(file_obj):
    """
    Read the uploaded CSV, map columns, validate/normalize/dedupe rows.
    Does NOT touch the database — see persist_new_leads for that.
    """
    result = LeadImportResult()

    raw = file_obj.read()
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw

    reader = csv.DictReader(io.StringIO(text))
    mapping = _map_columns(reader.fieldnames)

    if "email" not in mapping:
        result.invalid_samples.append(
            "No recognizable email column found in the CSV header row "
            "(expected one of: email, email_address, e-mail)."
        )
        return result

    seen_in_file = set()
    existing_emails = set(Lead.objects.values_list("email", flat=True))

    for row_num, row in enumerate(reader, start=2):  # header is row 1
        result.total_rows += 1
        raw_email = row.get(mapping["email"], "")
        normalized_email = _normalize_email(raw_email)

        if not normalized_email:
            result.invalid_count += 1
            _add_sample(result, row_num, raw_email, "empty email")
            continue

        try:
            _email_validator(normalized_email)
        except ValidationError:
            result.invalid_count += 1
            _add_sample(result, row_num, raw_email, "invalid email format")
            continue

        if normalized_email in seen_in_file:
            result.duplicate_in_file_count += 1
            continue
        seen_in_file.add(normalized_email)

        if normalized_email in existing_emails:
            result.duplicate_existing_count += 1
            continue

        result.valid_count += 1
        result.rows_to_create.append(
            {
                "email": normalized_email,
                "business_name": (row.get(mapping.get("business_name"), "") or "").strip()[:255],
                "owner_name": (row.get(mapping.get("owner_name"), "") or "").strip()[:255],
                "phone": (row.get(mapping.get("phone"), "") or "").strip()[:50],
                "country": (row.get(mapping.get("country"), "") or "").strip()[:100],
                "source": (row.get(mapping.get("source"), "") or "").strip()[:100] or "csv_import",
                "score": _safe_int(row.get(mapping.get("score"), 0)),
            }
        )

    result.new_leads_count = len(result.rows_to_create)
    return result


def _add_sample(result, row_num, raw_value, reason):
    if len(result.invalid_samples) < MAX_ERROR_SAMPLE:
        result.invalid_samples.append(f"row {row_num}: '{raw_value}' ({reason})")


def persist_new_leads(result):
    """
    Bulk-create Lead rows for validated rows in a LeadImportResult.

    Note on opt-out preservation: this function only ever creates leads
    for emails that were NOT already present (see duplicate_existing_count
    above), so an existing lead's `unsubscribed` flag is never touched by
    a re-import — it simply isn't re-created or overwritten.
    """
    objs = [Lead(**row) for row in result.rows_to_create]
    Lead.objects.bulk_create(objs, ignore_conflicts=True)
    return len(objs)
