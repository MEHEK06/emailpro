import logging
import time

from celery import shared_task
from django.db import transaction

from core.models import Campaign, CampaignRecipient, ClassificationRun, Contact, ImportBatch
from core.services import csv_import, gemini_classifier, mailer

logger = logging.getLogger(__name__)

CLASSIFICATION_BATCH_SIZE = 20


@shared_task(bind=True, max_retries=0)
def process_import_batch(self, batch_id):
    """Validate/dedupe/persist a freshly uploaded CSV in the background."""
    try:
        batch = ImportBatch.objects.get(pk=batch_id)
    except ImportBatch.DoesNotExist:
        logger.error("ImportBatch %s no longer exists.", batch_id)
        return

    batch.mark_processing()
    try:
        with batch.file.open("rb") as fh:
            result = csv_import.parse_and_validate(fh)

        with transaction.atomic():
            csv_import.persist_new_contacts(result, batch)
            batch.total_rows = result.total_rows
            batch.valid_count = result.valid_count
            batch.invalid_count = result.invalid_count
            batch.duplicate_in_file_count = result.duplicate_in_file_count
            batch.duplicate_existing_count = result.duplicate_existing_count
            batch.new_contacts_count = result.new_contacts_count
            batch.error_log = "\n".join(result.invalid_samples)
            batch.save(
                update_fields=[
                    "total_rows",
                    "valid_count",
                    "invalid_count",
                    "duplicate_in_file_count",
                    "duplicate_existing_count",
                    "new_contacts_count",
                    "error_log",
                ]
            )
        batch.mark_completed()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Import batch %s failed", batch_id)
        batch.mark_failed(str(exc))


@shared_task(bind=True, max_retries=0)
def run_classification(self, run_id):
    """
    Classify every currently-unclassified contact via Gemini, in batches.
    Contacts Gemini isn't confident about (or that fail even after
    retries) are left unclassified for manual review.
    """
    try:
        run = ClassificationRun.objects.get(pk=run_id)
    except ClassificationRun.DoesNotExist:
        logger.error("ClassificationRun %s no longer exists.", run_id)
        return

    contacts = list(Contact.objects.filter(category=Contact.Category.UNCLASSIFIED))
    run.mark_running(total_contacts=len(contacts))

    business_count = 0
    individual_count = 0
    api_errors = 0
    error_messages = []

    for i in range(0, len(contacts), CLASSIFICATION_BATCH_SIZE):
        chunk = contacts[i : i + CLASSIFICATION_BATCH_SIZE]
        emails = [c.email for c in chunk]
        try:
            classifications = gemini_classifier.classify_batch(emails)
        except gemini_classifier.ClassificationError as exc:
            api_errors += 1
            error_messages.append(str(exc))
            logger.warning("Classification batch failed, leaving unclassified: %s", exc)
            continue

        to_update_business = []
        to_update_individual = []
        for contact in chunk:
            category = classifications.get(contact.email)
            if category == Contact.Category.BUSINESS:
                to_update_business.append(contact.pk)
            elif category == Contact.Category.INDIVIDUAL:
                to_update_individual.append(contact.pk)
            # else: leave unclassified for manual review

        if to_update_business:
            Contact.objects.filter(pk__in=to_update_business).update(
                category=Contact.Category.BUSINESS
            )
            business_count += len(to_update_business)
        if to_update_individual:
            Contact.objects.filter(pk__in=to_update_individual).update(
                category=Contact.Category.INDIVIDUAL
            )
            individual_count += len(to_update_individual)

    remaining_unclassified = Contact.objects.filter(category=Contact.Category.UNCLASSIFIED).count()

    run.classified_business = business_count
    run.classified_individual = individual_count
    run.left_unclassified = remaining_unclassified
    run.api_errors = api_errors
    run.error_details = "\n".join(error_messages)
    run.save(
        update_fields=[
            "classified_business",
            "classified_individual",
            "left_unclassified",
            "api_errors",
            "error_details",
        ]
    )

    if api_errors and business_count == 0 and individual_count == 0:
        run.mark_failed("All classification batches failed; see error_details.")
    else:
        run.mark_completed()


@shared_task(bind=True, max_retries=0)
def send_campaign(self, campaign_id):
    """
    Send a confirmed campaign to its queued CampaignRecipient rows,
    throttled to the configured per-minute rate. Already-sent recipients
    are skipped so re-running this task is safe (duplicate-send
    protection also lives at the DB level via a unique constraint).
    """
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("Campaign %s no longer exists.", campaign_id)
        return

    if not mailer.is_configured():
        campaign.status = Campaign.Status.FAILED
        campaign.save(update_fields=["status"])
        logger.error("Campaign %s cannot send: Gmail is not configured.", campaign_id)
        return

    campaign.status = Campaign.Status.SENDING
    campaign.save(update_fields=["status"])

    delay = mailer.throttle_delay_seconds()
    queued_recipients = campaign.recipients.filter(status=CampaignRecipient.Status.QUEUED).order_by("id")

    sent = 0
    failed = 0

    for recipient in queued_recipients.iterator():
        recipient.attempts += 1
        recipient.save(update_fields=["attempts"])
        try:
            mailer.send_single_email(
                subject=campaign.subject,
                html_body=campaign.body_html,
                plain_body=campaign.body_text,
                to_email=recipient.email,
                attachment_field=campaign.attachment if campaign.attachment else None,
            )
            recipient.mark_sent()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            recipient.mark_failed(str(exc))
            failed += 1
            logger.warning("Send failed for %s on campaign %s: %s", recipient.email, campaign_id, exc)

        time.sleep(delay)

    campaign.sent_count = campaign.recipients.filter(status=CampaignRecipient.Status.SENT).count()
    campaign.failed_count = campaign.recipients.filter(status=CampaignRecipient.Status.FAILED).count()
    # The campaign is marked "completed" once the run finishes even if some
    # individual recipients failed — per-recipient outcomes are what the
    # report screen shows; the campaign as a whole ran to completion.
    campaign.status = Campaign.Status.COMPLETED
    campaign.save(update_fields=["sent_count", "failed_count", "status"])
