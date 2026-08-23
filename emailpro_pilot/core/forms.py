from django import forms
from django.conf import settings

from core.models import Campaign


class CSVUploadForm(forms.Form):
    file = forms.FileField(
        label="Contacts CSV",
        help_text="A CSV with a single 'email' column (header name is case-insensitive).",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        return f


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["subject", "body_html", "body_text", "segment", "attachment"]
        widgets = {
            "subject": forms.TextInput(attrs={"class": "form-control", "placeholder": "Subject line"}),
            "body_html": forms.Textarea(
                attrs={"class": "form-control", "rows": 10, "placeholder": "Message (HTML allowed)"}
            ),
            "body_text": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "placeholder": "Optional plain-text version (auto-generated if left blank)",
                }
            ),
            "segment": forms.Select(attrs={"class": "form-select"}),
            "attachment": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if attachment and hasattr(attachment, "size"):
            max_bytes = settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
            if attachment.size > max_bytes:
                raise forms.ValidationError(
                    f"Attachment must be smaller than {settings.MAX_ATTACHMENT_SIZE_MB} MB."
                )
        return attachment
