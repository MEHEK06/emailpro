from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone


class Lead(models.Model):
    """A single outreach lead, deduplicated by normalized email."""

    business_name = models.CharField(max_length=255, blank=True, default="")
    owner_name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(unique=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=100, blank=True, default="")
    score = models.PositiveIntegerField(default=0)

    contacted = models.BooleanField(default=False)
    contacted_at = models.DateTimeField(null=True, blank=True)

    unsubscribed = models.BooleanField(default=False, db_index=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.business_name or self.owner_name or self.email}"

    def mark_contacted(self):
        self.contacted = True
        self.contacted_at = timezone.now()
        self.save(update_fields=["contacted", "contacted_at"])

    def mark_unsubscribed(self):
        self.unsubscribed = True
        self.unsubscribed_at = timezone.now()
        self.save(update_fields=["unsubscribed", "unsubscribed_at"])

    @property
    def is_sendable(self):
        return bool(self.email) and not self.unsubscribed


class EmailTemplate(models.Model):
    """
    The single reusable subject/HTML template + catalog PDF that the
    dashboard edits in place. Deliberately a single persisted row (this
    is a single-user pilot) rather than a list of saved templates.
    """

    subject = models.CharField(max_length=255, blank=True, default="")
    html_body = models.TextField(blank=True, default="")
    catalog_pdf = models.FileField(
        upload_to="catalog/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    updated_at = models.DateTimeField(auto_now=True)

    ALLOWED_VARIABLES = ("ownerName", "businessName", "whatsappNumber", "unsubscribeUrl")

    class Meta:
        verbose_name = "Email template"
        verbose_name_plural = "Email template"

    def __str__(self):
        return self.subject or "(untitled template)"

    @classmethod
    def get_current(cls):
        """Single-row accessor: get-or-create the one template row."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Campaign(models.Model):
    """
    A send event — either a bulk send to many leads or an individual
    send (recipient_count=1) triggered from the lead table. Snapshots the
    subject/HTML/attachment actually used, independent of later edits to
    EmailTemplate.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    attachment = models.FileField(upload_to="campaign_attachments/", null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    is_bulk = models.BooleanField(default=True)

    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    error_details = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} [{self.status}]"


class EmailDelivery(models.Model):
    """Immutable per-recipient send record for a campaign."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="deliveries")
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="deliveries")
    email = models.EmailField(help_text="Snapshot of the address at queue time.")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)

    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "lead"], name="unique_delivery_per_campaign"
            )
        ]

    def __str__(self):
        return f"{self.email} -> campaign #{self.campaign_id} ({self.status})"

    def mark_sent(self):
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "sent_at", "error_message"])

    def mark_failed(self, message):
        self.status = self.Status.FAILED
        self.error_message = message[:2000]
        self.save(update_fields=["status", "error_message"])
