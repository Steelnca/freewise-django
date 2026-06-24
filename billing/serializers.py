from __future__ import annotations

from rest_framework import serializers

from .models import SubscriptionPlan, SubscriptionPlanPrice, FreelancerSubscription, ClientSubscription

class SubscriptionPlanPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlanPrice
        fields = [
            "public_id",
            "billing_cycle",
            "price",
            "is_active",
        ]

class SubscriptionPlanSerializer(serializers.ModelSerializer):
    prices = SubscriptionPlanPriceSerializer(many=True, read_only=True)

    class Meta:
        model = SubscriptionPlan
        fields = [
            "public_id",
            "role",
            "name",
            "slug",
            "description",
            "max_open_bids",
            "max_active_contracts",
            "max_jobs_posted",
            "max_active_jobs",
            "is_active",
            "is_default",
            "prices",
        ]


class FreelancerSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = FreelancerSubscription
        fields = [
            "public_id",
            "status",
            "starts_at",
            "ends_at",
            "auto_renew",
            "provider_name",
            "provider_reference",
            "plan",
        ]


class ClientSubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = ClientSubscription
        fields = [
            "public_id",
            "status",
            "starts_at",
            "ends_at",
            "auto_renew",
            "provider_name",
            "provider_reference",
            "plan",
        ]

class FreelancerQuotaSerializer(serializers.Serializer):
    plan_name = serializers.CharField()
    plan_slug = serializers.CharField()
    open_bids = serializers.IntegerField()
    active_contracts = serializers.IntegerField()
    max_open_bids = serializers.IntegerField()
    max_active_contracts = serializers.IntegerField()
    can_create_proposal = serializers.BooleanField()
    can_take_contract = serializers.BooleanField()


class ClientQuotaSerializer(serializers.Serializer):
    plan_name = serializers.CharField()
    plan_slug = serializers.CharField()
    posted_jobs = serializers.IntegerField()
    active_jobs = serializers.IntegerField()
    max_jobs_posted = serializers.IntegerField()
    max_active_jobs = serializers.IntegerField()
    can_post_job = serializers.BooleanField()
    can_keep_active_job = serializers.BooleanField()


class ActivateSubscriptionSerializer(serializers.Serializer):
    plan_public_id = serializers.CharField()
    auto_renew = serializers.BooleanField(required=False, default=False)
    provider_name = serializers.CharField(required=False, allow_blank=True, default="")
    provider_reference = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_plan_public_id(self, value):
        try:
            return SubscriptionPlan.objects.get(public_id=value, is_active=True)
        except SubscriptionPlan.DoesNotExist as exc:
            raise serializers.ValidationError("Active subscription plan not found.") from exc

    def validate(self, attrs):
        role = self.context.get("role")
        plan = attrs["plan_public_id"]
        if role and plan.role != role:
            raise serializers.ValidationError({"plan_public_id": "This plan is not valid for this role."})
        return attrs