"""
Signed, stateless unsubscribe links using Django's built-in signing
module. No separate token table: the lead's primary key is signed with
the app's SECRET_KEY, so a link can't be forged or altered, and it never
expires (unsubscribe links should keep working indefinitely, unlike a
password reset link).
"""
from django.conf import settings
from django.core import signing
from django.urls import reverse

_SALT = "leads.unsubscribe"


class InvalidUnsubscribeToken(Exception):
    pass


def generate_unsubscribe_token(lead_id):
    return signing.dumps(lead_id, salt=_SALT)


def verify_unsubscribe_token(token):
    """Returns the lead_id if valid; raises InvalidUnsubscribeToken otherwise."""
    try:
        return signing.loads(token, salt=_SALT)
    except signing.BadSignature as exc:
        raise InvalidUnsubscribeToken(str(exc)) from exc


def build_unsubscribe_url(lead_id, request=None):
    token = generate_unsubscribe_token(lead_id)
    path = reverse("leads:unsubscribe", args=[token])
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "http://localhost:8000")
    return base.rstrip("/") + path
