from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .models import Category, Job, Tag

# If your milestone plan models live in another app, adjust this import.
from contracts.models import MilestonePlan, MilestonePlanItem

MAX_MILESTONES = 10
FIRST_MILESTONE_MAX_PERCENT = Decimal("35")
LAST_MILESTONE_MIN_PERCENT = Decimal("15")


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
    proposal_count = serializers.IntegerField(source="proposals.count", read_only=True)
    milestone_plans = MilestonePlanSerializer(many=True, read_only=True)

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
            "milestone_mode",
            "split_owner",
            "collab_allowed",
            "budget_total",
            "deadline",
            "status",
            "proposal_count",
            "milestone_plans",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("public_id", "status", "proposal_count", "created_at", "updated_at")


class JobWriteSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        source="category",
        required=False,
        allow_null=True,
    )
    tag_ids = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(),
        source="tags",
        many=True,
        required=False,
    )
    milestone_plan = MilestonePlanInputSerializer(required=False, allow_null=True)

    class Meta:
        model = Job
        fields = (
            "title",
            "description",
            "category_id",
            "tag_ids",
            "experience_level",
            "budget_total",
            "deadline",
            "milestone_plan",
        )

    def validate(self, attrs):
        plan = attrs.get("milestone_plan")
        budget_total = attrs.get("budget_total")

        if plan:
            items = plan.get("items") or []
            if not items:
                raise serializers.ValidationError(
                    {"milestone_plan": "Milestone plan must contain at least one item."}
                )

            if len(items) > MAX_MILESTONES:
                raise serializers.ValidationError(
                    {"milestone_plan": f"Maximum {MAX_MILESTONES} milestones are allowed."}
                )

            total = Decimal("0.00")
            for item in items:
                total += Decimal(str(item["amount"]))

            if len(items) > 1:
                first_cap = total * FIRST_MILESTONE_MAX_PERCENT / Decimal("100")
                last_floor = total * LAST_MILESTONE_MIN_PERCENT / Decimal("100")

                if Decimal(str(items[0]["amount"])) > first_cap:
                    raise serializers.ValidationError(
                        {"milestone_plan": "The first milestone is too large."}
                    )

                if Decimal(str(items[-1]["amount"])) < last_floor:
                    raise serializers.ValidationError(
                        {"milestone_plan": "The last milestone is too small."}
                    )

            # Client plan becomes the source of truth for the deal amount.
            attrs["budget_total"] = total
            attrs["milestone_mode"] = (
                Job.MilestoneMode.SINGLE if len(items) == 1 else Job.MilestoneMode.MULTI
            )
            attrs["split_owner"] = Job.SplitOwner.CLIENT
        else:
            if budget_total in (None, ""):
                raise serializers.ValidationError(
                    {"budget_total": "Enter a total deal price when no milestone plan is provided."}
                )

            # No plan from client means the split will come later from freelancer proposal.
            attrs["split_owner"] = Job.SplitOwner.FREELANCER

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
            status=MilestonePlan.Status.PROPOSED,
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
                status=MilestonePlanItem.Status.DRAFT,
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