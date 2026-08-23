import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from leads.models import Campaign, EmailDelivery, Lead
from leads.services import mailer, template_render, unsubscribe

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0)
def send_lead_delivery(self, delivery_id):
    """
    Send ONE recipient's email for a campaign. Rate-limited at the task
    level so a single worker never dequeues these faster than the
    configured rate, regardless of how many are queued at once.

    Note: rate_limit is enforced per worker process. Running multiple
    worker processes multiplies the effective send rate — fine for a
    single-worker pilot setup, worth revisiting before scaling out.
    """
    try:
        delivery = EmailDelivery.objects.select_related("campaign", "lead").get(pk=delivery_id)
    except EmailDelivery.DoesNotExist:
        logger.error("EmailDelivery %s no longer exists.", delivery_id)
        return

    if delivery.status != EmailDelivery.Status.QUEUED:
        return  # already sent/failed — safe to no-op on retry/duplicate dispatch

    lead = delivery.lead
    campaign = delivery.campaign

    if lead.unsubscribed:
        delivery.mark_failed("Lead is unsubscribed; skipped.")
        return

    delivery.attempts += 1
    delivery.save(update_fields=["attempts"])

    try:
        unsubscribe_url = unsubscribe.build_unsubscribe_url(lead.pk)
        rendered_html = template_render.render_template(
            campaign.html_body,
            owner_name=lead.owner_name,
            business_name=lead.business_name,
            whatsapp_number=lead.phone,
            unsubscribe_url=unsubscribe_url,
        )
        plain_body = template_render.render_plain_fallback(rendered_html)

        mailer.send_lead_email(
            subject=campaign.subject,
            html_body=rendered_html,
            plain_body=plain_body,
            to_email=delivery.email,
            attachment_field=campaign.attachment if campaign.attachment else None,
        )
        delivery.mark_sent()
        lead.mark_contacted()
    except Exception as exc:  # noqa: BLE001
        delivery.mark_failed(str(exc))
        logger.warning("Send failed for %s on campaign %s: %s", delivery.email, campaign.pk, exc)

    _update_campaign_totals(campaign.pk)


# Apply the configured rate limit to the task after definition so it can
# be driven by settings.SEND_RATE_PER_MINUTE without a hard-coded value.
send_lead_delivery.rate_limit = f"{getattr(settings, 'LEADS_SEND_RATE_PER_MINUTE', 20)}/m"


def _update_campaign_totals(campaign_id):
    campaign = Campaign.objects.get(pk=campaign_id)
    campaign.sent_count = campaign.deliveries.filter(status=EmailDelivery.Status.SENT).count()
    campaign.failed_count = campaign.deliveries.filter(status=EmailDelivery.Status.FAILED).count()
    still_queued = campaign.deliveries.filter(status=EmailDelivery.Status.QUEUED).exists()

    if still_queued:
        campaign.status = Campaign.Status.SENDING
    else:
        campaign.status = Campaign.Status.COMPLETED
        campaign.finished_at = timezone.now()

    campaign.save(update_fields=["sent_count", "failed_count", "status", "finished_at"])
