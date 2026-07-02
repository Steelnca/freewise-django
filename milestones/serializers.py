"""
Milestone API serializers.

Serializers validate request shape and present API data.
All state transitions stay in milestones/services.py and contracts/services.py.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from payments.services import refresh_payment_attempt_from_provider

from .models import Milestone, MilestonePlan, MilestonePlanItem, MilestoneSubmission


PLAN_EDITABLE_STATUSES = {
    MilestonePlan.Status.DRAFT,
    MilestonePlan.Status.PROPOSED,
}


class MilestonePlanItemInputSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    due_date = serializers.DateField(required=False, allow_null=True)
    order = serializers.IntegerField(min_value=1)
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_amount(self, value):
        if Decimal(str(value)) <= Decimal("0"):
            raise serializers.ValidationError(_("Amount must be greater than zero."))
        return value

    def validate_metadata(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Metadata must be an object."))
        return value


class MilestonePlanItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MilestonePlanItem
        fields = [
            "public_id",
            "title",
            "description",
            "amount",
            "due_date",
            "order",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "public_id",
            "status",
            "created_at",
            "updated_at",
        ]


class MilestonePlanSerializer(serializers.ModelSerializer):
    items = MilestonePlanItemSerializer(many=True, read_only=True)

    job_public_id = serializers.CharField(source="job.public_id", read_only=True)
    proposal_public_id = serializers.CharField(
        source="proposal.public_id",
        read_only=True,
        allow_null=True,
    )
    created_by_public_id = serializers.CharField(
        source="created_by.public_id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = MilestonePlan
        fields = [
            "public_id",
            "job_public_id",
            "proposal_public_id",
            "created_by_public_id",
            "source_role",
            "status",
            "note",
            "total_amount",
            "currency",
            "suggestion_enabled",
            "is_selected",
            "selected_at",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MilestonePlanWriteSerializer(serializers.Serializer):
    """
    Used by both plan creation and plan editing.

    job_public_id belongs in the URL.
    proposal_public_id is optional and belongs in the request body.
    """

    proposal_public_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")
    suggestion_enabled = serializers.BooleanField(required=False, default=True)
    items = MilestonePlanItemInputSerializer(many=True)

    def validate_proposal_public_id(self, value):
        return str(value or "").strip() or None

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError(
                _("Milestone plan must contain at least one item.")
            )

        orders = [item["order"] for item in items]
        if len(orders) != len(set(orders)):
            raise serializers.ValidationError(
                _("Each milestone item must have a unique order.")
            )

        return sorted(items, key=lambda item: item["order"])


class MilestonePlanPatchSerializer(serializers.Serializer):
    """
    Only allows editing content, never direct workflow/status mutation.
    """

    note = serializers.CharField(required=False, allow_blank=True)
    suggestion_enabled = serializers.BooleanField(required=False)
    items = MilestonePlanItemInputSerializer(many=True, required=False)

    def validate(self, attrs):
        plan = self.context["plan"]

        if plan.status not in PLAN_EDITABLE_STATUSES:
            raise serializers.ValidationError(
                {"detail": _("This milestone plan can no longer be edited.")}
            )

        if "items" in attrs:
            items = attrs["items"]

            if not items:
                raise serializers.ValidationError(
                    {"items": _("Milestone plan must contain at least one item.")}
                )

            orders = [item["order"] for item in items]
            if len(orders) != len(set(orders)):
                raise serializers.ValidationError(
                    {"items": _("Each milestone item must have a unique order.")}
                )

            attrs["items"] = sorted(items, key=lambda item: item["order"])

        return attrs


class MilestoneActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    submission_note = serializers.CharField(required=False, allow_blank=True, default="")
    submission_link = serializers.URLField(required=False, allow_blank=True)
    revision_note = serializers.CharField(required=False, allow_blank=True, default="")
    revision_scope = serializers.CharField(required=False, allow_blank=True, default="")
    review_note = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    payload = serializers.JSONField(required=False)

    def validate_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Submission payload must be an object."))
        return value

    def validate(self, attrs):
        action = str(self.context.get("action") or "").strip().lower()

        if attrs.get("submission_note") and not attrs.get("note"):
            attrs["note"] = attrs["submission_note"]

        if attrs.get("note") and not attrs.get("submission_note"):
            attrs["submission_note"] = attrs["note"]

        if action == "submit":
            has_submission = any(
                [
                    attrs.get("submission_note"),
                    attrs.get("submission_link"),
                    attrs.get("payload") is not None,
                ]
            )
            if not has_submission:
                raise serializers.ValidationError(
                    {
                        "detail": _(
                            "Add a submission note, submission link, or submission data."
                        )
                    }
                )

        elif action == "request_revision":
            if not attrs.get("revision_note") and not attrs.get("revision_scope"):
                raise serializers.ValidationError(
                    {
                        "detail": _(
                            "Add a revision note or describe the revision scope."
                        )
                    }
                )

        elif action == "dispute":
            if not attrs.get("reason"):
                raise serializers.ValidationError(
                    {"reason": _("A dispute reason is required.")}
                )

        return attrs


class MilestoneSubmissionSerializer(serializers.ModelSerializer):
    milestone_public_id = serializers.CharField(source="milestone.public_id", read_only=True)

    class Meta:
        model = MilestoneSubmission
        fields = [
            "public_id",
            "milestone_public_id",
            "note",
            "external_link",
            "payload",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MilestoneSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    latest_payment_attempt_id = serializers.SerializerMethodField()
    latest_payment_attempt_internal_status = serializers.SerializerMethodField()
    latest_payment_attempt_provider_status = serializers.SerializerMethodField()
    latest_payment_attempt_checkout_url = serializers.SerializerMethodField()
    latest_payment_attempt_retryable = serializers.SerializerMethodField()

    source_plan_item_public_id = serializers.CharField(
        source="proposal.public_id",
        read_only=True,
        allow_null=True,
    )

    can_submit = serializers.SerializerMethodField()
    can_fund = serializers.SerializerMethodField()
    can_approve = serializers.SerializerMethodField()
    can_request_revision = serializers.SerializerMethodField()
    can_dispute = serializers.SerializerMethodField()

    class Meta:
        model = Milestone
        fields = [
            "public_id",
            "source_plan_item_public_id",
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
            "revision_note",
            "revision_scope",
            "dispute_reason",
            "submitted_at",
            "approved_at",
            "released_at",
            "funded_at",
            "refunded_at",
            "disputed_at",
            "revision_requested_at",
            "review_due_at",
            "created_at",
            "updated_at",
            "latest_payment_attempt_id",
            "latest_payment_attempt_internal_status",
            "latest_payment_attempt_provider_status",
            "latest_payment_attempt_checkout_url",
            "latest_payment_attempt_retryable",
            "can_submit",
            "can_fund",
            "can_approve",
            "can_request_revision",
            "can_dispute",
        ]
        read_only_fields = fields

    def _latest_payment_attempt(self, obj):
        cached = getattr(obj, "_latest_payment_attempt", None)
        if cached is not None:
            return cached

        attempt = obj.payment_attempts.order_by(
            "-attempt_number",
            "-created_at",
        ).first()

        if attempt is not None:
            try:
                attempt = refresh_payment_attempt_from_provider(attempt)
            except Exception:
                # Payment provider refresh must never break contract detail reads.
                pass

        obj._latest_payment_attempt = attempt
        return attempt

    def get_latest_payment_attempt_id(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return str(attempt.public_id) if attempt else None

    def get_latest_payment_attempt_internal_status(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.status if attempt else None

    def get_latest_payment_attempt_provider_status(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.provider_status if attempt else None

    def get_latest_payment_attempt_checkout_url(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return attempt.checkout_url if attempt else None

    def get_latest_payment_attempt_retryable(self, obj):
        attempt = self._latest_payment_attempt(obj)
        return bool(attempt and attempt.is_retryable)

    def get_can_submit(self, obj):
        return obj.status in {
            Milestone.Status.FUNDED,
            Milestone.Status.REVISION_REQUESTED,
        }

    def get_can_fund(self, obj):
        return obj.status == Milestone.Status.PENDING

    def get_can_approve(self, obj):
        return obj.status == Milestone.Status.SUBMITTED

    def get_can_request_revision(self, obj):
        return obj.status == Milestone.Status.SUBMITTED

    def get_can_dispute(self, obj):
        return obj.status in {
            Milestone.Status.SUBMITTED,
            Milestone.Status.REVISION_REQUESTED,
        }