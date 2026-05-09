
from rest_framework import serializers
from .models import EscrowTransaction, Payout, ChargilyWebhookLog


class EscrowTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = EscrowTransaction
        fields = (
            'id', 'milestone', 'amount', 'platform_fee', 'freelancer_gets',
            'chargily_checkout_id', 'status',
            'created_at', 'paid_at', 'released_at',
        )
        read_only_fields = fields


class PayoutSerializer(serializers.ModelSerializer):
    freelancer_username = serializers.CharField(source='freelancer.account.user.username', read_only=True)

    class Meta:
        model  = Payout
        fields = (
            'id', 'freelancer_username', 'amount',
            'status', 'reference', 'created_at', 'paid_at',
        )
        read_only_fields = fields