from django.contrib import admin

from core.models import Campaign, CampaignRecipient, ClassificationRun, Contact, ImportBatch


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("email", "category", "source_batch", "created_at")
    list_filter = ("category",)
    search_fields = ("email",)


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "total_rows",
        "valid_count",
        "invalid_count",
        "duplicate_in_file_count",
        "duplicate_existing_count",
        "new_contacts_count",
        "uploaded_at",
    )
    list_filter = ("status",)
    readonly_fields = [f.name for f in ImportBatch._meta.fields if f.name != "file"]


@admin.register(ClassificationRun)
class ClassificationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "total_contacts",
        "classified_business",
        "classified_individual",
        "left_unclassified",
        "api_errors",
        "created_at",
    )
    list_filter = ("status",)
    readonly_fields = [f.name for f in ClassificationRun._meta.fields]


class CampaignRecipientInline(admin.TabularInline):
    model = CampaignRecipient
    extra = 0
    readonly_fields = ("contact", "email", "status", "error_message", "attempts", "sent_at")
    can_delete = False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "segment",
        "status",
        "recipient_count",
        "sent_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "segment")
    inlines = [CampaignRecipientInline]


@admin.register(CampaignRecipient)
class CampaignRecipientAdmin(admin.ModelAdmin):
    list_display = ("email", "campaign", "status", "attempts", "sent_at")
    list_filter = ("status", "campaign")
    search_fields = ("email",)
