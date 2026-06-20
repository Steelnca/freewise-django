
from decimal import Decimal

from rest_framework import serializers

from django.utils.translation import gettext as _

from contracts.serializers import MilestonePlanSerializer
from jobs.models import Job

from .models import Proposal


class ProposalSerializer(serializers.ModelSerializer):
    freelancer_username = serializers.CharField(source='freelancer.account.user.username', read_only=True)
    freelancer_slug = serializers.CharField(source='freelancer.account.slug', read_only=True)
    freelancer_rating = serializers.DecimalField(source='freelancer.rating', max_digits=3, decimal_places=2, read_only=True)
    job_public_id = serializers.CharField(source='job.public_id', read_only=True)
    job_title = serializers.CharField(source='job.title', read_only=True)
    milestone_plans = MilestonePlanSerializer(many=True, read_only=True)
    contract_public_id = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = (
            'public_id',
            'job_public_id',
            'job_title',
            'freelancer_username',
            'freelancer_slug',
            'freelancer_rating',
            'contract_public_id',
            'cover_letter',
            'proposed_price',
            'delivery_days',
            'milestone_plans',
            'status',
            'shortlisted_at',
            'contracted_at',
            'created_at',
        )
        read_only_fields = ('public_id', 'status', 'created_at')

    def get_contract_public_id(self, obj):
        contract = getattr(obj, "contract", None)
        return contract.public_id if contract else None

class ProposalCreateSerializer(serializers.ModelSerializer):
    proposed_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
    )

    class Meta:
        model  = Proposal
        fields = ('cover_letter', 'proposed_price', 'delivery_days')

    def validate(self, attrs):

        request = self.context["request"]
        account = getattr(request.user, "account", None)

        if not account or not getattr(account, "is_freelancer", False):
            raise serializers.ValidationError({
                "detail": "Only freelancer accounts can submit proposals."
            })

        if getattr(account, "is_client", False) and not getattr(account, "is_freelancer", False):
            raise serializers.ValidationError({
                "detail": "Client-only accounts cannot submit proposals."
            })

        job = self.context["job"]
        freelancer = getattr(account, "freelancer_profile", None)

        if Proposal.objects.filter(job=job, freelancer=freelancer).exists():
            raise serializers.ValidationError({
                "detail": "You already submitted a proposal for this job."
            })

        proposed_price = attrs.get("proposed_price")

        if job.pricing_mode == Job.PricingMode.FIXED and proposed_price not in (None, ""):
            raise serializers.ValidationError({
                "proposed_price": "This job has a fixed budget and does not accept price proposals."
            })

        if job.pricing_mode == Job.PricingMode.FIXED:
            attrs["proposed_price"] = job.budget_total

        elif job.pricing_mode == Job.PricingMode.NEGOTIABLE:
            proposed_price = attrs.get("proposed_price")
            if proposed_price in (None, ""):
                raise serializers.ValidationError({
                    "proposed_price": _("Proposed price is required for negotiable jobs.")
                })
            if Decimal(str(proposed_price)) <= 0:
                raise serializers.ValidationError({
                    "proposed_price": _("Proposed price must be greater than zero.")
                })
            attrs["proposed_price"] = Decimal(str(proposed_price))

        return attrs

