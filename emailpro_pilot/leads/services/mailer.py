"""
SMTP sending for a single lead delivery, with retries for transient
failures. Structurally identical to core.services.mailer's retry logic
(kept as a separate copy rather than a shared import so the two pilot
apps stay independently deployable), but throttling here is delegated to
Celery's task-level rate_limit instead of an in-process sleep.
"""
import logging
import smtplib
import time

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

TRANSIENT_RETRIES = 3
RETRY_BACKOFF_SECONDS = 3

TRANSIENT_EXCEPTIONS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPResponseException,
    ConnectionError,
    TimeoutError,
    OSError,
)

PERMANENT_EXCEPTIONS = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)


class MailerNotConfigured(Exception):
    pass


def is_configured():
    return bool(settings.GMAIL_ADDRESS and settings.GMAIL_APP_PASSWORD)


def send_lead_email(subject, html_body, plain_body, to_email, attachment_field=None):
    """
    Send one email via SMTP. Retries transient errors up to
    TRANSIENT_RETRIES times; raises immediately on permanent errors.
    """
    if not is_configured():
        raise MailerNotConfigured(
            "SMTP sending is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD."
        )

    last_error = None
    for attempt in range(1, TRANSIENT_RETRIES + 1):
        try:
            message = EmailMultiAlternatives(
                subject=subject,
                body=plain_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[to_email],
            )
            message.attach_alternative(html_body, "text/html")

            if attachment_field:
                attachment_field.open("rb")
                try:
                    message.attach(
                        attachment_field.name.rsplit("/", 1)[-1],
                        attachment_field.read(),
                        "application/pdf",
                    )
                finally:
                    attachment_field.close()

            message.send(fail_silently=False)
            return True
        except PERMANENT_EXCEPTIONS as exc:
            logger.info("Permanent send failure to %s: %s", to_email, exc)
            raise
        except TRANSIENT_EXCEPTIONS as exc:
            last_error = exc
            logger.warning(
                "Transient send failure to %s on attempt %s/%s: %s",
                to_email,
                attempt,
                TRANSIENT_RETRIES,
                exc,
            )
            if attempt < TRANSIENT_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise last_error
