
from rest_framework import serializers

from contracts.serializers import MilestonePlanSerializer

from .models import Proposal


class ProposalSerializer(serializers.ModelSerializer):
    freelancer_username = serializers.CharField(source='freelancer.account.user.username', read_only=True)
    freelancer_slug = serializers.CharField(source='freelancer.account.slug', read_only=True)
    freelancer_rating = serializers.DecimalField(source='freelancer.rating', max_digits=3, decimal_places=2, read_only=True)
    job_title = serializers.CharField(source='job.title', read_only=True)
    milestone_plans = MilestonePlanSerializer(many=True, read_only=True)

    class Meta:
        model = Proposal
        fields = (
            'public_id',
            'job',
            'job_title',
            'freelancer_username',
            'freelancer_slug',
            'freelancer_rating',
            'cover_letter',
            'proposed_price',
            'delivery_days',
            'milestone_plans',
            'status',
            'created_at',
        )
        read_only_fields = ('public_id', 'status', 'created_at')


class ProposalCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Proposal
        fields = ('cover_letter', 'proposed_price', 'delivery_days')