from decimal import Decimal

from rest_framework import serializers
from django.db import transaction
from django.utils.translation import gettext as _

from contracts.models import MilestonePlan, MilestonePlanItem
from contracts.serializers import ContractSerializer
from proposals.serializers import ProposalSerializer
from billing.services import assert_can_post_job

from .models import Category, Job, Tag
from .constants import (
    MAX_MILESTONES, FIRST_MILESTONE_MAX_PERCENT, LAST_MILESTONE_MIN_PERCENT,
    _status_value, USER_EDITABLE_STATUSES, SYSTEM_MANAGED_STATUSES, STATUS_TRANSITIONS
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("id", "name", "slug", "icon")


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "slug")


class MilestonePlanItemInputSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    due_date = serializers.DateField()
    order = serializers.IntegerField(min_value=1)
    metadata = serializers.DictField(required=False, default=dict)


class MilestonePlanInputSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    suggestion_enabled = serializers.BooleanField(default=True)
    items = MilestonePlanItemInputSerializer(many=True)


class MilestonePlanItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MilestonePlanItem
        fields = (
            "public_id",
            "plan",
            "title",
            "description",
            "amount",
            "due_date",
            "order",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "status", "created_at", "updated_at")


class MilestonePlanSerializer(serializers.ModelSerializer):
    items = MilestonePlanItemSerializer(many=True, read_only=True)

    class Meta:
        model = MilestonePlan
        fields = (
            "public_id",
            "job",
            "proposal",
            "created_by",
            "source_role",
            "status",
            "note",
            "suggestion_enabled",
            "currency",
            "total_amount",
            "is_selected",
            "items",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "total_amount", "is_selected", "created_at", "updated_at")


class JobSerializer(serializers.ModelSerializer):
    client_username = serializers.CharField(source="client.account.user.username", read_only=True)
    client_slug = serializers.CharField(source="client.account.slug", read_only=True)
    category = CategorySerializer(read_only=True)
    tags = TagSerializer(many=True, read_only=True)
    proposal_count = serializers.SerializerMethodField()
    milestone_plans = MilestonePlanSerializer(many=True, read_only=True)
    allow_milestone_suggestions = serializers.BooleanField(read_only=True)

    class Meta:
        model = Job
        fields = (
            "public_id",
            "client_username",
            "client_slug",
            "title",
            "description",
            "category",
            "tags",
            "experience_level",
            "pricing_mode",
            "split_owner",
            "collab_allowed",
            "budget_total",
            "deadline",
            "status",
            "proposal_count",
            "milestone_plans",
            "allow_milestone_suggestions",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "status", "proposal_count", "created_at", "updated_at")

    def get_proposal_count(self, obj):
        annotated_count = getattr(obj, "proposal_count", None)
        if annotated_count is not None:
            return annotated_count
        if not obj.pk:
            return 0
        return obj.proposals.count()


class JobWriteSerializer(serializers.ModelSerializer):
    category = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Category.objects.all(),
        required=False,
        allow_null=True,
    )

    pricing_mode = serializers.ChoiceField(
        choices=Job.PricingMode.choices
    )

    budget_total = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    allow_milestone_suggestions = serializers.BooleanField(required=False)

    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        source="tags",
        many=True,
        required=False,
    )

    milestone_plan = MilestonePlanInputSerializer(required=False, allow_null=True)

    publish = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Job
        fields = (
            "title",
            "description",
            "category",
            "tag_ids",
            "experience_level",
            "pricing_mode",
            "budget_total",
            "deadline",
            "allow_milestone_suggestions",
            "milestone_plan",
            "publish",
        )

    def validate_budget_total(self, value):
        if value is None or Decimal(str(value)) <= 0:
            raise serializers.ValidationError({
                "budget_total": _("Budget total is required and must be greater than zero.")
            })
        return value

    def validate(self, attrs):
        pricing_mode = attrs.get("pricing_mode", getattr(self.instance, "pricing_mode", None))
        budget_total = attrs.get("budget_total", getattr(self.instance, "budget_total", None))

        if pricing_mode not in Job.PricingMode.values:
            raise serializers.ValidationError({
                "pricing_mode": _("Choose fixed or negotiable pricing.")
            })

        plan = attrs.get("milestone_plan")
        allow_suggestions = attrs.get(
            "allow_milestone_suggestions",
            getattr(self.instance, "allow_milestone_suggestions", True),
        )

        if not plan and not allow_suggestions:
            raise serializers.ValidationError({
                "allow_milestone_suggestions": _("Enable suggestions when no milestone plan is provided.")
            })

        if plan:
            if self.instance is not None and hasattr(self.instance, "contract"):
                raise serializers.ValidationError(
                    {"milestone_plan": _("Milestone plans cannot be changed after a contract has been created.")}
                )

            items = plan.get("items") or []
            if not items:
                raise serializers.ValidationError(
                    {"milestone_plan": _("Milestone plan must contain at least one item.")}
                )

            if len(items) > MAX_MILESTONES:
                raise serializers.ValidationError(
                    {"milestone_plan": _(f"Maximum {MAX_MILESTONES} milestones are allowed.")}
                )

            total_items_amount = Decimal("0.00")
            for item in items:
                total_items_amount += Decimal(str(item["amount"]))

            if Decimal(str(budget_total)) != total_items_amount:
                raise serializers.ValidationError(
                    {"budget_total": _("The budget amount does not match the total milestone amount.")}
                )

            if len(items) > 1:
                first_cap = total_items_amount * FIRST_MILESTONE_MAX_PERCENT / Decimal("100")
                last_floor = total_items_amount * LAST_MILESTONE_MIN_PERCENT / Decimal("100")

                if Decimal(str(items[0]["amount"])) > first_cap:
                    raise serializers.ValidationError(
                        {"milestone_plan": _("The first milestone is too large.")}
                    )

                if Decimal(str(items[-1]["amount"])) < last_floor:
                    raise serializers.ValidationError(
                        {"milestone_plan": _("The last milestone is too small.")}
                    )

            # Client plan becomes the source of truth for the deal amount.
            attrs["budget_total"] = total_items_amount
            attrs["split_owner"] = Job.SplitOwner.CLIENT

        else:
            if budget_total in (None, ""):
                raise serializers.ValidationError(
                    {"budget_total": _("Enter a total deal price when no milestone plan is provided.")}
                )

            attrs["budget_total"] = budget_total
            # No plan from client means the split will come later from freelancer proposal.
            attrs["split_owner"] = Job.SplitOwner.FREELANCER

        publish = attrs.get("publish", False)
        if publish and not attrs.get("title"):
            raise serializers.ValidationError({"title": "Title is required to publish."})

        return attrs

    def _save_plan(self, job, plan_data):
        if not plan_data:
            return

        # Keep one active draft/planned structure per job for MVP.
        MilestonePlan.objects.filter(job=job).delete()

        items_payload = plan_data["items"]
        total = sum((Decimal(str(item["amount"])) for item in items_payload), Decimal("0.00"))

        plan = MilestonePlan.objects.create(
            job=job,
            created_by=self.context["request"].user,
            source_role=MilestonePlan.SourceRole.CLIENT,
            status=MilestonePlan.Status.APPROVED,
            note=plan_data.get("note", ""),
            suggestion_enabled=bool(plan_data.get("suggestion_enabled", True)),
            currency="DZD",
            total_amount=total,
            is_selected=True,
        )

        for item_data in items_payload:
            MilestonePlanItem.objects.create(
                plan=plan,
                title=item_data["title"],
                description=item_data.get("description", ""),
                amount=item_data["amount"],
                due_date=item_data["due_date"],
                order=item_data["order"],
                status=MilestonePlanItem.Status.APPROVED,
                metadata=item_data.get("metadata", {}),
            )

    @transaction.atomic
    def create(self, validated_data):
        plan_data = validated_data.pop("milestone_plan", None)
        tags = validated_data.pop("tags", [])
        job = super().create(validated_data)

        if tags:
            job.tags.set(tags)

        self._save_plan(job, plan_data)
        return job

    @transaction.atomic
    def update(self, instance, validated_data):
        plan_data = validated_data.pop("milestone_plan", None)
        tags = validated_data.pop("tags", None)
        job = super().update(instance, validated_data)

        if tags is not None:
            job.tags.set(tags)

        if plan_data is not None:
            self._save_plan(job, plan_data)

        return job

class JobReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = [
            "public_id",
            "title",
            "description",
            "category",
            "tags",
            "experience_level",
            "pricing_mode",
            "budget_total",
            "deadline",
            "collab_allowed",
            "allow_milestone_suggestions",
            "split_owner",
            "status",
            "created_at",
            "updated_at",
        ]

class JobApplicantWorkspaceSerializer(serializers.Serializer):
    job = JobSerializer()
    proposal = ProposalSerializer()
    selected_plan = MilestonePlanSerializer(allow_null=True, required=False)
    contract = ContractSerializer(allow_null=True, required=False)
