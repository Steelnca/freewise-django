# payments/serializers.py
"""
Freewise payment serializers.

These serializers are intentionally boring:
- wallets expose balances
- transactions expose the immutable ledger
- escrow holds expose contract locks
- payouts expose withdrawal state
- payout requests validate user input before the service layer touches money
"""

from decimal import Decimal

from rest_framework import serializers

from .models import Wallet, WalletTransaction, EscrowHold, Payout


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = (
            "public_id",
            "transaction_type",
            "status",
            "amount",
            "currency",
            "balance_before",
            "balance_after",
            "reference_type",
            "reference_id",
            "provider_name",
            "provider_reference",
            "description",
            "metadata",
            "created_at",
        )
        read_only_fields = fields


class EscrowHoldSerializer(serializers.ModelSerializer):
    funding_transaction_id = serializers.SerializerMethodField()
    resolution_transaction_id = serializers.SerializerMethodField()

    class Meta:
        model = EscrowHold
        fields = (
            "public_id",
            "contract_reference",
            "amount",
            "currency",
            "status",
            "idempotency_key",
            "funding_transaction_id",
            "resolution_transaction_id",
            "resolution_note",
            "resolved_at",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_funding_transaction_id(self, obj):
        return obj.funding_transaction_id

    def get_resolution_transaction_id(self, obj):
        return obj.resolution_transaction_id


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = (
            "id",
            "amount",
            "currency",
            "status",
            "idempotency_key",
            "provider_name",
            "provider_reference",
            "destination_type",
            "destination_label",
            "failure_reason",
            "processed_at",
            "metadata",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = (
            "id",
            "currency",
            "available_balance",
            "escrow_balance",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class PayoutRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    idempotency_key = serializers.CharField(max_length=120)
    provider_name = serializers.CharField(required=False, allow_blank=True, default="")
    provider_reference = serializers.CharField(required=False, allow_blank=True, default="")
    destination_type = serializers.CharField(required=False, allow_blank=True, default="")
    destination_label = serializers.CharField(required=False, allow_blank=True, default="")
    description = serializers.CharField(required=False, allow_blank=True, default="")
    metadata = serializers.JSONField(required=False, default=dict)