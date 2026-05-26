"""
Freewise payment models.

This module is intentionally ledger-first:
- Wallet stores the current read-optimized balances.
- WalletTransaction is the immutable audit trail.
- EscrowHold tracks funds locked for a contract.
- Payout tracks withdrawals or releases.
- WebhookLog stores provider callbacks for debugging and reconciliation.

Important:
Do not mutate balances directly anywhere else in the codebase.
All balance changes should happen through controlled service functions
that create WalletTransaction rows inside database transactions.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from .constants import MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES


class Currency(models.TextChoices):
    DZD = "DZD", _("Algerian Dinar")
    USD = "USD", _("US Dollar")
    EUR = "EUR", _("Euro")

class TimeStampedModel(models.Model):
    """
    Small reusable base model for created/updated timestamps.
    """

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("When this record was created."),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("When this record was last updated."),
    )

    class Meta:
        abstract = True


class Wallet(TimeStampedModel):
    """
    One wallet per user for the current MVP.

    Freewise starts with DZD, so this model is intentionally simple.
    If you later expand to multi-currency wallets, this can be moved to
    a user-currency pair without changing the ledger logic.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        FROZEN = "frozen", _("Frozen")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
        verbose_name=_("user"),
        help_text=_("The account owner of this wallet."),
    )

    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.DZD,
        verbose_name=_("currency"),
        help_text=_("ISO 4217 currency code used by this wallet. Freewise starts with DZD."),
    )

    # Read-optimized balances.
    available_balance = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal("0.00"),
        verbose_name=_("available balance"),
        help_text=_("Money the user can spend or withdraw right now."),
    )
    escrow_balance = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal("0.00"),
        verbose_name=_("escrow balance"),
        help_text=_("Money currently locked in contract escrow."),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_("status"),
        help_text=_("Wallet state. Frozen wallets should not be allowed to move funds."),
    )

    class Meta:
        verbose_name = _("wallet")
        verbose_name_plural = _("wallets")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["currency"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} - {self.currency}"

    def clean(self):
        super().clean()
        if self.available_balance < 0:
            raise ValidationError(
                {"available_balance": _("Available balance cannot be negative.")}
            )
        if self.escrow_balance < 0:
            raise ValidationError(
                {"escrow_balance": _("Escrow balance cannot be negative.")}
            )


class WalletTransaction(TimeStampedModel):
    """
    Immutable ledger entry.

    This is the single source of truth for auditing money movement.
    The wallet balances are only a fast summary of the ledger.
    """

    class Type(models.TextChoices):
        DEPOSIT = "deposit", _("Deposit")
        ESCROW_HOLD = "escrow_hold", _("Escrow Hold")
        ESCROW_RELEASE = "escrow_release", _("Escrow Release")
        PAYOUT = "payout", _("Payout")
        REFUND = "refund", _("Refund")
        PLATFORM_FEE = "platform_fee", _("Platform Fee")
        ADJUSTMENT = "adjustment", _("Adjustment")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")
        REVERSED = "reversed", _("Reversed")

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name=_("wallet"),
        help_text=_("Wallet impacted by this transaction."),
    )

    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_wallet_transactions",
        verbose_name=_("initiated by"),
        help_text=_("Who triggered the transaction. Leave empty for system actions."),
    )

    transaction_type = models.CharField(
        max_length=30,
        choices=Type.choices,
        verbose_name=_("transaction type"),
        help_text=_("Why this ledger entry exists."),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("status"),
        help_text=_("Current processing state of this transaction."),
    )

    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        verbose_name=_("amount"),
        help_text=_("Positive amount moved by this transaction."),
    )

    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.DZD,
        verbose_name=_("currency"),
        help_text=_("Currency used for this transaction."),
    )

    balance_before = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal("0.00"),
        verbose_name=_("balance before"),
        help_text=_("Wallet balance before this transaction was applied."),
    )

    balance_after = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal("0.00"),
        verbose_name=_("balance after"),
        help_text=_("Wallet balance after this transaction was applied."),
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("processed at"),
        help_text=_("When the provider webhook successfully processed this payment."),
    )

    # Generic references so we do not tightly couple payments to future app structure.
    reference_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("reference type"),
        help_text=_("Optional source object type such as contract, payout, or refund."),
    )
    reference_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("reference id"),
        help_text=_("Optional source object identifier."),
    )

    provider_name = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("provider name"),
        help_text=_("Payment provider name, if this transaction came from one."),
    )
    provider_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("provider reference"),
        help_text=_("External reference from the payment provider."),
    )

    idempotency_key = models.CharField(
        max_length=120,
        unique=True,
        verbose_name=_("idempotency key"),
        help_text=_("Unique key that prevents the same money action from being processed twice."),
    )

    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Human-readable explanation of this transaction."),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Extra provider or business data stored as JSON."),
    )

    class Meta:
        verbose_name = _("wallet transaction")
        verbose_name_plural = _("wallet transactions")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "transaction_type"]),
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["provider_name", "provider_reference"]),
            models.Index(fields=["idempotency_key"]),
        ]

    def __str__(self) -> str:
        return f"{self.wallet_id} - {self.transaction_type} - {self.amount}"

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": _("Amount must be greater than zero.")})
        if self.balance_before < 0:
            raise ValidationError(
                {"balance_before": _("Balance before cannot be negative.")}
            )
        if self.balance_after < 0:
            raise ValidationError({"balance_after": _("Balance after cannot be negative.")})


class EscrowHold(TimeStampedModel):
    """
    Tracks money locked for a contract.

    This record is intentionally separate from the wallet transaction so
    disputes, refunds, and releases remain easy to audit.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        RELEASED = "released", _("Released")
        REFUNDED = "refunded", _("Refunded")
        DISPUTED = "disputed", _("Disputed")
        CANCELLED = "cancelled", _("Cancelled")

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="escrow_holds",
        verbose_name=_("wallet"),
        help_text=_("Wallet from which the escrow funds were taken."),
    )

    funding_transaction = models.OneToOneField(
        WalletTransaction,
        on_delete=models.PROTECT,
        related_name="escrow_hold_funding",
        verbose_name=_("funding transaction"),
        help_text=_("The transaction that moved funds into escrow."),
    )

    resolution_transaction = models.OneToOneField(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escrow_hold_resolution",
        verbose_name=_("resolution transaction"),
        help_text=_("The transaction that released, refunded, or otherwise resolved this hold."),
    )

    idempotency_key = models.CharField(
        max_length=120,
        unique=True,
        verbose_name=_("idempotency key"),
        help_text=_("Unique key that prevents duplicate escrow holds."),
    )

    contract_reference = models.CharField(
        max_length=100,
        verbose_name=_("contract reference"),
        help_text=_("Reference to the contract or order tied to this escrow hold."),
    )

    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        verbose_name=_("amount"),
        help_text=_("Amount locked in escrow."),
    )

    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.DZD,
        verbose_name=_("currency"),
        help_text=_("Currency used for this escrow hold."),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_("status"),
        help_text=_("Current state of the escrow hold."),
    )

    resolution_note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("resolution note"),
        help_text=_("Internal note explaining why the escrow hold was resolved."),
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("resolved at"),
        help_text=_("When the hold was released, refunded, disputed, or cancelled."),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Extra contract or provider data stored as JSON."),
    )

    class Meta:
        verbose_name = _("escrow hold")
        verbose_name_plural = _("escrow holds")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["contract_reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.contract_reference} - {self.amount} {self.currency}"

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": _("Amount must be greater than zero.")})


class Payout(TimeStampedModel):
    """
    Tracks the release of freelancer earnings out of the platform.

    This is intentionally generic so Freewise can support new payout rails
    later without rewriting the data model.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        REVERSED = "reversed", _("Reversed")
        CANCELLED = "cancelled", _("Cancelled")

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="payouts",
        verbose_name=_("wallet"),
        help_text=_("Wallet this payout is paying out from."),
    )

    ledger_transaction = models.OneToOneField(
        WalletTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payout",
        verbose_name=_("ledger transaction"),
        help_text=_("The wallet transaction that records this payout."),
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_payouts",
        verbose_name=_("requested by"),
        help_text=_("Who requested the payout. Leave empty for system actions."),
    )

    idempotency_key = models.CharField(
        max_length=120,
        unique=True,
        verbose_name=_("idempotency key"),
        help_text=_("Unique key that prevents duplicate payout requests."),
    )

    amount = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        verbose_name=_("amount"),
        help_text=_("Amount requested for payout."),
    )

    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.DZD,
        verbose_name=_("currency"),
        help_text=_("Currency used for this payout."),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("status"),
        help_text=_("Current payout processing state."),
    )

    provider_name = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("provider name"),
        help_text=_("Provider handling this payout, if any."),
    )

    provider_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("provider reference"),
        help_text=_("External payout reference from the provider."),
    )

    destination_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("destination type"),
        help_text=_("Type of payout destination such as bank account, phone, or wallet."),
    )

    destination_label = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("destination label"),
        help_text=_("Human-readable payout destination label for admins and support."),
    )

    failure_reason = models.TextField(
        blank=True,
        default="",
        verbose_name=_("failure reason"),
        help_text=_("Why the payout failed, if it failed."),
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("processed at"),
        help_text=_("When the payout was processed by the platform or provider."),
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Extra payout data stored as JSON."),
    )

    class Meta:
        verbose_name = _("payout")
        verbose_name_plural = _("payouts")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["wallet", "status"]),
            models.Index(fields=["provider_name", "provider_reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.wallet_id} - {self.amount} {self.currency}"

    def clean(self):
        super().clean()
        if self.amount <= 0:
            raise ValidationError({"amount": _("Amount must be greater than zero.")})


class WebhookLog(TimeStampedModel):
    """
    Raw provider webhook audit log.

    Keep these records even when processing fails so support and devs can
    investigate payment issues later.
    """

    class Status(models.TextChoices):
        RECEIVED = "received", _("Received")
        PROCESSED = "processed", _("Processed")
        FAILED = "failed", _("Failed")
        IGNORED = "ignored", _("Ignored")

    provider_name = models.CharField(
        max_length=50,
        default="chargily",
        verbose_name=_("provider name"),
        help_text=_("Webhook provider name."),
    )

    event_name = models.CharField(
        max_length=100,
        verbose_name=_("event name"),
        help_text=_("Provider event name or type."),
    )

    provider_event_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("provider event id"),
        help_text=_("Unique event identifier from the provider, if available."),
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        verbose_name=_("status"),
        help_text=_("Processing state of the webhook log."),
    )

    signature_valid = models.BooleanField(
        default=False,
        verbose_name=_("signature valid"),
        help_text=_("Whether the provider signature passed verification."),
    )

    processed = models.BooleanField(
        default=False,
        verbose_name=_("processed"),
        help_text=_("Whether this webhook has already been handled successfully."),
    )

    raw_body = models.TextField(
        verbose_name=_("raw body"),
        help_text=_("Raw webhook payload exactly as received from the provider."),
    )

    payload = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("payload"),
        help_text=_("Parsed webhook payload stored as JSON."),
    )

    headers = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("headers"),
        help_text=_("Request headers captured at webhook time."),
    )

    related_reference_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("related reference type"),
        help_text=_("Optional model type related to this webhook, such as payment or payout."),
    )

    related_reference_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("related reference id"),
        help_text=_("Optional object identifier related to this webhook."),
    )

    processing_error = models.TextField(
        blank=True,
        default="",
        verbose_name=_("processing error"),
        help_text=_("Error message stored when webhook processing fails."),
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("processed at"),
        help_text=_("When this webhook was processed."),
    )

    class Meta:
        verbose_name = _("webhook log")
        verbose_name_plural = _("webhook logs")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider_name", "event_name"]),
            models.Index(fields=["provider_event_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["processed"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider_name} - {self.event_name} - {self.created_at:%Y-%m-%d %H:%M:%S}"