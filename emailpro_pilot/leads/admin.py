from django.contrib import admin

from leads.models import Campaign, EmailDelivery, EmailTemplate, Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "business_name",
        "owner_name",
        "country",
        "source",
        "score",
        "contacted",
        "unsubscribed",
        "created_at",
    )
    list_filter = ("contacted", "unsubscribed", "country", "source")
    search_fields = ("email", "business_name", "owner_name")


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("subject", "updated_at")


class EmailDeliveryInline(admin.TabularInline):
    model = EmailDelivery
    extra = 0
    readonly_fields = ("lead", "email", "status", "error_message", "attempts", "sent_at")
    can_delete = False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "is_bulk",
        "status",
        "recipient_count",
        "sent_count",
        "failed_count",
        "created_at",
    )
    list_filter = ("status", "is_bulk")
    inlines = [EmailDeliveryInline]


@admin.register(EmailDelivery)
class EmailDeliveryAdmin(admin.ModelAdmin):
    list_display = ("email", "campaign", "status", "attempts", "sent_at")
    list_filter = ("status",)
    search_fields = ("email",)
