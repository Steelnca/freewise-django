"""
Freewise contract serializers.

These serializers are presentation-only.
All state changes happen in contracts/services.py.
"""

from rest_framework import serializers

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


class MilestoneActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    fee_amount = serializers.DecimalField(
        required=False,
        max_digits=14,
        decimal_places=2,
        default=0,
    )