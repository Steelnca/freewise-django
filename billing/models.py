from __future__ import annotations

import secrets

from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.models.mixins import PublicIDMixin

class SubscriptionPlan(PublicIDMixin, models.Model):
    class Role(models.TextChoices):
        FREELANCER = "FREELANCER", _("Freelancer")
        CLIENT = "CLIENT", _("Client")

    PUBLIC_ID_PREFIX = "fwsubp"
    PUBLIC_ID_LENGTH_PREFIX = 6

    role = models.CharField(max_length=20, choices=Role.choices, db_index=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField()
    description = models.TextField(blank=True, default="")

    max_open_bids = models.PositiveIntegerField(default=0)
    max_active_contracts = models.PositiveIntegerField(default=0)
    max_jobs_posted = models.PositiveIntegerField(default=0)
    max_active_jobs = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True, db_index=True)
    is_default = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["role"],
                condition=models.Q(is_default=True),
                name="unique_default_plan_per_role",
            ),
            models.UniqueConstraint(
                fields=["role", "slug"],
                name="unique_plan_slug_per_role",
            ),
        ]

    def __str__(self):
        return f"{self.role}: {self.name}"

class SubscriptionPlanPrice(PublicIDMixin, models.Model):
    class BillingCycle(models.TextChoices):
        MONTHLY = "MONTHLY", _("Monthly")
        YEARLY = "YEARLY", _("Yearly")

    PUBLIC_ID_PREFIX = "fwsubpp"
    PUBLIC_ID_LENGTH_PREFIX = 6

    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.CASCADE,
        related_name="prices",
    )

    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        db_index=True,
    )

    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "billing_cycle"],
                name="unique_price_per_plan_and_cycle",
            ),
        ]

    def __str__(self):
        return f"{self.plan.name} - {self.billing_cycle}"

class FreelancerSubscription(PublicIDMixin, models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        PAST_DUE = "PAST_DUE", _("Past due")
        CANCELED = "CANCELED", _("Canceled")
        EXPIRED = "EXPIRED", _("Expired")

    PUBLIC_ID_PREFIX = "fwfsub"
    PUBLIC_ID_LENGTH_PREFIX = 32

    freelancer = models.OneToOneField(
        "freelancers.FreelancerProfile",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        limit_choices_to={"role": SubscriptionPlan.Role.FREELANCER},
        related_name="freelancer_subscriptions",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    starts_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    provider_name = models.CharField(max_length=50, blank=True, default="")
    provider_reference = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_active_subscription(self) -> bool:
        return self.status == self.Status.ACTIVE and (self.ends_at is None or self.ends_at > timezone.now())

class ClientSubscription(PublicIDMixin, models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        PAST_DUE = "PAST_DUE", _("Past due")
        CANCELED = "CANCELED", _("Canceled")
        EXPIRED = "EXPIRED", _("Expired")

    PUBLIC_ID_PREFIX = "fwcsub"
    PUBLIC_ID_LENGTH_PREFIX = 32

    client = models.OneToOneField(
        "clients.ClientProfile",
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        limit_choices_to={"role": SubscriptionPlan.Role.CLIENT},
        related_name="client_subscriptions",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    starts_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    provider_name = models.CharField(max_length=50, blank=True, default="")
    provider_reference = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_active_subscription(self) -> bool:
        return self.status == self.Status.ACTIVE and (self.ends_at is None or self.ends_at > timezone.now())