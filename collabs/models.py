
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from core.models.mixins import PublicIDMixin

class CollabRequest(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", _("Public")
        INVITE_ONLY = "INVITE_ONLY", _("Invite only")

    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        FILLED = "FILLED", _("Filled")
        CLOSED = "CLOSED", _("Closed")
        CANCELLED = "CANCELLED", _("Cancelled")

    PUBLIC_ID_PREFIX = "fwcr"
    PUBLIC_ID_LENGTH_PREFIX = 12

    milestone = models.ForeignKey(
        "milestones.Milestone",
        on_delete=models.CASCADE,
        related_name="collab_requests",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collab_requests",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    seat_label = models.CharField(max_length=120)
    seats_needed = models.PositiveSmallIntegerField(default=1)

    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    seat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="DZD")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CollabApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        ACCEPTED = "ACCEPTED", _("Accepted")
        REJECTED = "REJECTED", _("Rejected")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")

    PUBLIC_ID_PREFIX = "fwca"
    PUBLIC_ID_LENGTH_PREFIX = 12

    request = models.ForeignKey(
        CollabRequest,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    freelancer = models.ForeignKey(
        "freelancers.FreelancerProfile",
        on_delete=models.CASCADE,
        related_name="collab_applications",
    )

    note = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
