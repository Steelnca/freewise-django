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
from uuid import uuid4

from django.core.validators import MinValueValidator
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.mixins import PublicIDMixin

from .constants import MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES
from .managers import PaymentAttemptManager

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

    lifetime_earnings = models.DecimalField(
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        default=Decimal("0.00"),
        verbose_name=_("life time earnings"),
        help_text=_("Available money earned through lifetime."),
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

class WalletTransaction(PublicIDMixin, TimeStampedModel):
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

    PUBLIC_ID_PREFIX = "fwwt"
    PUBLIC_ID_LENGTH_PREFIX = 32

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

class EscrowHold(PublicIDMixin, TimeStampedModel):
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

    PUBLIC_ID_PREFIX = "fweh"
    PUBLIC_ID_LENGTH_PREFIX = 8

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
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
        verbose_name=_("amount"),
        help_text=_("Remaining escrow amount. Zero is valid when the hold is fully resolved."),
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

        if self.amount is not None and self.amount < Decimal("0.00"):
            raise ValidationError({"amount": _("Amount cannot be negative.")})

class Payout(PublicIDMixin, TimeStampedModel):
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

    PUBLIC_ID_PREFIX = "fwpo"
    PUBLIC_ID_LENGTH_PREFIX = 12

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

class PaymentAttempt(models.Model):
    """
    One external payment attempt for one milestone funding action.

    Freewise owns the truth. Chargily is only the provider snapshot.
    """

    class Provider(models.TextChoices):
        CHARGILY = "chargily", _("Chargily")

    class InternalStatus(models.TextChoices):
        CREATED = "CREATED", _("Created")
        REDIRECTED = "REDIRECTED", _("Redirected to provider")
        PENDING_PROVIDER = "PENDING_PROVIDER", _("Waiting for provider confirmation")
        PROCESSING = "PROCESSING", _("Processing")
        PAID_PROVIDER_NOT_SETTLED = "PAID_PROVIDER_NOT_SETTLED", _("Paid by provider, not settled yet")
        SETTLED = "SETTLED", _("Settled in Freewise")
        FAILED = "FAILED", _("Failed")
        CANCELED = "CANCELED", _("Canceled")
        EXPIRED = "EXPIRED", _("Expired")
        RECONCILED = "RECONCILED", _("Reconciled from provider status")
        WEBHOOK_RECEIVED = "WEBHOOK_RECEIVED", _("Webhook received")

    objects = PaymentAttemptManager()

    attempt_id = models.UUIDField(
        default=uuid4,
        editable=False,
        unique=True,
        verbose_name=_("attempt id"),
        help_text=_("Internal unique ID for this payment attempt."),
    )

    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="payment_attempts",
        verbose_name=_("contract"),
        help_text=_("Contract this payment attempt belongs to."),
    )

    milestone = models.ForeignKey(
        "milestones.Milestone",
        on_delete=models.CASCADE,
        related_name="payment_attempts",
        verbose_name=_("milestone"),
        help_text=_("Milestone being funded by this payment attempt."),
    )

    initiated_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="initiated_payment_attempts",
        verbose_name=_("initiated by"),
        help_text=_("User who started this payment attempt."),
    )

    provider = models.CharField(
        max_length=32,
        choices=Provider.choices,
        default=Provider.CHARGILY,
        db_index=True,
        verbose_name=_("provider"),
        help_text=_("Payment provider used for this attempt."),
    )

    attempt_number = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_("attempt number"),
        help_text=_("1 for the first try, 2 for the retry, and so on."),
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("amount"),
        help_text=_("Amount of money to be paid in this attempt."),
    )

    currency = models.CharField(
        max_length=3,
        verbose_name=_("currency"),
        help_text=_("Currency code used for this payment attempt."),
    )

    internal_status = models.CharField(
        max_length=40,
        choices=InternalStatus.choices,
        default=InternalStatus.CREATED,
        db_index=True,
        verbose_name=_("internal status"),
        help_text=_("Freewise payment state for this attempt."),
    )

    provider_status = models.CharField(
        max_length=40,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("provider status"),
        help_text=_("Latest status reported by Chargily."),
    )

    provider_checkout_id = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        default=None,
        unique=True,
        db_index=True,
        verbose_name=_("provider checkout id"),
        help_text=_("Chargily or gateway checkout ID returned after checkout creation."),
    )

    provider_checkout_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("provider checkout url"),
        help_text=_("Hosted payment link returned by Chargily."),
    )

    provider_payment_method = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("provider payment method"),
        help_text=_("Payment method selected at the provider checkout."),
    )

    provider_reference = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("provider reference"),
        help_text=_("Any extra reference returned or echoed by the provider."),
    )

    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        verbose_name=_("idempotency key"),
        help_text=_("Prevents duplicate attempts being created twice."),
    )

    success_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("success url"),
        help_text=_("Success redirect URL used for this attempt."),
    )

    failure_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("failure url"),
        help_text=_("Failure redirect URL used for this attempt."),
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("expires at"),
        help_text=_("When the checkout is expected to expire."),
    )

    provider_created_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("provider created at"),
        help_text=_("When the provider checkout was created."),
    )

    provider_paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("provider paid at"),
        help_text=_("When the provider marked the checkout as paid."),
    )

    webhook_received_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("webhook received at"),
        help_text=_("When Freewise received a webhook for this attempt."),
    )

    webhook_processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("webhook processed at"),
        help_text=_("When the webhook was fully processed by Freewise."),
    )

    reconciled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("reconciled at"),
        help_text=_("When Freewise reconciled this attempt from provider status."),
    )

    last_reconciled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("last reconciled at"),
        help_text=_("When Freewise last tried to reconcile this attempt from provider status."),
    )

    settled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("settled at"),
        help_text=_("When Freewise finalized the money flow from this attempt."),
    )

    failure_reason = models.TextField(
        blank=True,
        default="",
        verbose_name=_("failure reason"),
        help_text=_("Human-readable reason why the attempt failed."),
    )

    webhook_payload = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("webhook payload"),
        help_text=_("Raw webhook payload received from the provider."),
    )

    provider_snapshot = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("provider snapshot"),
        help_text=_("Latest provider checkout snapshot stored by Freewise."),
    )

    settlement_transaction = models.OneToOneField(
        "payments.WalletTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_attempt",
        verbose_name=_("settlement transaction"),
        help_text=_("Wallet transaction created when the attempt was settled."),
    )

    escrow_hold = models.OneToOneField(
        "payments.EscrowHold",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_attempt",
        verbose_name=_("escrow hold"),
        help_text=_("Escrow hold created after the payment was settled."),
    )

    retry_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="retries",
        verbose_name=_("retry of"),
        help_text=_("Previous payment attempt that this one retried."),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("When this payment attempt was created."),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("When this payment attempt was last updated."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("payment attempt")
        verbose_name_plural = _("payment attempts")
        indexes = [
            models.Index(fields=["contract", "milestone"]),
            models.Index(fields=["provider", "provider_status"]),
            models.Index(fields=["internal_status"]),
            models.Index(fields=["idempotency_key"]),
            models.Index(fields=["provider_checkout_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["milestone", "attempt_number"],
                name="unique_payment_attempt_number_per_milestone",
            ),
        ]

    def __str__(self) -> str:
        return f"PaymentAttempt #{self.pk} — {self.milestone.title}"

    @property
    def is_final(self) -> bool:
        return self.internal_status in {
            self.InternalStatus.SETTLED,
            self.InternalStatus.FAILED,
            self.InternalStatus.CANCELED,
            self.InternalStatus.EXPIRED,
        }

    @property
    def retryable(self) -> bool:
        return self.internal_status in {
            self.InternalStatus.FAILED,
            self.InternalStatus.CANCELED,
            self.InternalStatus.EXPIRED,
        }