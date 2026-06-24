
from django.core.exceptions import PermissionDenied

from .models import Job


def _get_client_profile(user):
    account = getattr(user, "account", None)
    return getattr(account, "client_profile", None)


def _ensure_client_profile(user):
    client_profile = _get_client_profile(user)
    if not client_profile:
        raise PermissionDenied("Client profile required.")
    return client_profile


def _wants_publish(status_value) -> bool:
    return status_value == Job.Status.OPEN