"""
Gmail SMTP sending for a single campaign, with a conservative per-minute
throttle and retries for transient SMTP failures. Uses Django's
EmailMultiAlternatives so both HTML and plain-text parts are sent, plus an
optional single attachment.
"""
import logging
import smtplib
import time
from html import unescape
from re import sub as re_sub

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


def html_to_plain_fallback(html):
    """Very small HTML->text fallback used only when no plain body is set."""
    text = re_sub(r"<br\s*/?>", "\n", html)
    text = re_sub(r"</p>", "\n\n", text)
    text = re_sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def is_configured():
    return bool(settings.GMAIL_ADDRESS and settings.GMAIL_APP_PASSWORD)


def send_single_email(subject, html_body, plain_body, to_email, attachment_field=None):
    """
    Send one email via Gmail SMTP. Retries transient errors up to
    TRANSIENT_RETRIES times; raises immediately on permanent errors.

    Returns True on success. Raises the underlying exception if all
    retries are exhausted or a permanent failure occurs.
    """
    if not is_configured():
        raise MailerNotConfigured(
            "Gmail sending is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD."
        )

    plain_body = plain_body or html_to_plain_fallback(html_body)

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


def throttle_delay_seconds():
    """Seconds to sleep between sends to respect SEND_RATE_PER_MINUTE."""
    rate = max(1, settings.SEND_RATE_PER_MINUTE)
    return 60.0 / rate
