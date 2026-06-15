
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

from core.models.mixins import PublicIDMixin


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(max_length=50, blank=True)  # e.g. icon class name or emoji

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)

    def __str__(self):
        return self.name


class Job(PublicIDMixin, models.Model):

    class Status(models.TextChoices):
        OPEN        = 'OPEN',        _('Open')
        IN_PROGRESS = 'IN_PROGRESS', _('In Progress')
        COMPLETED   = 'COMPLETED',   _('Completed')
        CANCELLED   = 'CANCELLED',   _('Cancelled')

    class ExperienceLevel(models.TextChoices):
        ENTRY    = 'BEGINNER',    _('Beginner')
        MID      = 'INTERMEDIATE', _('Intermediate')
        EXPERT   = 'EXPERT',   _('Expert')

    class PricingMode(models.TextChoices):
        FIXED = "FIXED", _("Fixed")
        NEGOTIABLE = "NEGOTIABLE", _("Negotiable")

    class MilestoneMode(models.TextChoices):
        SINGLE = "SINGLE", _("Single")
        MULTI = "MULTI", _("Multi")

    class SplitOwner(models.TextChoices):
        CLIENT = "CLIENT", _("Client")
        FREELANCER = "FREELANCER", _("Freelancer")

    PUBLIC_ID_PREFIX = "fwj"
    PUBLIC_ID_LENGTH_PREFIX = 8

    # --- Ownership ---
    client = models.ForeignKey(
        'clients.ClientProfile',
        on_delete=models.CASCADE,
        related_name='jobs',
    )

    # --- Details ---
    title        = models.CharField(max_length=200)
    description  = models.TextField()
    category     = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        related_name='jobs',
    )
    tags             = models.ManyToManyField(Tag, blank=True, related_name='jobs')
    experience_level = models.CharField(
        max_length=32,
        choices=ExperienceLevel.choices,
        default=ExperienceLevel.MID,
    )

    # --- Deadline ---
    deadline = models.DateField(null=True, blank=True)

    # --- Status ---
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    pricing_mode = models.CharField(
        max_length=20,
        choices=PricingMode.choices,
        default=PricingMode.NEGOTIABLE,
        db_index=True,
    )

    milestone_mode = models.CharField(
        max_length=10,
        choices=MilestoneMode.choices,
        default=MilestoneMode.SINGLE,
        db_index=True,
    )

    split_owner = models.CharField(
        max_length=20,
        choices=SplitOwner.choices,
        default=SplitOwner.CLIENT,
        db_index=True,
    )

    collab_allowed = models.BooleanField(default=False)
    allow_milestone_suggestions = models.BooleanField(default=True)

    budget_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_('Budget Total'),
        help_text=_('Budget total range of the client.')
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()

        if self.budget_total is not None and self.budget_total <= Decimal("0.00"):
            raise ValidationError({"budget_total": _("Job budget total must be greater than zero.")})