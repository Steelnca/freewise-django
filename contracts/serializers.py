"""
Freewise contract serializers.

These serializers are presentation-only.
All state changes happen in contracts/services.py.
"""

from rest_framework import serializers
from decimal import Decimal

from milestones.models import Milestone
from milestones.serializers import MilestoneSerializer, MilestonePlanSerializer

from .models import Contract, ContractEvent

class ContractSerializer(serializers.ModelSerializer):
    client_username = serializers.CharField(source="client.account.user.username", read_only=True)
    freelancer_username = serializers.CharField(source="freelancer.account.user.username", read_only=True)
    job_public_id = serializers.CharField(source="job.public_id", read_only=True, allow_null=True)
    job_title = serializers.CharField(source="job.title", read_only=True, allow_null=True)
    source_type_label = serializers.CharField(source="get_source_type_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    source_label = serializers.SerializerMethodField()
    milestones = MilestoneSerializer(many=True, read_only=True)
    milestone_mode = serializers.SerializerMethodField()
    milestone_mode_value = serializers.SerializerMethodField()
    collab_allowed = serializers.BooleanField(read_only=True)

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

    active_milestone = serializers.SerializerMethodField()
    source_plan = MilestonePlanSerializer(read_only=True)

    class Meta:
        model = Contract
        fields = (
            "public_id",
            "source_type",
            "source_type_label",
            "job_public_id",
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
            "milestone_mode",
            "milestone_mode_value",
            "collab_allowed",
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

            "active_milestone",
            "source_plan",
        )
        read_only_fields = fields

    def get_source_label(self, obj):
        return obj.display_name

    def get_milestone_mode(self, obj):
        if not obj.source_plan_id:
            return None
        return obj.source_plan.get_mode_display()

    def get_milestone_mode_value(self, obj):
        if not obj.source_plan_id:
            return None
        return obj.source_plan.mode

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

    def get_active_milestone(self, obj):
        milestone = (
            obj.milestones
            .exclude(
                status__in=[
                    Milestone.Status.RELEASED,
                    Milestone.Status.REFUNDED,
                ]
            ).order_by("order").first()
        )

        if not milestone: return None

        return MilestoneSerializer(
            milestone, context=self.context,
        ).data

class ContractEventSerializer(serializers.ModelSerializer):
    event_type = serializers.CharField(read_only=True)
    actor_username = serializers.CharField(source="actor.account.user.username", read_only=True)
    metadata = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ContractEvent
        fields = (
            "public_id",
            "event_type",
            "actor_username",
            "metadata",
            "created_at",
        )
        read_only_fields = fields

