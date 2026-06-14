
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from contracts.models import Milestone

from .models import CollabRequest, CollabApplication

@transaction.atomic
def open_collab_request(*, milestone: Milestone, created_by, title: str, seat_label: str, seats_needed: int, seat_amount, visibility="PUBLIC", description=""):
    if not milestone.contract.collab_allowed:
        raise ValidationError({"detail": "Collabs are not allowed for this contract."})

    return CollabRequest.objects.create(
        milestone=milestone,
        created_by=created_by,
        title=title,
        seat_label=seat_label,
        seats_needed=seats_needed,
        seat_amount=seat_amount,
        visibility=visibility,
        description=description,
        currency=milestone.contract.currency,
    )


@transaction.atomic
def apply_to_collab(*, request_obj: CollabRequest, freelancer, note: str = ""):
    if request_obj.visibility == CollabRequest.Visibility.INVITE_ONLY:
        raise ValidationError({"detail": "This collab is invite only."})
    return CollabApplication.objects.create(
        request=request_obj,
        freelancer=freelancer,
        note=note,
    )


@transaction.atomic
def accept_collab_application(*, application: CollabApplication):
    application.status = CollabApplication.Status.ACCEPTED
    application.save(update_fields=["status", "updated_at"])
    return application