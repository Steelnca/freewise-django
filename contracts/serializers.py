"""
Freewise contract serializers.

These serializers are presentation-only.
All state changes happen in contracts/services.py.
"""

from rest_framework import serializers
from decimal import Decimal

from .models import Contract, Milestone


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Milestone
        fields = (
            "id",
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
            "refunded_at",
            "disputed_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


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
    first_pending_milestone_id = serializers.SerializerMethodField()
    first_funded_milestone_id = serializers.SerializerMethodField()
    has_dispute = serializers.SerializerMethodField()
    is_funding_locked = serializers.SerializerMethodField()
    is_finished = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = (
            "id",
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
            "first_pending_milestone_id",
            "first_funded_milestone_id",
            "has_dispute",
            "is_funding_locked",
            "is_finished",
            "funded_at",
            "active_at",
            "submitted_at",
            "completed_at",
            "cancelled_at",
            "disputed_at",
            "released_at",
            "refunded_at",
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
        total = self.get_milestone_total(obj)
        if not obj.agreed_price:
            return 0
        return int((total / obj.agreed_price) * 100)

    def get_first_pending_milestone_id(self, obj):
        milestone = obj.milestones.filter(status=Milestone.Status.PENDING).order_by("order", "created_at").first()
        return milestone.id if milestone else None

    def get_first_funded_milestone_id(self, obj):
        milestone = obj.milestones.filter(status=Milestone.Status.FUNDED).order_by("order", "created_at").first()
        return milestone.id if milestone else None

    def get_has_dispute(self, obj):
        if obj.status == Contract.Status.DISPUTED:
            return True
        return obj.milestones.filter(status=Milestone.Status.DISPUTED).exists()

    def get_is_funding_locked(self, obj):
        return obj.status != Contract.Status.PENDING_FUNDING

    def get_is_finished(self, obj):
        return obj.status in {Contract.Status.RELEASED, Contract.Status.REFUNDED}

class MilestoneActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    submission_link = serializers.URLField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    fee_amount = serializers.DecimalField(
        required=False,
        max_digits=14,
        decimal_places=2,
        default=0,
    )

class MilestoneCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    due_date = serializers.DateField()
    order = serializers.IntegerField(min_value=1)