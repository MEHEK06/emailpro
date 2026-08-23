"""
Renders the user-edited HTML template for a specific lead, exposing only
the four documented variables: {{ownerName}}, {{businessName}},
{{whatsappNumber}}, {{unsubscribeUrl}}.

This deliberately does NOT use Django's full template engine (which would
allow arbitrary {% %} tags, filters, and access to whatever context is
passed in). Instead it does a simple, safe substitution over a fixed
variable set so a template can never do more than fill in those four
values — no template injection surface, no accidental access to other
model fields.
"""
import re

from django.utils.html import escape

# Matches {{ownerName}}, {{ ownerName }}, {{owner_name}} is NOT matched
# (only the exact documented camelCase names are recognized).
_VARIABLE_PATTERN = re.compile(r"\{\{\s*(ownerName|businessName|whatsappNumber|unsubscribeUrl)\s*\}\}")


def render_template(html_body, *, owner_name, business_name, whatsapp_number, unsubscribe_url):
    """
    Substitute the four documented variables into html_body.

    unsubscribe_url is inserted as-is (it's a URL Claude/the app
    generated, safe to place in an href attribute as long as it's a
    proper URL). All other values are HTML-escaped since they come from
    lead data (business_name, owner_name, phone) that could otherwise
    contain characters that break the surrounding HTML.
    """
    values = {
        "ownerName": escape(owner_name or ""),
        "businessName": escape(business_name or ""),
        "whatsappNumber": escape(whatsapp_number or ""),
        "unsubscribeUrl": unsubscribe_url or "",
    }

    def _replace(match):
        return values[match.group(1)]

    return _VARIABLE_PATTERN.sub(_replace, html_body)


def render_plain_fallback(html_body):
    """Very small HTML->text fallback for the plain-text alternative part."""
    text = re.sub(r"<br\s*/?>", "\n", html_body)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    from html import unescape

    return unescape(text).strip()
