
"""
Freewise payment services.

This module is the only place where wallet balances should be mutated.

Design rules:
- Treat Wallet as the current balance snapshot.
- Treat WalletTransaction as the immutable audit trail.
- Treat EscrowHold as the contract lock record.
- Treat Payout as the external withdrawal record.

Best practice:
- Always mutate money inside transaction.atomic().
- Always lock the relevant wallet rows with select_for_update().
- Never trust the frontend for balances, statuses, or totals.
- Use idempotency keys for every money-moving action.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from .models import (
    EscrowHold,
    Payout,
    Wallet,
    WalletTransaction,
)
from .constants import DEFAULT_CURRENCY

User = get_user_model()

MONEY_QUANTIZER = Decimal("0.01")


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------
class PaymentServiceError(Exception):
    """
    Base exception for wallet/payment service failures.
    """


class WalletNotFoundError(PaymentServiceError):
    """
    Raised when a wallet is missing.
    """


class WalletFrozenError(PaymentServiceError):
    """
    Raised when a wallet is frozen and should not move funds.
    """


class InsufficientFundsError(PaymentServiceError):
    """
    Raised when a wallet does not have enough available balance.
    """


class EscrowHoldError(PaymentServiceError):
    """
    Raised when escrow operations are invalid.
    """


class PayoutError(PaymentServiceError):
    """
    Raised when payout operations are invalid.
    """


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def normalize_money(value: Decimal | str | int | float) -> Decimal:
    """
    Convert a value to a properly rounded Decimal with 2 decimal places.
    """
    amount = Decimal(str(value)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    return amount

def validate_positive_money(amount: Decimal) -> None:
    """
    Money amounts used by the ledger must always be positive.
    """
    if amount <= 0:
        raise ValidationError({"amount": _("Amount must be greater than zero.")})

def validate_currency(currency: str) -> str:
    """
    Normalize and validate currency codes early.
    """
    code = (currency or DEFAULT_CURRENCY).strip().upper()
    if len(code) != 3:
        raise ValidationError({"currency": _("Currency must be a 3-letter ISO code.")})
    return code

def build_metadata(base: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    """
    Merge metadata dictionaries without mutating input values.
    """
    data: Dict[str, Any] = dict(base or {})
    for key, value in extra.items():
        if value is not None:
            data[key] = value
    return data

def lock_wallets(wallets: Iterable[Wallet]) -> Dict[int, Wallet]:
    """
    Lock multiple wallet rows in a deterministic order.

    This helps avoid deadlocks when a transaction touches more than one wallet.
    """
    wallet_ids = sorted({wallet.pk for wallet in wallets if wallet is not None})
    if not wallet_ids:
        raise WalletNotFoundError(_("No wallets were provided."))

    locked_wallets = (
        Wallet.objects.select_for_update()
        .filter(pk__in=wallet_ids)
        .order_by("pk")
    )
    locked_map = {wallet.pk: wallet for wallet in locked_wallets}

    if len(locked_map) != len(wallet_ids):
        raise WalletNotFoundError(_("One or more wallets could not be found."))

    return locked_map

def ensure_wallet_active(wallet: Wallet) -> None:
    """
    Block money movement when a wallet is frozen.
    """
    if wallet.status != Wallet.Status.ACTIVE:
        raise WalletFrozenError(_("This wallet is frozen."))

def get_or_create_wallet_for_user(
    user: User,
    currency: str = DEFAULT_CURRENCY,
) -> Wallet:
    """
    Create a wallet for a user if it does not already exist.

    For the MVP, Freewise uses one wallet per user.
    """
    currency = validate_currency(currency)

    wallet, created = Wallet.objects.get_or_create(
        user=user,
        defaults={"currency": currency},
    )

    if not created and wallet.currency != currency:
        # Early-stage safety: do not silently switch wallet currency.
        raise ValidationError(
            {
                "currency": _(
                    "Existing wallet currency does not match the requested currency."
                )
            }
        )

    return wallet

def _get_locked_wallet(wallet: Wallet) -> Wallet:
    """
    Reload a single wallet row using SELECT ... FOR UPDATE.
    """
    return Wallet.objects.select_for_update().get(pk=wallet.pk)

def _wallet_snapshot(wallet: Wallet) -> Dict[str, Any]:
    """
    Store a compact wallet snapshot in transaction metadata.

    Note:
    WalletTransaction.balance_before / balance_after are used as the
    available-balance snapshot. Escrow changes are captured in metadata.
    """
    return {
        "wallet_id": wallet.pk,
        "currency": wallet.currency,
        "available_balance": str(wallet.available_balance),
        "escrow_balance": str(wallet.escrow_balance),
        "status": wallet.status,
    }

def _get_or_return_existing_transaction(
    *,
    idempotency_key: str,
) -> Optional[WalletTransaction]:
    """
    Idempotency helper.

    If a transaction with the same idempotency key already exists,
    return it instead of creating a duplicate.
    """
    if not idempotency_key:
        raise ValidationError(
            {"idempotency_key": _("Idempotency key is required.")},
        )

    return WalletTransaction.objects.filter(
        idempotency_key=idempotency_key
    ).first()

def create_wallet_transaction(
    *,
    wallet: Wallet,
    initiated_by: Optional[User],
    transaction_type: str,
    amount: Decimal,
    currency: str,
    balance_before: Decimal,
    balance_after: Decimal,
    idempotency_key: str,
    status: str = WalletTransaction.Status.COMPLETED,
    reference_type: str = "",
    reference_id: str = "",
    provider_name: str = "",
    provider_reference: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> WalletTransaction:
    """
    Create an immutable ledger entry.

    This function is intentionally strict:
    - positive amounts only
    - unique idempotency key
    - full validation before save
    """
    amount = normalize_money(amount)
    currency = validate_currency(currency)

    validate_positive_money(amount)

    existing = _get_or_return_existing_transaction(
        idempotency_key=idempotency_key,
    )
    if existing:
        return existing

    tx = WalletTransaction(
        wallet=wallet,
        initiated_by=initiated_by,
        transaction_type=transaction_type,
        status=status,
        amount=amount,
        currency=currency,
        balance_before=normalize_money(balance_before),
        balance_after=normalize_money(balance_after),
        reference_type=reference_type,
        reference_id=reference_id,
        provider_name=provider_name,
        provider_reference=provider_reference,
        idempotency_key=idempotency_key,
        description=description,
        metadata=metadata or {},
    )

    tx.full_clean()

    try:
        tx.save()
    except IntegrityError:
        # Another worker may have written the same idempotency key first.
        return WalletTransaction.objects.get(idempotency_key=idempotency_key)

    return tx

def _get_existing_escrow_hold(*, idempotency_key: str):
    return EscrowHold.objects.filter(idempotency_key=idempotency_key).first()

def _get_existing_payout(*, idempotency_key: str):
    return Payout.objects.filter(idempotency_key=idempotency_key).first()

def calculate_platform_fee(amount: Decimal | str | int | float) -> Decimal:
    amount = normalize_money(amount)
    percent = Decimal(str(getattr(settings, "FREEWISE_PLATFORM_FEE_PERCENT", 10)))
    return normalize_money(amount * percent / Decimal("100"))

# -----------------------------------------------------------------------------
# Public services
# -----------------------------------------------------------------------------
@transaction.atomic
def record_deposit(
    *,
    wallet: Wallet,
    amount: Decimal | str | int | float,
    idempotency_key: str,
    initiated_by: Optional[User] = None,
    provider_name: str = "",
    provider_reference: str = "",
    reference_type: str = "",
    reference_id: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> WalletTransaction:
    """
    Credit a wallet after a successful payment provider confirmation.

    This is the typical entry point for Chargily webhook success handling.
    """
    amount = normalize_money(amount)
    validate_positive_money(amount)

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    wallet_currency = validate_currency(wallet.currency)
    before_available = wallet.available_balance
    before_escrow = wallet.escrow_balance

    wallet.available_balance = before_available + amount
    wallet.full_clean()
    wallet.save(update_fields=["available_balance", "updated_at"])

    tx = create_wallet_transaction(
        wallet=wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.DEPOSIT,
        amount=amount,
        currency=wallet_currency,
        balance_before=before_available,
        balance_after=wallet.available_balance,
        idempotency_key=idempotency_key,
        status=WalletTransaction.Status.COMPLETED,
        reference_type=reference_type,
        reference_id=reference_id,
        provider_name=provider_name,
        provider_reference=provider_reference,
        description=description or _("Deposit recorded."),
        metadata=build_metadata(
            metadata,
            action="deposit",
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=wallet.pk,
                    currency=wallet.currency,
                    available_balance=before_available,
                    escrow_balance=before_escrow,
                    status=wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(wallet),
        ),
    )

    return tx


@transaction.atomic
def hold_funds_for_escrow(
    *,
    wallet: Wallet,
    amount: Decimal | str | int | float,
    contract_reference: str,
    idempotency_key: str,
    initiated_by: Optional[User] = None,
    reference_type: str = "contract",
    reference_id: str = "",
    provider_name: str = "",
    provider_reference: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> EscrowHold:
    """
    Move money from available balance into escrow.

    This should be used when a client funds a contract or job.
    """

    existing = _get_existing_escrow_hold(idempotency_key=idempotency_key)
    if existing:
        return existing

    amount = normalize_money(amount)
    validate_positive_money(amount)

    if not contract_reference:
        raise ValidationError(
            {"contract_reference": _("Contract reference is required.")}
        )

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    wallet_currency = validate_currency(wallet.currency)
    before_available = wallet.available_balance
    before_escrow = wallet.escrow_balance

    if before_available < amount:
        raise InsufficientFundsError(_("Insufficient available balance."))


    wallet.available_balance = before_available - amount
    wallet.escrow_balance = before_escrow + amount
    wallet.full_clean()
    wallet.save(update_fields=["available_balance", "escrow_balance", "updated_at"])

    tx = create_wallet_transaction(
        wallet=wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.ESCROW_HOLD,
        amount=amount,
        currency=wallet_currency,
        balance_before=before_available,
        balance_after=wallet.available_balance,
        provider_name=provider_name,
        provider_reference=provider_reference,
        idempotency_key=idempotency_key,
        status=WalletTransaction.Status.COMPLETED,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description or _("Funds moved into escrow."),
        metadata=build_metadata(
            metadata,
            action="escrow_hold",
            contract_reference=contract_reference,
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=wallet.pk,
                    currency=wallet.currency,
                    available_balance=before_available,
                    escrow_balance=before_escrow,
                    status=wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(wallet),
        ),
    )

    hold = EscrowHold(
        wallet=wallet,
        funding_transaction=tx,
        contract_reference=contract_reference,
        idempotency_key=idempotency_key,
        amount=amount,
        currency=wallet_currency,
        status=EscrowHold.Status.ACTIVE,
        metadata=build_metadata(
            metadata,
            provider_name=provider_name,
            provider_reference=provider_reference,
        ),
    )

    hold.full_clean()
    hold.save()

    return hold


@transaction.atomic
def refund_escrow_hold(
    *,
    hold: EscrowHold,
    idempotency_key: str,
    initiated_by: Optional[User] = None,
    amount: Optional[Decimal | str | int | float] = None,
    reference_type: str = "contract",
    reference_id: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> WalletTransaction:
    """
    Return escrow funds back to the original wallet.

    Useful for contract cancellation or dispute refunds.
    """
    hold = EscrowHold.objects.select_for_update().select_related("wallet").get(
        pk=hold.pk
    )
    wallet = _get_locked_wallet(hold.wallet)

    ensure_wallet_active(wallet)

    if hold.status not in {
        EscrowHold.Status.ACTIVE,
        EscrowHold.Status.DISPUTED,
    }:
        raise EscrowHoldError(_("This escrow hold cannot be refunded."))

    refund_amount = normalize_money(amount or hold.amount)
    validate_positive_money(refund_amount)

    if refund_amount > hold.amount:
        raise EscrowHoldError(_("Refund amount cannot exceed the escrow hold amount."))

    wallet_currency = validate_currency(wallet.currency)
    before_available = wallet.available_balance
    before_escrow = wallet.escrow_balance

    if before_escrow < refund_amount:
        raise InsufficientFundsError(_("Insufficient escrow balance."))

    wallet.available_balance = before_available + refund_amount
    wallet.escrow_balance = before_escrow - refund_amount
    wallet.full_clean()
    wallet.save(update_fields=["available_balance", "escrow_balance", "updated_at"])

    tx = create_wallet_transaction(
        wallet=wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.REFUND,
        amount=refund_amount,
        currency=wallet_currency,
        balance_before=before_available,
        balance_after=wallet.available_balance,
        idempotency_key=idempotency_key,
        status=WalletTransaction.Status.COMPLETED,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description or _("Escrow refunded back to the wallet."),
        metadata=build_metadata(
            metadata,
            action="escrow_refund",
            contract_reference=hold.contract_reference,
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=wallet.pk,
                    currency=wallet.currency,
                    available_balance=before_available,
                    escrow_balance=before_escrow,
                    status=wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(wallet),
        ),
    )

    hold.amount = hold.amount - refund_amount

    if hold.amount < Decimal("0.00"):
        raise EscrowHoldError(_("Escrow hold amount cannot go below zero."))

    if hold.amount == Decimal("0.00"):
        hold.status = EscrowHold.Status.REFUNDED
        hold.resolved_at = timezone.now()
        hold.resolution_transaction = tx
        hold.resolution_note = description or _("Escrow refunded in full.")
    else:
        hold.status = EscrowHold.Status.ACTIVE
        hold.resolution_transaction = tx
        hold.resolution_note = description or _("Escrow refunded partially.")

    # Zero is a valid terminal amount.
    hold.save(
        update_fields=[
            "amount",
            "status",
            "resolved_at",
            "resolution_transaction",
            "resolution_note",
            "updated_at",
        ]
    )

    return tx


@transaction.atomic
def release_escrow_hold_to_wallet(
    *,
    hold: EscrowHold,
    recipient_wallet: Wallet,
    idempotency_key: str,
    initiated_by: Optional[User] = None,
    amount: Optional[Decimal | str | int | float] = None,
    fee_wallet: Optional[Wallet] = None,
    fee_amount: Decimal | str | int | float = Decimal("0.00"),
    reference_type: str = "contract",
    reference_id: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, WalletTransaction]:
    """
    Release escrow funds from the client wallet to a recipient wallet.

    This is the key settlement path for completed contracts.
    Optional platform fees can be routed to a separate fee wallet.
    """
    hold = EscrowHold.objects.select_for_update().select_related("wallet").get(
        pk=hold.pk
    )

    source_wallet = _get_locked_wallet(hold.wallet)
    recipient_wallet = _get_locked_wallet(recipient_wallet)

    wallets = [source_wallet, recipient_wallet]
    if fee_wallet is not None:
        wallets.append(fee_wallet)

    locked_map = lock_wallets(wallets)
    source_wallet = locked_map[source_wallet.pk]
    recipient_wallet = locked_map[recipient_wallet.pk]
    locked_fee_wallet = locked_map.get(fee_wallet.pk) if fee_wallet else None

    ensure_wallet_active(source_wallet)
    ensure_wallet_active(recipient_wallet)
    if locked_fee_wallet:
        ensure_wallet_active(locked_fee_wallet)

    if hold.status not in {EscrowHold.Status.ACTIVE, EscrowHold.Status.DISPUTED}:
        raise EscrowHoldError(_("This escrow hold cannot be released."))

    release_amount = normalize_money(amount or hold.amount)
    fee_amount = normalize_money(fee_amount)
    validate_positive_money(release_amount)

    if fee_amount < 0:
        raise ValidationError({"fee_amount": _("Fee amount cannot be negative.")})

    total_outgoing = release_amount + fee_amount
    if total_outgoing > hold.amount:
        raise EscrowHoldError(
            _("Release amount plus fee cannot exceed the escrow hold amount.")
        )

    if source_wallet.currency != recipient_wallet.currency:
        raise ValidationError(_("Source and recipient wallets must use the same currency."))

    if locked_fee_wallet and locked_fee_wallet.currency != source_wallet.currency:
        raise ValidationError(_("Fee wallet must use the same currency."))

    if source_wallet.escrow_balance < total_outgoing:
        raise InsufficientFundsError(_("Insufficient escrow balance."))

    currency = validate_currency(source_wallet.currency)

    source_before_available = source_wallet.available_balance
    source_before_escrow = source_wallet.escrow_balance
    recipient_before_available = recipient_wallet.available_balance
    recipient_before_escrow = recipient_wallet.escrow_balance
    fee_before_available = locked_fee_wallet.available_balance if locked_fee_wallet else None
    fee_before_escrow = locked_fee_wallet.escrow_balance if locked_fee_wallet else None

    # Source wallet: move money out of escrow.
    source_wallet.escrow_balance = source_before_escrow - total_outgoing

    # Recipient wallet: receive the released amount.
    recipient_wallet.available_balance = recipient_before_available + release_amount

    # Optional fee wallet: receive the platform fee.
    if locked_fee_wallet and fee_amount > 0:
        locked_fee_wallet.available_balance = locked_fee_wallet.available_balance + fee_amount

    source_wallet.full_clean()
    recipient_wallet.full_clean()
    if locked_fee_wallet:
        locked_fee_wallet.full_clean()

    source_wallet.save(update_fields=["escrow_balance", "updated_at"])
    recipient_wallet.save(update_fields=["available_balance", "updated_at"])
    if locked_fee_wallet and fee_amount > 0:
        locked_fee_wallet.save(update_fields=["available_balance", "updated_at"])

    source_tx = create_wallet_transaction(
        wallet=source_wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.ESCROW_RELEASE,
        amount=total_outgoing,
        currency=currency,
        balance_before=source_before_available,
        balance_after=source_wallet.available_balance,
        idempotency_key=f"{idempotency_key}:source",
        status=WalletTransaction.Status.COMPLETED,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description or _("Escrow released from source wallet."),
        metadata=build_metadata(
            metadata,
            action="escrow_release_source",
            contract_reference=hold.contract_reference,
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=source_wallet.pk,
                    currency=source_wallet.currency,
                    available_balance=source_before_available,
                    escrow_balance=source_before_escrow,
                    status=source_wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(source_wallet),
            release_amount=str(release_amount),
            fee_amount=str(fee_amount),
        ),
    )

    recipient_tx = create_wallet_transaction(
        wallet=recipient_wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.DEPOSIT,
        amount=release_amount,
        currency=currency,
        balance_before=recipient_before_available,
        balance_after=recipient_wallet.available_balance,
        idempotency_key=f"{idempotency_key}:recipient",
        status=WalletTransaction.Status.COMPLETED,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description or _("Released escrow credited to recipient wallet."),
        metadata=build_metadata(
            metadata,
            action="escrow_release_recipient",
            contract_reference=hold.contract_reference,
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=recipient_wallet.pk,
                    currency=recipient_wallet.currency,
                    available_balance=recipient_before_available,
                    escrow_balance=recipient_before_escrow,
                    status=recipient_wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(recipient_wallet),
        ),
    )

    fee_tx = None
    if locked_fee_wallet and fee_amount > 0:
        fee_tx = create_wallet_transaction(
            wallet=locked_fee_wallet,
            initiated_by=initiated_by,
            transaction_type=WalletTransaction.Type.PLATFORM_FEE,
            amount=fee_amount,
            currency=currency,
            balance_before=fee_before_available,
            balance_after=locked_fee_wallet.available_balance,
            idempotency_key=f"{idempotency_key}:fee",
            status=WalletTransaction.Status.COMPLETED,
            reference_type=reference_type,
            reference_id=reference_id,
            description=description or _("Platform fee recorded."),
            metadata=build_metadata(
                metadata,
                action="platform_fee",
                contract_reference=hold.contract_reference,
                wallet_before=_wallet_snapshot(
                    Wallet(
                        pk=locked_fee_wallet.pk,
                        currency=locked_fee_wallet.currency,
                        available_balance=fee_before_available,
                        escrow_balance=fee_before_escrow,
                        status=locked_fee_wallet.status,
                    )
                ),
                wallet_after=_wallet_snapshot(locked_fee_wallet),
            ),
        )

    hold.amount = hold.amount - total_outgoing

    if hold.amount < Decimal("0.00"):
        raise EscrowHoldError(_("Escrow hold amount cannot go below zero."))

    if hold.amount == Decimal("0.00"):
        hold.status = EscrowHold.Status.RELEASED
        hold.resolved_at = timezone.now()
        hold.resolution_transaction = source_tx
        hold.resolution_note = description or _("Escrow released in full.")
    else:
        hold.status = EscrowHold.Status.ACTIVE
        hold.resolution_transaction = source_tx
        hold.resolution_note = description or _("Escrow released partially.")

    # Do not full_clean() here because zero is a valid terminal escrow amount.
    hold.save(
        update_fields=[
            "amount",
            "status",
            "resolved_at",
            "resolution_transaction",
            "resolution_note",
            "updated_at",
        ]
    )

    result: Dict[str, WalletTransaction] = {
        "source": source_tx,
        "recipient": recipient_tx,
    }
    if fee_tx:
        result["fee"] = fee_tx

    return result


@transaction.atomic
def request_payout(
    *,
    wallet: Wallet,
    amount: Decimal | str | int | float,
    idempotency_key: str,
    initiated_by: Optional[User] = None,
    provider_name: str = "",
    provider_reference: str = "",
    destination_type: str = "",
    destination_label: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Payout:
    """
    Create a payout request and immediately reserve funds from the wallet.

    For MVP simplicity, funds are removed from available balance here.
    If the provider later fails, create a compensating adjustment transaction.
    """

    existing = _get_existing_payout(idempotency_key=idempotency_key)
    if existing:
        return existing

    amount = normalize_money(amount)
    validate_positive_money(amount)

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    currency = validate_currency(wallet.currency)
    before_available = wallet.available_balance
    before_escrow = wallet.escrow_balance

    if before_available < amount:
        raise InsufficientFundsError(_("Insufficient available balance."))

    wallet.available_balance = before_available - amount
    wallet.full_clean()
    wallet.save(update_fields=["available_balance", "updated_at"])

    tx = create_wallet_transaction(
        wallet=wallet,
        initiated_by=initiated_by,
        transaction_type=WalletTransaction.Type.WITHDRAWAL,
        amount=amount,
        currency=currency,
        balance_before=before_available,
        balance_after=wallet.available_balance,
        idempotency_key=idempotency_key,
        status=WalletTransaction.Status.COMPLETED,
        description=description or _("Payout requested."),
        metadata=build_metadata(
            metadata,
            action="payout_request",
            wallet_before=_wallet_snapshot(
                Wallet(
                    pk=wallet.pk,
                    currency=wallet.currency,
                    available_balance=before_available,
                    escrow_balance=before_escrow,
                    status=wallet.status,
                )
            ),
            wallet_after=_wallet_snapshot(wallet),
            destination_type=destination_type,
            destination_label=destination_label,
        ),
    )

    payout = Payout(
        wallet=wallet,
        ledger_transaction=tx,
        requested_by=initiated_by,
        idempotency_key=idempotency_key,
        amount=amount,
        currency=currency,
        status=Payout.Status.PENDING,
        provider_name=provider_name,
        provider_reference=provider_reference,
        destination_type=destination_type,
        destination_label=destination_label,
        metadata=metadata or {},
    )
    payout.full_clean()
    payout.save()

    return payout

@transaction.atomic
def get_or_create_platform_wallet(currency: str = DEFAULT_CURRENCY) -> Wallet:
    """
    Return Freewise's platform wallet.

    Uses the User.get_platform_user() classmethod and keeps the wallet
    as a normal ledger wallet owned by the platform system account.
    """
    currency = validate_currency(currency)

    platform_user = User.get_platform_user()

    wallet, created = Wallet.objects.get_or_create(
        user=platform_user,
        defaults={"currency": currency},
    )

    if not created and wallet.currency != currency:
        raise ValidationError(
            {
                "currency": _(
                    "Existing platform wallet currency does not match the requested currency."
                )
            }
        )

    return wallet