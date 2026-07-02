
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.db.models import Q

from payments.constants import DEFAULT_CURRENCY
from core.models.mixins import PublicIDMixin
from jobs.models import Job


# Create your models here.

class MilestonePlan(PublicIDMixin, models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PROPOSED = "PROPOSED", _("Proposed")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CONVERTED = "CONVERTED", _("Converted")

    class SourceRole(models.TextChoices):
        CLIENT = "CLIENT", _("Client")
        FREELANCER = "FREELANCER", _("Freelancer")

    class MilestoneMode(models.TextChoices):
        SINGLE = "SINGLE", _("Single")
        MULTI = "MULTI", _("Multi")

    PUBLIC_ID_PREFIX = "fwmp"
    PUBLIC_ID_LENGTH_PREFIX = 12

    job = models.ForeignKey(
        "jobs.Job",
        on_delete=models.CASCADE,
        related_name="milestone_plans",
    )

    proposal = models.ForeignKey(
        "proposals.Proposal",
        blank=True,
        null=True,
        on_delete=models.CASCADE,
        related_name="milestone_plans",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="milestone_plans",
    )

    source_role = models.CharField(
        max_length=12,
        choices=SourceRole.choices,
        default=SourceRole.FREELANCER,
        db_index=True,
    )

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    mode = models.CharField(
        max_length=10,
        choices=MilestoneMode.choices,
        default=MilestoneMode.MULTI,
        db_index=True,
    )

    note = models.TextField(blank=True, default="")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = models.CharField(max_length=3, default="DZD")
    suggestion_enabled = models.BooleanField(default=True)

    is_selected = models.BooleanField(
        default=False,
        verbose_name=_("Selected"),
        help_text=_(
            "Indicates whether this is the currently approved milestone plan "
            "chosen for the job or proposal. Only one plan should be selected "
            "at a time and selected plans may be used to generate contract milestones."
        ),
        db_index=True,
    )

    selected_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Selected at"),
        help_text=_("When the milestone plan was selected by the client."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job"],
                condition=Q(is_selected=True),
                name="unique_selected_plan_per_job",
            )
        ]

    def __str__(self):
        return f"Plan {self.public_id}"

    def clean(self):
        super().clean()

        if self.job.pricing_mode == Job.PricingMode.FIXED and not self.job.budget_total == self.total_amount:
            raise ValidationError({"total_amount": _("Milestone plan total does not match the job budget.")})

class MilestonePlanItem(PublicIDMixin, models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PROPOSED = "PROPOSED", _("Proposed")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        CONVERTED = "CONVERTED", _("Converted")

    PUBLIC_ID_PREFIX = "fwmpi"
    PUBLIC_ID_LENGTH_PREFIX = 28

    plan = models.ForeignKey(
        MilestonePlan,
        on_delete=models.CASCADE,
        related_name="items",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_date = models.DateField()
    order = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Milestone(PublicIDMixin, models.Model):
    """
    Contract milestone.

    Each milestone is tracked independently so Freewise can support
    single-deliverable and multi-phase contracts later without rewrites.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        FUNDED = "FUNDED", _("Funded")
        SUBMITTED = "SUBMITTED", _("Submitted")
        REVISION_REQUESTED = "REVISION_REQUESTED", _("Revision requested")
        DISPUTED = "DISPUTED", _("Disputed")
        STALLED = "STALLED", _("Stalled")
        RELEASED = "RELEASED", _("Released")
        REFUNDED = "REFUNDED", _("Refunded")

    PUBLIC_ID_PREFIX = "fwm"

    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="milestones",
        verbose_name=_("contract"),
        help_text=_("The contract this milestone belongs to."),
    )

    proposal = models.OneToOneField(
        MilestonePlanItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="milestone",
    )

    title = models.CharField(
        max_length=255,
        verbose_name=_("title"),
        help_text=_("A short milestone title."),
    )

    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Optional details about this milestone."),
    )

    currency = models.CharField(
        max_length=3,
        default=DEFAULT_CURRENCY,
        verbose_name=_("currency"),
        help_text=_("Three-letter currency code used for this milestone."),
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("amount"),
        help_text=_("The amount reserved for this milestone."),
    )

    due_date = models.DateField(
        verbose_name=_("due date"),
        help_text=_("The expected delivery date for this milestone."),
    )

    order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("order"),
        help_text=_("The order this milestone appears in."),
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("status"),
        help_text=_("The current stage of this milestone."),
    )

    submission_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("submission note"),
        help_text=_("A short note from the freelancer when submitting work."),
    )

    submission_link = models.URLField(
        blank=True,
        default="",
        verbose_name=_("submission link"),
        help_text=_("A link to the delivered work, preview, or files."),
    )

    review_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("review note"),
        help_text=_("A note from the client after review."),
    )

    revision_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("revision note"),
        help_text=_("A note from the client after revision."),
    )


    dispute_reason = models.TextField(
        blank=True,
        default="",
        verbose_name=_("dispute reason"),
        help_text=_("Why the milestone was disputed."),
    )

    resolution_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("resolution note"),
        help_text=_("A note about how the dispute was resolved."),
    )

    funded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("funded at"),
        help_text=_("When money was locked in escrow for this milestone."),
    )

    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("submitted at"),
        help_text=_("When the freelancer submitted this milestone."),
    )

    stalled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("stalled at"),
        help_text=_("When the milestone stalled/paused at."),
    )


    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("approved at"),
        help_text=_("When the client approved the submission."),
    )

    released_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("released at"),
        help_text=_("When escrow was released for this milestone."),
    )

    refunded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("refunded at"),
        help_text=_("When this milestone was refunded back to the client."),
    )

    disputed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("disputed at"),
        help_text=_("When this milestone was marked as disputed."),
    )

    review_due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("review due at"),
        help_text=_("When the review window ends and Freewise may auto-finalize the milestone."),
    )

    revision_scope = models.TextField(
        blank=True,
        default="",
        verbose_name=_("revision scope"),
        help_text=_("What should change before the freelancer resubmits."),
    )

    revision_requested_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("revision requested at"),
        help_text=_("When the client asked for a revision."),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("When this milestone was created."),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("When this milestone was last updated."),
    )

    class Meta:
        ordering = ["order", "created_at"]
        verbose_name = _("milestone")
        verbose_name_plural = _("milestones")
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "order"],
                name="unique_milestone_order_per_contract",
            ),
        ]
        indexes = [
            models.Index(fields=["contract", "status"]),
            models.Index(fields=["order"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} — {self.contract.display_name}"

    def clean(self):
        super().clean()

        if self.amount is not None and self.amount <= Decimal("0.00"):
            raise ValidationError({"amount": _("Milestone amount must be greater than zero.")})

        if self.contract_id and self.currency and self.currency != self.contract.currency:
            raise ValidationError(
                {"currency": _("Milestone currency must match the contract currency.")}
            )

class MilestoneSubmission(PublicIDMixin, models.Model):
    """
    Immutable proof of work. Keep the original copy here.
    """

    PUBLIC_ID_PREFIX = "fwms"
    PUBLIC_ID_LENGTH_PREFIX = 16

    milestone = models.ForeignKey(
        Milestone,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="milestone_submissions",
    )

    note = models.TextField(blank=True, default="")
    external_link = models.URLField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=16, default="SUBMITTED", db_index=True)
    submitted_at = models.DateTimeField(auto_now_add=True)


class MilestoneTemplate(models.Model):
    class JobKind(models.TextChoices):
        ANY = "ANY", _("Any")
        WEBSITE = "WEBSITE", _("Website")
        MOBILE_APP = "MOBILE_APP", _("Mobile app")
        DESIGN = "DESIGN", _("Design")
        DEV = "DEV", _("Development")

    class PricingMode(models.TextChoices):
        ANY = "ANY", _("Any")
        FIXED = "FIXED", _("Fixed")
        NEGOTIABLE = "NEGOTIABLE", _("Negotiable")

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)

    job_kind = models.CharField(
        max_length=20,
        choices=JobKind.choices,
        default=JobKind.ANY,
        db_index=True,
    )

    pricing_mode = models.CharField(
        max_length=20,
        choices=PricingMode.choices,
        default=PricingMode.ANY,
        db_index=True,
    )

    category = models.ForeignKey(
        "jobs.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="milestone_templates",
    )

    min_budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    min_duration_days = models.PositiveIntegerField(null=True, blank=True)
    max_duration_days = models.PositiveIntegerField(null=True, blank=True)

    min_steps = models.PositiveSmallIntegerField(default=1)
    max_steps = models.PositiveSmallIntegerField(default=6)

    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveSmallIntegerField(default=100, db_index=True)

    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "created_at"]

    def __str__(self):
        return self.name


class MilestoneTemplateStep(models.Model):
    class DeliverableType(models.TextChoices):
        LINK = "LINK", _("Link")
        FILE = "FILE", _("File")
        DEMO = "DEMO", _("Demo")
        SCREENSHOT = "SCREENSHOT", _("Screenshot")
        REPO = "REPO", _("Repo")
        HANDOFF = "HANDOFF", _("Handoff")

    template = models.ForeignKey(
        MilestoneTemplate,
        on_delete=models.CASCADE,
        related_name="steps",
    )

    title = models.CharField(max_length=255)
    outcome_summary = models.TextField(blank=True, default="")
    acceptance_criteria = models.TextField(blank=True, default="")
    client_visible_note = models.TextField(blank=True, default="")
    internal_note = models.TextField(blank=True, default="")

    percent = models.DecimalField(max_digits=5, decimal_places=2)
    order = models.PositiveSmallIntegerField()
    is_final = models.BooleanField(default=False)

    deliverable_type = models.CharField(
        max_length=20,
        choices=DeliverableType.choices,
        default=DeliverableType.HANDOFF,
    )

    default_due_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["template", "order"], name="unique_template_step_order"),
        ]

    def __str__(self):
        return f"{self.template.name} - {self.title}"