from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone


class Contact(models.Model):
    """A single deduplicated, normalized email contact."""

    class Category(models.TextChoices):
        BUSINESS = "business", "Business"
        INDIVIDUAL = "individual", "Individual"
        UNCLASSIFIED = "unclassified", "Unclassified"

    email = models.EmailField(unique=True, db_index=True)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.UNCLASSIFIED, db_index=True
    )
    source_batch = models.ForeignKey(
        "ImportBatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.email


class ImportBatch(models.Model):
    """One CSV upload and the outcome of validating/deduplicating it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    file = models.FileField(
        upload_to="imports/%Y/%m/%d/",
        validators=[FileExtensionValidator(allowed_extensions=["csv"])],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    total_rows = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    duplicate_in_file_count = models.PositiveIntegerField(default=0)
    duplicate_existing_count = models.PositiveIntegerField(default=0)
    new_contacts_count = models.PositiveIntegerField(default=0)

    # Small JSON-ish sample of problem rows, capped, for admin/user review.
    error_log = models.TextField(blank=True, default="")

    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Batch #{self.pk} ({self.status})"

    def mark_processing(self):
        self.status = self.Status.PROCESSING
        self.save(update_fields=["status"])

    def mark_completed(self):
        self.status = self.Status.COMPLETED
        self.processed_at = timezone.now()
        self.save(update_fields=["status", "processed_at"])

    def mark_failed(self, message):
        self.status = self.Status.FAILED
        self.processed_at = timezone.now()
        self.error_log = (self.error_log + "\n" if self.error_log else "") + f"FATAL: {message}"
        self.save(update_fields=["status", "processed_at", "error_log"])


class ClassificationRun(models.Model):
    """A background Gemini classification job over unclassified contacts."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    total_contacts = models.PositiveIntegerField(default=0)
    classified_business = models.PositiveIntegerField(default=0)
    classified_individual = models.PositiveIntegerField(default=0)
    left_unclassified = models.PositiveIntegerField(default=0)
    api_errors = models.PositiveIntegerField(default=0)

    error_details = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ClassificationRun #{self.pk} ({self.status})"

    def mark_running(self, total_contacts):
        self.status = self.Status.RUNNING
        self.total_contacts = total_contacts
        self.started_at = timezone.now()
        self.save(update_fields=["status", "total_contacts", "started_at"])

    def mark_completed(self):
        self.status = self.Status.COMPLETED
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "finished_at"])

    def mark_failed(self, message):
        self.status = self.Status.FAILED
        self.finished_at = timezone.now()
        self.error_details = (self.error_details + "\n" if self.error_details else "") + f"FATAL: {message}"
        self.save(update_fields=["status", "finished_at", "error_details"])


class Campaign(models.Model):
    """A single email campaign draft/queued/sent to a chosen segment."""

    class Segment(models.TextChoices):
        BUSINESS = "business", "Business only"
        INDIVIDUAL = "individual", "Individual only"
        ALL = "all", "All classified + unclassified"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    subject = models.CharField(max_length=255)
    body_html = models.TextField(help_text="HTML version of the message.")
    body_text = models.TextField(
        blank=True, help_text="Plain-text version. Auto-generated from HTML if left blank."
    )
    segment = models.CharField(max_length=20, choices=Segment.choices, default=Segment.ALL)
    attachment = models.FileField(upload_to="attachments/%Y/%m/%d/", null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} [{self.status}]"

    def queryset_for_segment(self):
        qs = Contact.objects.all()
        if self.segment == self.Segment.BUSINESS:
            return qs.filter(category=Contact.Category.BUSINESS)
        if self.segment == self.Segment.INDIVIDUAL:
            return qs.filter(category=Contact.Category.INDIVIDUAL)
        return qs


class CampaignRecipient(models.Model):
    """Immutable per-contact send record for a campaign."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="campaign_sends")
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
                fields=["campaign", "contact"], name="unique_recipient_per_campaign"
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
