
from rest_framework import serializers
from .models import Contract, Milestone


class MilestoneSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Milestone
        fields = (
            'id', 'title', 'amount', 'due_date', 'order',
            'status', 'created_at', 'submitted_at', 'approved_at',
        )
        read_only_fields = ('status', 'created_at', 'submitted_at', 'approved_at')


class ContractSerializer(serializers.ModelSerializer):
    client_username     = serializers.CharField(source='client.account.user.username', read_only=True)
    freelancer_username = serializers.CharField(source='freelancer.account.user.username', read_only=True)
    job_title           = serializers.CharField(source='job.title', read_only=True)
    milestones          = MilestoneSerializer(many=True, read_only=True)

    class Meta:
        model  = Contract
        fields = (
            'id',
            'job', 'job_title',
            'client_username', 'freelancer_username',
            'agreed_price', 'deadline', 'status',
            'milestones',
            'created_at', 'completed_at',
        )
        read_only_fields = ('status', 'created_at', 'completed_at')