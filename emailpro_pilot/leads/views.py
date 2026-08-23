from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from leads.forms import EmailTemplateForm, LeadCSVUploadForm
from leads.models import Campaign, EmailDelivery, EmailTemplate, Lead
from leads.services import csv_import, mailer, template_render, unsubscribe
from leads.tasks import send_lead_delivery


def _default_bulk_target():
    """Currently selected default: valid email, not yet contacted, not unsubscribed."""
    return Lead.objects.filter(unsubscribed=False, contacted=False).exclude(email="")


@login_required
def dashboard(request):
    template = EmailTemplate.get_current()
    context = {
        "total_leads": Lead.objects.count(),
        "contacted_count": Lead.objects.filter(contacted=True).count(),
        "sent_count": EmailDelivery.objects.filter(status=EmailDelivery.Status.SENT).count(),
        "failed_count": EmailDelivery.objects.filter(status=EmailDelivery.Status.FAILED).count(),
        "csv_form": LeadCSVUploadForm(),
        "template_form": EmailTemplateForm(instance=template),
        "template": template,
        "leads": Lead.objects.all()[:500],
        "smtp_configured": mailer.is_configured(),
    }
    return render(request, "leads/dashboard.html", context)


@login_required
def upload_csv(request):
    if request.method == "POST":
        form = LeadCSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            result = csv_import.parse_and_validate(form.cleaned_data["file"])
            created = csv_import.persist_new_leads(result)
            messages.success(
                request,
                f"Import complete: {created} new lead(s) added. "
                f"{result.duplicate_existing_count} already existed, "
                f"{result.duplicate_in_file_count} duplicated within the file, "
                f"{result.invalid_count} invalid row(s) skipped.",
            )
            if result.invalid_samples:
                messages.warning(request, "Sample issues: " + "; ".join(result.invalid_samples[:5]))
        else:
            messages.error(request, "Please choose a valid CSV file.")
    return redirect(reverse("leads:dashboard"))


@login_required
def update_template(request):
    template = EmailTemplate.get_current()
    if request.method == "POST":
        form = EmailTemplateForm(request.POST, request.FILES, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, "Template and catalog saved.")
        else:
            messages.error(request, "Could not save template — check the form for errors.")
    return redirect(reverse("leads:dashboard"))


@login_required
@require_POST
def delete_lead(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    lead.delete()
    messages.success(request, "Lead deleted.")
    return redirect(reverse("leads:dashboard"))


@login_required
@require_POST
def send_individual(request, pk):
    lead = get_object_or_404(Lead, pk=pk)

    if not mailer.is_configured():
        messages.error(request, "SMTP is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
        return redirect(reverse("leads:dashboard"))
    if lead.unsubscribed:
        messages.error(request, f"{lead.email} has unsubscribed and cannot be sent to.")
        return redirect(reverse("leads:dashboard"))

    template = EmailTemplate.get_current()
    with transaction.atomic():
        campaign = Campaign.objects.create(
            subject=template.subject,
            html_body=template.html_body,
            attachment=template.catalog_pdf if template.catalog_pdf else None,
            is_bulk=False,
            recipient_count=1,
            status=Campaign.Status.QUEUED,
            confirmed_at=timezone.now(),
        )
        delivery = EmailDelivery.objects.create(campaign=campaign, lead=lead, email=lead.email)

    send_lead_delivery.delay(delivery.pk)
    messages.success(request, f"Sending to {lead.email}...")
    return redirect(reverse("leads:campaign_detail", args=[campaign.pk]))


@login_required
def campaign_preview(request):
    """
    GET: build a non-destructive preview of who would receive the bulk
    send and what the email will look like. Nothing is created here.
    POST: explicit confirmation — creates the Campaign + EmailDelivery
    rows for the same recipient set and dispatches the send tasks.
    """
    lead_ids_param = request.POST.get("lead_ids") or request.GET.get("lead_ids", "")
    if lead_ids_param:
        ids = [int(x) for x in lead_ids_param.split(",") if x.strip().isdigit()]
        leads = Lead.objects.filter(pk__in=ids, unsubscribed=False).exclude(email="")
    else:
        leads = _default_bulk_target()

    template = EmailTemplate.get_current()

    if request.method == "POST":
        if not mailer.is_configured():
            messages.error(request, "SMTP is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD.")
            return redirect(reverse("leads:dashboard"))

        lead_list = list(leads)
        if not lead_list:
            messages.error(request, "No recipients match the current selection.")
            return redirect(reverse("leads:dashboard"))

        with transaction.atomic():
            campaign = Campaign.objects.create(
                subject=template.subject,
                html_body=template.html_body,
                attachment=template.catalog_pdf if template.catalog_pdf else None,
                is_bulk=True,
                recipient_count=len(lead_list),
                status=Campaign.Status.QUEUED,
                confirmed_at=timezone.now(),
            )
            deliveries = [
                EmailDelivery(campaign=campaign, lead=lead, email=lead.email) for lead in lead_list
            ]
            EmailDelivery.objects.bulk_create(deliveries, ignore_conflicts=True)

        for delivery in campaign.deliveries.all():
            send_lead_delivery.delay(delivery.pk)

        messages.success(request, f"Bulk campaign confirmed — sending to {len(lead_list)} recipient(s).")
        return redirect(reverse("leads:campaign_detail", args=[campaign.pk]))

    # GET: build a read-only preview.
    sample_lead = leads.first()
    rendered_sample = None
    if sample_lead:
        rendered_sample = template_render.render_template(
            template.html_body,
            owner_name=sample_lead.owner_name,
            business_name=sample_lead.business_name,
            whatsapp_number=sample_lead.phone,
            unsubscribe_url=unsubscribe.build_unsubscribe_url(sample_lead.pk, request),
        )

    context = {
        "template": template,
        "leads": leads[:200],
        "recipient_count": leads.count(),
        "rendered_sample": rendered_sample,
        "sample_lead": sample_lead,
        "lead_ids_param": lead_ids_param,
        "smtp_configured": mailer.is_configured(),
    }
    return render(request, "leads/campaign_preview.html", context)


@login_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk)
    deliveries = campaign.deliveries.select_related("lead").all()[:1000]
    return render(request, "leads/campaign_detail.html", {"campaign": campaign, "deliveries": deliveries})


@login_required
def campaign_list(request):
    campaigns = Campaign.objects.all()
    return render(request, "leads/campaign_list.html", {"campaigns": campaigns})


def unsubscribe_view(request, token):
    """
    Public, unauthenticated endpoint — recipients click this straight
    from their email client with no login. Marks the lead unsubscribed
    and shows a confirmation page.
    """
    try:
        lead_id = unsubscribe.verify_unsubscribe_token(token)
        lead = Lead.objects.get(pk=lead_id)
    except (unsubscribe.InvalidUnsubscribeToken, Lead.DoesNotExist):
        return render(request, "leads/unsubscribe_invalid.html", status=400)

    if not lead.unsubscribed:
        lead.mark_unsubscribed()

    return render(request, "leads/unsubscribe_confirmed.html", {"lead": lead})
