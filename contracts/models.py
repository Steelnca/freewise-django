"""
Freewise contract models.

This module keeps contracts and milestones stateful, but not smart.
Business rules belong in contracts/services.py.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


DEFAULT_CURRENCY = "DZD"


class Contract(models.Model):
    """
    A contract is the agreement between a client and a freelancer.

    It can originate from a job board proposal or a future service-order flow.
    """

    class SourceType(models.TextChoices):
        JOB_BOARD = "job_board", _("Job board")
        SERVICE_ORDER = "service_order", _("Service order")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PENDING_FUNDING = "pending_funding", _("Pending funding")
        FUNDED = "funded", _("Funded")
        ACTIVE = "active", _("Active")
        SUBMITTED = "submitted", _("Submitted")
        REVISION_REQUESTED = "revision_requested", _("Revision requested")
        APPROVED = "approved", _("Approved")
        DISPUTED = "disputed", _("Disputed")
        RELEASED = "released", _("Released")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    # --- Source of the contract ---
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.JOB_BOARD,
        db_index=True,
        verbose_name=_("source type"),
        help_text=_("Where this contract came from."),
    )

    job = models.OneToOneField(
        "jobs.Job",
        on_delete=models.PROTECT,
        related_name="contract",
        null=True,
        blank=True,
        verbose_name=_("job"),
        help_text=_("Linked job post, if the contract came from the job board."),
    )

    proposal = models.OneToOneField(
        "proposals.Proposal",
        on_delete=models.PROTECT,
        related_name="contract",
        null=True,
        blank=True,
        verbose_name=_("proposal"),
        help_text=_("Linked proposal, if the contract came from a proposal."),
    )

    # --- Parties ---
    client = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.PROTECT,
        related_name="contracts",
        verbose_name=_("client"),
        help_text=_("The client side of the contract."),
    )

    freelancer = models.ForeignKey(
        "freelancers.FreelancerProfile",
        on_delete=models.PROTECT,
        related_name="contracts",
        verbose_name=_("freelancer"),
        help_text=_("The freelancer side of the contract."),
    )

    # --- Display / terms ---
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("title"),
        help_text=_("Short human-readable contract title."),
    )

    currency = models.CharField(
        max_length=3,
        default=DEFAULT_CURRENCY,
        verbose_name=_("currency"),
        help_text=_("Contract currency code. Freewise starts with DZD."),
    )

    agreed_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("agreed price"),
        help_text=_("Total agreed amount for the contract."),
    )

    deadline = models.DateField(
        verbose_name=_("deadline"),
        help_text=_("Delivery deadline agreed by both parties."),
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name=_("status"),
        help_text=_("Current lifecycle state of the contract."),
    )

    notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("notes"),
        help_text=_("Internal notes or summary for moderators and admins."),
    )

    # --- Lifecycle timestamps ---
    funded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("funded at"),
        help_text=_("When the contract was funded into escrow."),
    )
    active_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("active at"),
        help_text=_("When the work officially started."),
    )
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("submitted at"),
        help_text=_("When the first milestone or final work was submitted."),
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("completed at"),
        help_text=_("When the contract fully completed and funds were released or refunded."),
    )
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("cancelled at"),
        help_text=_("When the contract was cancelled."),
    )
    disputed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("disputed at"),
        help_text=_("When the contract entered dispute."),
    )
    released_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("released at"),
        help_text=_("When escrow was fully released."),
    )
    refunded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("refunded at"),
        help_text=_("When escrow was refunded."),
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

        if self.source_type == self.SourceType.SERVICE_ORDER and not self.title:
            raise ValidationError(
                {
                    "title": _(
                        "Service order contracts should have a readable title."
                    )
                }
            )


class Milestone(models.Model):
    """
    Contract milestone.

    Each milestone is tracked independently so Freewise can support
    single-deliverable and multi-phase contracts later without rewrites.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        FUNDED = "funded", _("Funded")
        SUBMITTED = "submitted", _("Submitted")
        REVISION_REQUESTED = "revision_requested", _("Revision requested")
        APPROVED = "approved", _("Approved")
        DISPUTED = "disputed", _("Disputed")
        RELEASED = "released", _("Released")
        REFUNDED = "refunded", _("Refunded")
        CANCELLED = "cancelled", _("Cancelled")

    contract = models.ForeignKey(
        Contract,
        on_delete=models.CASCADE,
        related_name="milestones",
        verbose_name=_("contract"),
        help_text=_("Parent contract for this milestone."),
    )

    title = models.CharField(
        max_length=255,
        verbose_name=_("title"),
        help_text=_("Short milestone title."),
    )

    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Optional milestone description."),
    )

    currency = models.CharField(
        max_length=3,
        default=DEFAULT_CURRENCY,
        verbose_name=_("currency"),
        help_text=_("Milestone currency code."),
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name=_("amount"),
        help_text=_("Milestone amount."),
    )

    due_date = models.DateField(
        verbose_name=_("due date"),
        help_text=_("Expected delivery date for this milestone."),
    )

    order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("order"),
        help_text=_("Used to sort milestones in sequence."),
    )

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name=_("status"),
        help_text=_("Current lifecycle state of the milestone."),
    )

    submission_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("submission note"),
        help_text=_("Optional note from the freelancer when submitting work."),
    )

    review_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("review note"),
        help_text=_("Optional note from the client after review."),
    )

    dispute_reason = models.TextField(
        blank=True,
        default="",
        verbose_name=_("dispute reason"),
        help_text=_("Reason the milestone was disputed."),
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
        help_text=_("When the client approved this milestone."),
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
        help_text=_("When this milestone was refunded."),
    )
    disputed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("disputed at"),
        help_text=_("When this milestone entered dispute."),
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