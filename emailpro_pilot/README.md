# EmailPro Django Pilot

A single-admin Django app that turns an email-only CSV into classified
contact segments, then sends controlled Gmail SMTP campaigns with
delivery-result reporting.

**Workflow:** Upload CSV → validate/deduplicate → classify with Gemini →
choose segment → compose/review → queue send → view sent/failed results.

> **Note on this build:** this project was written in a sandboxed
> environment with no package registry access, so the test suite below
> has **not actually been executed** here — only syntax-checked
> (`python -m py_compile`). Please run `python manage.py test` yourself
> after installing dependencies before relying on it. If anything
> doesn't pass, it's likely a small import/signature mismatch rather
> than a logic error — flag it and it's a quick fix.

## 1. Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env — see "Configuration" below
```

You'll also need Redis running locally for Celery:

```bash
# macOS: brew install redis && brew services start redis
# Ubuntu: sudo apt install redis-server
# or: docker run -d -p 6379:6379 redis:7
```

## 2. Configuration (all via environment variables)

Edit `.env` (loaded automatically by `emailpro/settings.py` via
`python-dotenv`). Nothing sensitive is hard-coded anywhere in the repo.

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | `True`/`False` |
| `DATABASE_URL` | Leave blank for local SQLite; set to a Postgres URL later |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis URLs |
| `GEMINI_API_KEY` | Google Gemini API key for classification |
| `GEMINI_MODEL` | Defaults to `gemini-1.5-flash` |
| `GMAIL_ADDRESS` | Sending Gmail address |
| `GMAIL_APP_PASSWORD` | [Gmail App Password](https://support.google.com/accounts/answer/185833) (not your normal password — requires 2FA enabled) |
| `SENDER_NAME` | Display name used in the "From" header |
| `SEND_RATE_PER_MINUTE` | Conservative throttle, default 20/min |

The in-app **Settings** screen (`/settings/`) shows which of these are
configured — it never displays the actual secret values.

## 3. Run it

You need three processes running (in separate terminals):

```bash
# 1. Database migrations + admin user (first time only)
python manage.py migrate
python manage.py createsuperuser

# 2. Django dev server
python manage.py runserver

# 3. Celery worker (handles CSV processing, classification, sending)
celery -A emailpro worker -l info
```

Then log in at `http://localhost:8000/admin/` first to establish a
session (all pilot screens require an authenticated user), then visit
`http://localhost:8000/`.

## 4. Using the pilot

1. **Upload CSV** — a CSV with a single `email` column (header name is
   case-insensitive). Invalid rows and duplicates (both within the file
   and against existing contacts) are automatically dropped and reported
   on the batch detail page.
2. **Classify** — from the dashboard, trigger classification of all
   currently-unclassified contacts. This runs as a background Celery job
   in batches of 20, calling Gemini with a strict structured-JSON prompt.
   Contacts Gemini isn't confident about are left `unclassified` for
   manual review rather than guessed.
3. **Compose a campaign** — subject, HTML message (plain-text is
   auto-generated if left blank), target segment (`business`,
   `individual`, or `all`), and an optional attachment.
4. **Review & confirm** — shows the exact recipient count for the chosen
   segment and requires an explicit "Confirm & send campaign" click
   (with a JS confirm dialog) before anything is queued. Nothing sends
   without this explicit step.
5. **Report** — the campaign detail page shows totals (recipients, sent,
   failed) and a per-recipient table with status and any error message.
   This is a Gmail SMTP pilot: **no open/click tracking** is available or
   promised.

## 5. Running tests

```bash
python manage.py test
```

Test coverage (`core/tests/`):

- `test_csv_import.py` — valid emails, invalid rows, case-insensitive
  header, in-file duplicates, duplicates against existing contacts,
  re-importing the same file.
- `test_classification.py` — Gemini JSON parsing (well-formed, malformed,
  partial), transient-error retries, permanent-error handling, and the
  `run_classification` task's unclassified-fallback behavior.
- `test_campaigns.py` — segment selection, attachment size validation,
  the explicit confirmation requirement, duplicate-send protection (DB
  unique constraint + confirm-view guard), and throttled task behavior
  skipping already-sent recipients.
- `test_email_delivery.py` — mocked Gmail SMTP success/failure/retry
  behavior and accurate sent/failed report totals for mixed outcomes.
- `test_e2e.py` — full acceptance flow: upload a small CSV → classify →
  compose → confirm → send → verify the campaign report, all through the
  actual view/task functions with Gemini and Gmail mocked at their
  service boundaries.

All external calls (Gemini API, Gmail SMTP) are mocked in tests — no
real network calls or real emails are sent by the test suite.

## 6. Project layout

```
emailpro/            # Django project settings, celery app, URLconf
core/
  models.py           # Contact, ImportBatch, ClassificationRun, Campaign, CampaignRecipient
  forms.py            # CSVUploadForm, CampaignForm
  views.py            # Upload, batches, contacts, classification, campaigns, settings
  tasks.py            # Celery tasks: process_import_batch, run_classification, send_campaign
  services/
    csv_import.py      # Parse/validate/normalize/dedupe CSV rows
    gemini_classifier.py  # Gemini API call + structured JSON parsing + retries
    mailer.py           # Gmail SMTP send with retry/throttle
  templates/core/      # Bootstrap (dark theme) + light HTMX templates
  tests/               # Test suite described above
```

## 7. Known limitations (by design, for this pilot phase)

- Single Django admin operator — no public sign-up, teams, or
  multi-tenant isolation.
- Email-only CSV input — no first-name personalization or flexible
  column mapping yet.
- Gmail SMTP is fine for small pilot volumes only; it will not scale to
  large sends and has no bounce/webhook handling.
- No unsubscribe management, GDPR/CAN-SPAM workflow, or domain
  verification — send only to an approved, consented pilot list.
- No open/click tracking.

These are explicitly deferred to a later, production-hardening phase.
