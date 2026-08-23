from django import forms

from leads.models import EmailTemplate


class LeadCSVUploadForm(forms.Form):
    file = forms.FileField(
        label="Leads CSV",
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        help_text=(
            "Common headers are auto-detected: business/company, "
            "owner/contact/name, email (required), phone, country, "
            "source, score."
        ),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        return f


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ["subject", "html_body", "catalog_pdf"]
        widgets = {
            "subject": forms.TextInput(attrs={"class": "form-control", "placeholder": "Email subject"}),
            "html_body": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 10,
                    "placeholder": (
                        "<p>Hello {{ownerName}},</p>"
                        "<p>We work with {{businessName}}...</p>"
                        "<p>WhatsApp: {{whatsappNumber}}</p>"
                        "<p><a href=\"{{unsubscribeUrl}}\">Unsubscribe</a></p>"
                    ),
                }
            ),
            "catalog_pdf": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_catalog_pdf(self):
        pdf = self.cleaned_data.get("catalog_pdf")
        if pdf and hasattr(pdf, "name") and not pdf.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Catalog must be a PDF file.")
        return pdf
