"""
Freewise contract serializers.

These serializers are presentation-only.
All state changes happen in contracts/services.py.
"""

from rest_framework import serializers
from decimal import Decimal

from payments.models import PaymentAttempt
from payments.services import refresh_payment_attempt_from_provider

from .models import Contract, Milestone, ContractEvent


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    latest_payment_attempt_id = serializers.SerializerMethodField()
    latest_payment_attempt_status = serializers.SerializerMethodField()
    latest_payment_attempt_provider_status = serializers.SerializerMethodField()
    latest_payment_attempt_checkout_url = serializers.SerializerMethodField()
    latest_payment_attempt_retryable = serializers.SerializerMethodField()

    class Meta:
        model = Milestone
        fields = (
            "id",
            "public_id",
            "title",
            "description",
            "currency",
            "amount",
            "due_date",
            "order",
            "status",
            "status_label",
            "submission_note",
            "submission_link",
            "review_note",
            "dispute_reason",
            "submitted_at",
            "approved_at",
            "released_at",
            "funded_at",
            "refunded_at",
            "disputed_at",
            "revision_scope",
            "revision_requested_at",
            "review_due_at",
            "created_at",
            "updated_at",
            "latest_payment_attempt_id",
            "latest_payment_attempt_status",
            "latest_payment_attempt_provider_status",
            "latest_payment_attempt_checkout_url",
            "latest_payment_attempt_retryable",
        )
        read_only_fields = fields

    def _latest_payment_attempt(self, obj):
        cached = getattr(obj, "_latest_payment_attempt", None)
        if cached is not None:
            return cached

        attempt = (
            obj.payment_attempts.order_by("-attempt_number", "-created_at").first()
        )

        if attempt and not attempt.is_final:
            attempt = refresh_payment_attempt_from_provider(attempt=attempt)

        obj._latest_payment_attempt = attempt
        return attempt

    def get_latest_payment_attempt_id(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return str(attempt.attempt_id) if attempt else None

    def get_latest_payment_attempt_status(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.internal_status if attempt else None

    def get_latest_payment_attempt_provider_status(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.provider_status if attempt else None

    def get_latest_payment_attempt_checkout_url(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.provider_checkout_url if attempt else None

    def get_latest_payment_attempt_retryable(self, obj):
        attempt = self._latest_payment_attempt(obj)
        if not attempt:
            return False

        return attempt.internal_status in {
            PaymentAttempt.InternalStatus.FAILED,
            PaymentAttempt.InternalStatus.CANCELED,
            PaymentAttempt.InternalStatus.EXPIRED,
        }

class ContractSerializer(serializers.ModelSerializer):
    client_username = serializers.CharField(source="client.account.user.username", read_only=True)
    freelancer_username = serializers.CharField(source="freelancer.account.user.username", read_only=True)
    job_title = serializers.CharField(source="job.title", read_only=True, allow_null=True)
    source_type_label = serializers.CharField(source="get_source_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.SerializerMethodField()
    milestones = MilestoneSerializer(many=True, read_only=True)

    viewer_role = serializers.SerializerMethodField()
    viewer_is_client = serializers.SerializerMethodField()
    viewer_is_freelancer = serializers.SerializerMethodField()

    milestone_total = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    funding_progress = serializers.SerializerMethodField()
    first_pending_milestone_public_id = serializers.SerializerMethodField()
    first_funded_milestone_public_id = serializers.SerializerMethodField()
    has_suspension = serializers.SerializerMethodField()
    is_funding_locked = serializers.SerializerMethodField()
    is_finished = serializers.SerializerMethodField()
    next_action = serializers.SerializerMethodField()
    next_action_milestone_public_id = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = (
            "id",
            "public_id",
            "source_type",
            "source_type_label",
            "job",
            "job_title",
            "proposal",
            "title",
            "source_label",
            "client_username",
            "freelancer_username",
            "currency",
            "agreed_price",
            "deadline",
            "status",
            "status_label",
            "notes",
            "milestones",
            "viewer_role",
            "viewer_is_client",
            "viewer_is_freelancer",
            "milestone_total",
            "remaining_amount",
            "funding_progress",
            "first_pending_milestone_public_id",
            "first_funded_milestone_public_id",
            "has_suspension",
            "is_funding_locked",
            "is_finished",
            "next_action",
            "next_action_milestone_public_id",
            "active_at",
            "completed_at",
            "cancelled_at",
            "suspended_at",
            "withdrawn_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_source_label(self, obj):
        return obj.display_name

    def _viewer_role(self, obj):
        request = self.context.get("request")
        if not request or not hasattr(request.user, "account"):
            return None

        account = request.user.account
        client_profile = getattr(account, "client_profile", None)
        freelancer_profile = getattr(account, "freelancer_profile", None)

        if client_profile and obj.client_id == client_profile.id:
            return "client"
        if freelancer_profile and obj.freelancer_id == freelancer_profile.id:
            return "freelancer"
        return None

    def get_viewer_role(self, obj):
        return self._viewer_role(obj)

    def get_viewer_is_client(self, obj):
        return self._viewer_role(obj) == "client"

    def get_viewer_is_freelancer(self, obj):
        return self._viewer_role(obj) == "freelancer"

    def get_milestone_total(self, obj):
        return sum((m.amount for m in obj.milestones.all()), Decimal("0.00"))

    def get_remaining_amount(self, obj):
        return obj.agreed_price - self.get_milestone_total(obj)

    def get_funding_progress(self, obj):
        total = obj.funded_balance + obj.released_amount
        if obj.agreed_price > 0:
            return (total / obj.agreed_price) * 100
        return 0

    def get_first_pending_milestone_public_id(self, obj):
        milestone = (
            obj.milestones
            .filter(status=Milestone.Status.PENDING)
            .order_by("order", "created_at")
            .first()
        )
        return milestone.public_id if milestone else None

    def get_first_funded_milestone_public_id(self, obj):
        milestone = (
            obj.milestones
            .filter(status=Milestone.Status.FUNDED)
            .order_by("order", "created_at")
            .first()
        )
        return milestone.public_id if milestone else None

    def get_has_suspension(self, obj):
        if obj.status == Contract.Status.SUSPENDED:
            return True
        return obj.milestones.filter(status=Milestone.Status.DISPUTED).exists()

    def get_is_funding_locked(self, obj):
        return obj.status != Contract.Status.PENDING_FUNDING

    def get_is_finished(self, obj):
        return obj.status in {
            Contract.Status.COMPLETED,
            Contract.Status.WITHDRAWN,
            Contract.Status.CANCELLED,
        }

    def get_next_action(self, obj):
        role = self._viewer_role(obj)

        if self.get_has_suspension(obj):
            return "under_suspension"

        if self.get_is_finished(obj):
            return "completed"

        first_pending = (
            obj.milestones.filter(status=Milestone.Status.PENDING)
            .order_by("order", "created_at")
            .first()
        )
        first_funded = (
            obj.milestones.filter(status=Milestone.Status.FUNDED)
            .order_by("order", "created_at")
            .first()
        )
        first_submitted = (
            obj.milestones.filter(status=Milestone.Status.SUBMITTED)
            .order_by("order", "created_at")
            .first()
        )
        first_revision_requested = (
            obj.milestones.filter(status=Milestone.Status.REVISION_REQUESTED)
            .order_by("order", "created_at")
            .first()
        )

        if role == "client":
            if obj.status == Contract.Status.PENDING_FUNDING:
                if first_pending:
                    return "split_or_edit_milestones_then_fund"
                return "create_milestone_then_fund"

            if first_revision_requested:
                return "review_revision_request"

            if first_submitted:
                return "review_submission"

            return "waiting_for_freelancer"

        if role == "freelancer":
            if first_revision_requested:
                return "submit_revision"

            if first_funded:
                return "submit_funded_milestone"

            return "waiting_for_client_funding"

        return "no_access"

    def get_next_action_milestone_public_id(self, obj):
        ordered = obj.milestones.order_by("order", "created_at")

        revision = ordered.filter(status=Milestone.Status.REVISION_REQUESTED).first()
        if revision:
            return revision.public_id

        submitted = ordered.filter(status=Milestone.Status.SUBMITTED).first()
        if submitted:
            return submitted.public_id

        funded = ordered.filter(status=Milestone.Status.FUNDED).first()
        if funded:
            return funded.public_id

        pending = ordered.filter(status=Milestone.Status.PENDING).first()
        if pending:
            return pending.public_id

        return None

class MilestoneActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    revision_scope = serializers.CharField(required=False, allow_blank=True, default="")
    submission_link = serializers.URLField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    fee_amount = serializers.DecimalField(required=False, max_digits=14, decimal_places=2, default=0)

class MilestoneCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    due_date = serializers.DateField()
    order = serializers.IntegerField(min_value=1)

class ContractEventSerializer(serializers.ModelSerializer):
    event_type = serializers.CharField(read_only=True)
    actor_username = serializers.CharField(source="actor.account.user.username", read_only=True)
    metadata = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ContractEvent
        fields = (
            "id",
            "event_type",
            "actor_username",
            "metadata",
            "created_at",
        )
        read_only_fields = fields