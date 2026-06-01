"""
Freewise contract models.

This module keeps contracts and milestones stateful, but not smart.
Business rules belong in contracts/services.py.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _


DEFAULT_CURRENCY = "DZD"


class Contract(models.Model):
    """
    A contract is the agreement between a client and a freelancer.

    It can originate from a job board proposal or a future service-order flow.
    """

    class SourceType(models.TextChoices):
        JOB_BOARD = "JOB_BOARD", _("Job board")
        SERVICE_ORDER = "SERVICE_ORDER", _("Service order")

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        PENDING_FUNDING = "PENDING_FUNDING", _("Pending funding")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        SUSPENDED = "SUSPENDED", _("Suspended")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")
        COMPLETED = "COMPLETED", _("Completed")
        CANCELLED = "CANCELLED", _("Cancelled")

    # --- Source of the contract ---
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.JOB_BOARD,
        db_index=True,
        verbose_name=_("source type"),
        help_text=_("Where this contract started."),
    )

    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.PROTECT,
        related_name="contract",
        null=True,
        blank=True,
        verbose_name=_("job"),
        help_text=_("Linked job post, if this contract came from the job board."),
    )

    proposal = models.OneToOneField(
        "proposals.Proposal",
        on_delete=models.PROTECT,
        related_name="contract",
        null=True,
        blank=True,
        verbose_name=_("proposal"),
        help_text=_("Accepted proposal for this contract, if any."),
    )

    # --- Parties ---
    client = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.PROTECT,
        related_name="contracts",
        verbose_name=_("client"),
        help_text=_("The client on this contract."),
    )

    freelancer = models.ForeignKey(
        "freelancers.FreelancerProfile",
        on_delete=models.PROTECT,
        related_name="contracts",
        verbose_name=_("freelancer"),
        help_text=_("The freelancer on this contract."),
    )

    # --- Display / terms ---
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("title"),
        help_text=_("Short title shown in the dashboard."),
    )

    currency = models.CharField(
        max_length=3,
        default=DEFAULT_CURRENCY,
        verbose_name=_("currency"),
        help_text=_("Three-letter currency code used for this contract."),
    )

    agreed_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("agreed price"),
        help_text=_("The full amount the client and freelancer agreed on."),
    )

    deadline = models.DateField(
        verbose_name=_("deadline"),
        help_text=_("The final delivery date both parties agreed to."),
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name=_("status"),
        help_text=_("The overall stage of the contract."),
    )

    notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("notes"),
        help_text=_("Private notes for support or moderation."),
    )

    # --- Lifecycle timestamps ---
    active_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("active at"),
        help_text=_("When the contract moved into active work."),
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("completed at"),
        help_text=_("When the contract fully finished."),
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("cancelled at"),
        help_text=_("When the contract was stopped before it started."),
    )

    suspended_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("suspended at"),
        help_text=_("When the contract was paused because of a dispute or review."),
    )

    withdrawn_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("withdrawn at"),
        help_text=_("When the contract ended early after work had started."),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("When this contract was created."),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("When this contract was last updated."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("contract")
        verbose_name_plural = _("contracts")
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["source_type"]),
            models.Index(fields=["client"]),
            models.Index(fields=["freelancer"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        """
        A stable human-readable label for UI and admin.
        """
        if self.title:
            return self.title

        if self.job_id and hasattr(self.job, "title"):
            return self.job.title

        if self.proposal_id and hasattr(self.proposal, "job") and self.proposal.job_id:
            return getattr(self.proposal.job, "title", f"Contract #{self.pk}")

        return f"Contract #{self.pk}"

    def clean(self):
        super().clean()

        if self.agreed_price is not None and self.agreed_price <= Decimal("0.00"):
            raise ValidationError(
                {"agreed_price": _("Agreed price must be greater than zero.")}
            )

        if self.source_type == self.SourceType.JOB_BOARD and not (self.job_id or self.proposal_id):
            raise ValidationError(
                {
                    "job": _(
                        "Job board contracts must be linked to a job or proposal."
                    )
                }
            )

        if self.source_type == self.SourceType.SERVICE_ORDER:
            if self.job_id or self.proposal_id:
                raise ValidationError(
                    {
                        "job": _(
                            "Service-order contracts should not be linked to a job or proposal."
                        )
                    }
                )
            if not self.title:
                raise ValidationError(
                    {
                        "title": _(
                            "Service-order contracts need a short title so they are easy to recognize."
                        )
                    }
                )

    @property
    def total_amount(self) -> Decimal:
        return self.agreed_price

    @property
    def funded_balance(self) -> Decimal:
        """
        Money currently locked in escrow for this contract.
        """
        total = self.milestones.filter(
            status__in=[
                Milestone.Status.FUNDED,
                Milestone.Status.SUBMITTED,
                Milestone.Status.REVISION_REQUESTED,
                Milestone.Status.DISPUTED,
            ]
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def released_amount(self) -> Decimal:
        total = self.milestones.filter(
            status=Milestone.Status.RELEASED
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def refunded_amount(self) -> Decimal:
        total = self.milestones.filter(
            status=Milestone.Status.REFUNDED
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")

    @property
    def pending_amount(self) -> Decimal:
        total = self.milestones.filter(
            status=Milestone.Status.PENDING
        ).aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0.00")


class Milestone(models.Model):
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
        RELEASED = "RELEASED", _("Released")
        REFUNDED = "REFUNDED", _("Refunded")

    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name="milestones",
        verbose_name=_("contract"),
        help_text=_("The contract this milestone belongs to."),
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


class ContractEvent(models.Model):

    class ContractEventType(models.TextChoices):
        CONTRACT_CREATED = "CONTRACT_CREATED", _("Contract created")
        CONTRACT_FUNDED = "CONTRACT_FUNDED", _("Contract funded")
        CONTRACT_SUSPENDED = "CONTRACT_SUSPENDED", _("Contract suspended")
        CONTRACT_WITHDRAWN = "CONTRACT_WITHDRAWN", _("Contract withdrawn")
        CONTRACT_CANCELLED = "CONTRACT_CANCELLED", _("Contract cancelled")
        CONTRACT_COMPLETED = "CONTRACT_COMPLETED", _("Contract completed")

        MILESTONE_CREATED = "MILESTONE_CREATED", _("Milestone created")
        MILESTONE_FUNDED = "MILESTONE_FUNDED", _("Milestone funded")
        MILESTONE_SUBMITTED = "MILESTONE_SUBMITTED", _("Milestone submitted")
        MILESTONE_REVISION_REQUESTED = "MILESTONE_REVISION_REQUESTED", _("Milestone revision requested")
        MILESTONE_REJECTED = "MILESTONE_REJECTED", _("Milestone rejected")
        MILESTONE_REVISED = "MILESTONE_REVISED", _("Milestone revised")
        MILESTONE_DISPUTED = "MILESTONE_DISPUTED", _("Milestone disputed")
        MILESTONE_APPROVED = "MILESTONE_APPROVED", _("Milestone approved")
        MILESTONE_RELEASED = "MILESTONE_RELEASED", _("Milestone released")
        MILESTONE_REFUNDED = "MILESTONE_REFUNDED", _("Milestone refunded")
        MILESTONE_DISPUTE_RESOLVED_TO_CLIENT = "MILESTONE_DISPUTE_RESOLVED_TO_CLIENT", _("Milestone dispute resolved to client")
        MILESTONE_DISPUTE_RESOLVED_TO_FREELANCER = "MILESTONE_DISPUTE_RESOLVED_TO_FREELANCER", _("Milestone dispute resolved to freelancer")

    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("contract"),
        help_text=_("The contract this activity belongs to."),
    )
    event_type = models.CharField(
        max_length=64,
        choices=ContractEventType.choices,
        verbose_name=_("event type"),
        help_text=_("What happened in the contract timeline."),
    )
    actor = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_("actor"),
        help_text=_("The user who triggered this event, if any."),
    )
    message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("message"),
        help_text=_("Optional human-readable note about this event."),
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Extra structured details for the event timeline."),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("When this event was recorded."),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contract", "event_type"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} — {self.contract.display_name}"