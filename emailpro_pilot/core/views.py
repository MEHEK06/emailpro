from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core import tasks
from core.forms import CampaignForm, CSVUploadForm
from core.models import Campaign, CampaignRecipient, ClassificationRun, Contact, ImportBatch
from core.services import mailer


@login_required
def dashboard(request):
    context = {
        "contact_total": Contact.objects.count(),
        "contact_business": Contact.objects.filter(category=Contact.Category.BUSINESS).count(),
        "contact_individual": Contact.objects.filter(category=Contact.Category.INDIVIDUAL).count(),
        "contact_unclassified": Contact.objects.filter(category=Contact.Category.UNCLASSIFIED).count(),
        "recent_batches": ImportBatch.objects.all()[:5],
        "recent_runs": ClassificationRun.objects.all()[:5],
        "recent_campaigns": Campaign.objects.all()[:5],
    }
    return render(request, "core/dashboard.html", context)


@login_required
def upload_csv(request):
    if request.method == "POST":
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            batch = ImportBatch.objects.create(file=form.cleaned_data["file"])
            tasks.process_import_batch.delay(batch.pk)
            messages.success(request, "CSV uploaded. Processing in the background.")
            return redirect(reverse("core:batch_detail", args=[batch.pk]))
    else:
        form = CSVUploadForm()
    return render(request, "core/upload.html", {"form": form})


@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    return render(request, "core/batch_detail.html", {"batch": batch})


@login_required
def batch_list(request):
    batches = ImportBatch.objects.all()
    return render(request, "core/batch_list.html", {"batches": batches})


@login_required
def contacts_list(request):
    category = request.GET.get("category", "")
    contacts = Contact.objects.all()
    if category in dict(Contact.Category.choices):
        contacts = contacts.filter(category=category)
    contacts = contacts[:500]  # pilot-scale cap on a single page
    return render(
        request,
        "core/contacts_list.html",
        {"contacts": contacts, "selected_category": category, "categories": Contact.Category.choices},
    )


@login_required
def start_classification(request):
    if request.method == "POST":
        pending_count = Contact.objects.filter(category=Contact.Category.UNCLASSIFIED).count()
        if pending_count == 0:
            messages.info(request, "There are no unclassified contacts right now.")
            return redirect(reverse("core:dashboard"))
        if not settings.GEMINI_API_KEY:
            messages.error(request, "Gemini is not configured. Set GEMINI_API_KEY first.")
            return redirect(reverse("core:settings_status"))

        run = ClassificationRun.objects.create()
        tasks.run_classification.delay(run.pk)
        messages.success(request, f"Classification started for {pending_count} contacts.")
        return redirect(reverse("core:classification_run_detail", args=[run.pk]))
    return redirect(reverse("core:dashboard"))


@login_required
def classification_run_detail(request, pk):
    run = get_object_or_404(ClassificationRun, pk=pk)
    return render(request, "core/classification_run.html", {"run": run})


@login_required
def classification_run_list(request):
    runs = ClassificationRun.objects.all()
    return render(request, "core/classification_run_list.html", {"runs": runs})


@login_required
def campaign_create(request):
    if request.method == "POST":
        form = CampaignForm(request.POST, request.FILES)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.save()
            campaign.recipient_count = campaign.queryset_for_segment().count()
            campaign.save(update_fields=["recipient_count"])
            return redirect(reverse("core:campaign_confirm", args=[campaign.pk]))
    else:
        form = CampaignForm()
    return render(request, "core/campaign_form.html", {"form": form})


@login_required
def campaign_confirm(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, status=Campaign.Status.DRAFT)
    preview_count = campaign.queryset_for_segment().count()

    if request.method == "POST":
        if not mailer.is_configured():
            messages.error(request, "Gmail sending is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
            return redirect(reverse("core:settings_status"))
        if preview_count == 0:
            messages.error(request, "No recipients match this segment.")
            return redirect(reverse("core:campaign_confirm", args=[campaign.pk]))

        with transaction.atomic():
            contacts = campaign.queryset_for_segment()
            existing_contact_ids = set(
                campaign.recipients.values_list("contact_id", flat=True)
            )
            new_recipients = [
                CampaignRecipient(campaign=campaign, contact=c, email=c.email)
                for c in contacts
                if c.pk not in existing_contact_ids
            ]
            CampaignRecipient.objects.bulk_create(new_recipients, ignore_conflicts=True)

            campaign.recipient_count = campaign.recipients.count()
            campaign.status = Campaign.Status.QUEUED
            from django.utils import timezone

            campaign.confirmed_at = timezone.now()
            campaign.save(update_fields=["recipient_count", "status", "confirmed_at"])

        tasks.send_campaign.delay(campaign.pk)
        messages.success(request, "Campaign confirmed and queued for sending.")
        return redirect(reverse("core:campaign_detail", args=[campaign.pk]))

    return render(
        request,
        "core/campaign_confirm.html",
        {"campaign": campaign, "preview_count": preview_count, "gmail_ready": mailer.is_configured()},
    )


@login_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    recipients = campaign.recipients.all()[:1000]
    queued_count = campaign.recipients.filter(status=CampaignRecipient.Status.QUEUED).count()
    return render(
        request,
        "core/campaign_detail.html",
        {"campaign": campaign, "recipients": recipients, "queued_count": queued_count},
    )


@login_required
def campaign_list(request):
    campaigns = Campaign.objects.all()
    return render(request, "core/campaign_list.html", {"campaigns": campaigns})


@login_required
def settings_status(request):
    context = {
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "gemini_model": settings.GEMINI_MODEL,
        "gmail_configured": mailer.is_configured(),
        "gmail_address_set": bool(settings.GMAIL_ADDRESS),
        "sender_name": settings.SENDER_NAME,
        "send_rate_per_minute": settings.SEND_RATE_PER_MINUTE,
        "database_engine": settings.DATABASES["default"]["ENGINE"],
    }
    return render(request, "core/settings_status.html", context)
