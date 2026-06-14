"""
Freewise payment services.

This module is the only place where wallet balances should be mutated.

Design rules:
- Treat Wallet as the current balance snapshot.
- Treat WalletTransaction as the immutable audit trail.
- Treat EscrowHold as the contract lock record.
- Treat Payout as the external withdrawal record.
- Treat PaymentAttempt as the provider/payment lifecycle record.

Best practice:
- Always mutate money inside transaction.atomic().
- Always lock the relevant wallet rows with select_for_update().
- Never trust the frontend for balances, statuses, or totals.
- Use idempotency keys for every money-moving action.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, Mapping, Optional
from dataclasses import asdict

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import PermissionDenied

from contracts.models import Contract, Milestone
from payments.gateways import BasePaymentGateway, GatewayWebhookEvent
from payments.gateways import get_payment_gateway
from core.utils import json_safe_dict


from .constants import DEFAULT_CURRENCY
from .models import EscrowHold, PaymentAttempt, Payout, Wallet, WalletTransaction

logger = logging.getLogger(__name__)

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
    return Decimal(str(value)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


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


def _get_locked_wallet(wallet: Wallet) -> Wallet:
    """
    Reload a single wallet row using SELECT ... FOR UPDATE.
    """
    return Wallet.objects.select_for_update().get(pk=wallet.pk)


def _wallet_snapshot(wallet: Wallet) -> Dict[str, Any]:
    """
    Store a compact wallet snapshot in transaction metadata.
    """
    return {
        "wallet_id": wallet.pk,
        "currency": wallet.currency,
        "available_balance": str(wallet.available_balance),
        "escrow_balance": str(wallet.escrow_balance),
        "status": wallet.status,
    }


def _get_existing_transaction_by_idempotency_key(
    *,
    idempotency_key: str,
) -> Optional[WalletTransaction]:
    if not idempotency_key:
        raise ValidationError({"idempotency_key": _("Idempotency key is required.")})

    return WalletTransaction.objects.filter(idempotency_key=idempotency_key).first()


def _get_existing_escrow_hold(*, idempotency_key: str) -> Optional[EscrowHold]:
    return EscrowHold.objects.filter(idempotency_key=idempotency_key).first()


def _get_existing_payout(*, idempotency_key: str) -> Optional[Payout]:
    return Payout.objects.filter(idempotency_key=idempotency_key).first()


def _resolve_platform_user() -> User:
    """
    Resolve Freewise's internal platform user.

    Supports a custom User.get_platform_user() classmethod, and falls back to
    common field names if that method is not present yet.
    """
    helper = getattr(User, "get_platform_user", None)
    if callable(helper):
        return helper()

    for field_name in ("user_type", "type", "status"):
        try:
            User._meta.get_field(field_name)
        except Exception:
            continue

        try:
            return User.objects.get(**{field_name: "platform"})
        except User.DoesNotExist:
            continue
        except User.MultipleObjectsReturned:
            raise ValidationError(
                {"platform_user": _("Multiple platform users were found.")}
            )

    raise ValidationError(
        {"platform_user": _("Platform user is not configured.")}
    )


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

    Idempotency is enforced by the idempotency key.
    """
    amount = normalize_money(amount)
    currency = validate_currency(currency)
    validate_positive_money(amount)

    existing = _get_existing_transaction_by_idempotency_key(
        idempotency_key=idempotency_key
    )
    if existing:
        if (
            existing.wallet_id != wallet.pk
            or existing.transaction_type != transaction_type
            or existing.amount != amount
            or existing.currency != currency
        ):
            raise ValidationError(
                {
                    "idempotency_key": _(
                        "This idempotency key is already used for a different transaction."
                    )
                }
            )
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


# -----------------------------------------------------------------------------
# Public services
# -----------------------------------------------------------------------------
@transaction.atomic
def get_or_create_wallet_for_user(
    user: User,
    currency: str = DEFAULT_CURRENCY,
) -> Wallet:
    """
    Create a wallet for a user if it does not already exist.
    """
    currency = validate_currency(currency)

    wallet, created = Wallet.objects.get_or_create(
        user=user,
        defaults={"currency": currency},
    )

    if not created and wallet.currency != currency:
        raise ValidationError(
            {
                "currency": _(
                    "Existing wallet currency does not match the requested currency."
                )
            }
        )

    return wallet


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
    """
    amount = normalize_money(amount)
    validate_positive_money(amount)

    existing = _get_existing_transaction_by_idempotency_key(
        idempotency_key=idempotency_key
    )
    if existing:
        return existing

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    # Re-check after the row lock in case another worker settled first.
    existing = _get_existing_transaction_by_idempotency_key(
        idempotency_key=idempotency_key
    )
    if existing:
        return existing

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
    """
    amount = normalize_money(amount)
    validate_positive_money(amount)

    if not contract_reference:
        raise ValidationError(
            {"contract_reference": _("Contract reference is required.")}
        )

    existing = _get_existing_escrow_hold(idempotency_key=idempotency_key)
    if existing:
        return existing

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    existing = _get_existing_escrow_hold(idempotency_key=idempotency_key)
    if existing:
        return existing

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
    """
    existing_tx = _get_existing_transaction_by_idempotency_key(
        idempotency_key=idempotency_key
    )
    if existing_tx:
        return existing_tx

    hold = EscrowHold.objects.select_for_update().select_related("wallet").get(
        pk=hold.pk
    )
    wallet = _get_locked_wallet(hold.wallet)
    ensure_wallet_active(wallet)

    existing_tx = _get_existing_transaction_by_idempotency_key(
        idempotency_key=idempotency_key
    )
    if existing_tx:
        return existing_tx

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
    release_amount: Optional[Decimal | str | int | float] = None,
    fee_wallet: Optional[Wallet] = None,
    fee_amount: Decimal | str | int | float = Decimal("0.00"),
    reference_type: str = "contract",
    reference_id: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, WalletTransaction]:
    """
    Release escrow funds from the source wallet to a recipient wallet.
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

    locked = lock_wallets(wallets)
    source_wallet = locked[source_wallet.pk]
    recipient_wallet = locked[recipient_wallet.pk]
    locked_fee_wallet = locked.get(fee_wallet.pk) if fee_wallet else None

    ensure_wallet_active(source_wallet)
    ensure_wallet_active(recipient_wallet)
    if locked_fee_wallet:
        ensure_wallet_active(locked_fee_wallet)

    source_anchor_key = f"{idempotency_key}:source"
    existing_source_tx = _get_existing_transaction_by_idempotency_key(
        idempotency_key=source_anchor_key
    )
    if existing_source_tx:
        result: Dict[str, WalletTransaction] = {
            "source": existing_source_tx,
            "recipient": WalletTransaction.objects.get(
                idempotency_key=f"{idempotency_key}:recipient"
            ),
        }
        if fee_wallet and fee_amount and normalize_money(fee_amount) > 0:
            fee_tx = WalletTransaction.objects.filter(
                idempotency_key=f"{idempotency_key}:fee"
            ).first()
            if fee_tx:
                result["fee"] = fee_tx
        return result

    if hold.status not in {EscrowHold.Status.ACTIVE, EscrowHold.Status.DISPUTED}:
        raise EscrowHoldError(_("This escrow hold cannot be released."))

    release_amount = normalize_money(release_amount)
    fee_amount = normalize_money(fee_amount)
    validate_positive_money(release_amount)

    if fee_amount < 0:
        raise ValidationError({"fee_amount": _("Fee amount cannot be negative.")})

    if fee_amount > 0 and fee_wallet is None:
        raise ValidationError(
            {"fee_wallet": _("A fee wallet is required when fee_amount is greater than zero.")}
        )

    total_outgoing = release_amount + fee_amount
    if total_outgoing > hold.amount:
        raise EscrowHoldError(
            _("Release amount plus fee cannot exceed the escrow hold amount.")
        )

    if total_outgoing <= Decimal("0.00"):
        raise ValidationError(
            {"release_amount": _("Total outgoing amount must be greater than zero.")}
        )

    if fee_amount > 0 and not locked_fee_wallet:
        raise ValidationError(_("A fee wallet is required when fee_amount is greater than zero."))

    if source_wallet.pk == recipient_wallet.pk:
        raise ValidationError(_("Source and recipient wallets cannot be the same."))

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
    recipient_before_earnings = recipient_wallet.lifetime_earnings
    fee_before_available = locked_fee_wallet.available_balance if locked_fee_wallet else None
    fee_before_escrow = locked_fee_wallet.escrow_balance if locked_fee_wallet else None

    source_wallet.escrow_balance = source_before_escrow - total_outgoing
    recipient_wallet.available_balance = recipient_before_available + release_amount

    # Count life time money earnings
    recipient_wallet.lifetime_earnings = recipient_before_earnings + release_amount

    if locked_fee_wallet and fee_amount > 0:
        locked_fee_wallet.available_balance = locked_fee_wallet.available_balance + fee_amount

    source_wallet.full_clean()
    recipient_wallet.full_clean()
    if locked_fee_wallet:
        locked_fee_wallet.full_clean()

    source_wallet.save(update_fields=["escrow_balance", "updated_at"])
    recipient_wallet.save(update_fields=["available_balance", "lifetime_earnings", "updated_at"])
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
        idempotency_key=source_anchor_key,
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
    """
    existing = _get_existing_payout(idempotency_key=idempotency_key)
    if existing:
        return existing

    amount = normalize_money(amount)
    validate_positive_money(amount)

    wallet = _get_locked_wallet(wallet)
    ensure_wallet_active(wallet)

    existing = _get_existing_payout(idempotency_key=idempotency_key)
    if existing:
        return existing

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
def calculate_platform_fee(amount: Decimal | str | int | float) -> Decimal:
    amount = normalize_money(amount)
    percent = Decimal(str(getattr(settings, "FREEWISE_PLATFORM_FEE_PERCENT", 10)))
    return normalize_money(amount * percent / Decimal("100"))


@transaction.atomic
def get_or_create_platform_wallet(currency: str = DEFAULT_CURRENCY) -> Wallet:
    """
    Return Freewise's platform wallet.
    """
    currency = validate_currency(currency)
    platform_user = _resolve_platform_user()

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


# -----------------------------------------------------------------------------
# PaymentAttempt helpers
# -----------------------------------------------------------------------------
def payment_contract_reference_for_milestone(milestone: Milestone) -> str:
    return f"contract:{milestone.contract.public_id}:milestone:{milestone.public_id}"


@transaction.atomic
def create_payment_attempt_for_milestone(
    *,
    milestone: Milestone,
    idempotency_key: str,
    initiated_by=None,
    provider_name: str = "",
    success_url: str,
    failure_url: str,
    retry_of: Optional[PaymentAttempt] = None,
) -> PaymentAttempt:
    """
    Create the internal payment attempt record before redirecting the user.
    """
    if not idempotency_key:
        raise ValidationError({"idempotency_key": _("Idempotency key is required.")})

    milestone = Milestone.objects.select_for_update().select_related("contract").get(
        pk=milestone.pk
    )

    contract = milestone.contract
    currency = milestone.currency or contract.currency or DEFAULT_CURRENCY

    attempt = PaymentAttempt.objects.create_attempt(
        milestone=milestone,
        idempotency_key=idempotency_key,
        initiated_by=initiated_by,
        provider=provider_name or PaymentAttempt.Provider.CHARGILY,
        retry_of=retry_of,
        success_url=success_url,
        failure_url=failure_url,
        provider_snapshot=json_safe_dict(
            {
                "milestone_public_id": milestone.public_id,
                "contract_public_id": contract.public_id,
                "amount": milestone.amount,
                "currency": currency,
            }
        ),
    )

    return attempt


@transaction.atomic
def attach_checkout_to_payment_attempt(
    *,
    attempt: PaymentAttempt,
    provider_checkout: dict[str, Any],
    provider_status: str = "",
    expires_at=None,
) -> PaymentAttempt:
    """
    Store the provider checkout ID/URL after the gateway returns it.
    """
    provider_checkout_id = (
        str(provider_checkout.get("id") or provider_checkout.get("checkout_id") or "")
        .strip()
    )
    provider_checkout_url = (
        str(provider_checkout.get("checkout_url") or provider_checkout.get("url") or "")
        .strip()
    )

    if not provider_checkout_id:
        raise ValidationError({"provider_checkout_id": _("Provider checkout ID is required.")})
    if not provider_checkout_url:
        raise ValidationError({"provider_checkout_url": _("Provider checkout URL is required.")})

    snapshot = dict(provider_checkout)
    if "data" in snapshot and isinstance(snapshot["data"], dict):
        snapshot = snapshot["data"]

    snapshot = json_safe_dict(snapshot)

    return PaymentAttempt.objects.attach_provider_checkout(
        attempt,
        provider_checkout_id=provider_checkout_id,
        provider_checkout_url=provider_checkout_url,
        provider_status=provider_status or str(provider_checkout.get("status") or ""),
        expires_at=expires_at,
        provider_snapshot=snapshot,
    )


@transaction.atomic
def settle_payment_attempt(
    *,
    attempt: PaymentAttempt,
    provider_snapshot: Optional[dict[str, Any]] = None,
    webhook_payload: Optional[dict[str, Any]] = None,
    provider_status: str = "paid",
    initiated_by=None,
) -> PaymentAttempt:
    """
    Finalize a paid provider checkout into Freewise's ledger.
    """
    attempt = (
        PaymentAttempt._base_manager.select_for_update()
        .select_related("contract", "milestone")
        .get(pk=attempt.pk)
    )

    if attempt.internal_status == PaymentAttempt.InternalStatus.SETTLED:
        _sync_funding_state_after_settlement(milestone=attempt.milestone)
        return attempt

    normalized_provider_status = (provider_status or attempt.provider_status or "").strip().lower()
    if normalized_provider_status not in {"paid"}:
        raise ValidationError({"provider_status": _("This attempt is not marked as paid.")})

    contract = attempt.contract
    milestone = attempt.milestone

    wallet = get_or_create_wallet_for_user(
        contract.client.account.user,
        currency=contract.currency or DEFAULT_CURRENCY,
    )

    record_deposit(
        wallet=wallet,
        amount=attempt.amount,
        idempotency_key=f"{attempt.idempotency_key}:deposit",
        initiated_by=initiated_by or attempt.initiated_by,
        provider_name=attempt.provider,
        provider_reference=attempt.provider_checkout_id,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=_("Provider payment settled into Freewise wallet."),
        metadata={
            "attempt_id": str(attempt.attempt_id),
            "contract_public_id": contract.public_id,
            "milestone_public_id": milestone.public_id,
            "provider_checkout_id": attempt.provider_checkout_id,
        },
    )

    hold = hold_funds_for_escrow(
        wallet=wallet,
        amount=attempt.amount,
        contract_reference=payment_contract_reference_for_milestone(milestone),
        idempotency_key=f"{attempt.idempotency_key}:escrow",
        initiated_by=initiated_by or attempt.initiated_by,
        reference_type="milestone",
        reference_id=str(milestone.public_id),
        description=_("Funds moved into escrow after provider payment."),
        metadata={
            "attempt_id": str(attempt.attempt_id),
            "contract_public_id": contract.public_id,
            "milestone_public_id": milestone.public_id,
            "provider_checkout_id": attempt.provider_checkout_id,
        },
    )

    _sync_funding_state_after_settlement(milestone=milestone)

    if provider_snapshot is not None:
        attempt.provider_snapshot = provider_snapshot
    if webhook_payload is not None:
        attempt.webhook_payload = webhook_payload
        attempt.webhook_received_at = attempt.webhook_received_at or timezone.now()
        attempt.webhook_processed_at = timezone.now()

    attempt = PaymentAttempt.objects.mark_settled(
        attempt,
        settlement_transaction=hold.funding_transaction,
        escrow_hold=hold,
        provider_status=normalized_provider_status,
    )

    return attempt


@transaction.atomic
def fail_payment_attempt(
    *,
    attempt: PaymentAttempt,
    reason: str = "",
    provider_status: str = "failed",
    provider_snapshot: Optional[dict[str, Any]] = None,
    webhook_payload: Optional[dict[str, Any]] = None,
) -> PaymentAttempt:
    """
    Mark a payment attempt as failed/canceled/expired.
    """
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)

    if provider_snapshot is not None:
        attempt.provider_snapshot = provider_snapshot
    if webhook_payload is not None:
        attempt.webhook_payload = webhook_payload
        attempt.webhook_received_at = attempt.webhook_received_at or timezone.now()
        attempt.webhook_processed_at = timezone.now()

    normalized = (provider_status or "").strip().lower()
    if normalized == "canceled" or normalized == "cancelled":
        return PaymentAttempt.objects.mark_canceled(
            attempt,
            reason=reason,
            provider_status=normalized,
        )
    if normalized == "expired":
        return PaymentAttempt.objects.mark_expired(
            attempt,
            reason=reason,
            provider_status=normalized,
        )

    return PaymentAttempt.objects.mark_failed(
        attempt,
        reason=reason,
        provider_status=normalized or "failed",
    )


@transaction.atomic
def reconcile_payment_attempt_from_provider(
    *,
    attempt: PaymentAttempt,
    provider_status: str,
    provider_snapshot: Optional[dict[str, Any]] = None,
    webhook_payload: Optional[dict[str, Any]] = None,
    initiated_by=None,
) -> PaymentAttempt:
    """
    Reconcile the attempt from the provider's latest checkout status.
    """
    normalized = (provider_status or "").strip().lower()

    if normalized == "paid":
        return settle_payment_attempt(
            attempt=attempt,
            provider_snapshot=provider_snapshot,
            webhook_payload=webhook_payload,
            provider_status=normalized,
            initiated_by=initiated_by,
        )

    if normalized in {"failed", "canceled", "expired"}:
        return fail_payment_attempt(
            attempt=attempt,
            reason=attempt.failure_reason or _("Provider checkout did not complete."),
            provider_status=normalized,
            provider_snapshot=provider_snapshot,
            webhook_payload=webhook_payload,
        )

    attempt = PaymentAttempt.objects.record_webhook(
        attempt,
        payload=webhook_payload,
        provider_status=normalized,
        provider_snapshot=provider_snapshot,
    )
    return PaymentAttempt.objects.reconcile_from_provider(
        attempt,
        provider_status=normalized,
        provider_snapshot=provider_snapshot,
    )



def _deep_find_first_string(data: Any, keys: tuple[str, ...]) -> str:
    """
    Recursively search dicts/lists for the first matching key.
    """
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                value = data.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and value.strip():
                    return value.strip()
                return str(value).strip()

        for value in data.values():
            found = _deep_find_first_string(value, keys)
            if found:
                return found

    elif isinstance(data, list):
        for item in data:
            found = _deep_find_first_string(item, keys)
            if found:
                return found

    return ""


def find_payment_attempt_for_gateway_event(
    *,
    gateway: BasePaymentGateway,
    event: GatewayWebhookEvent,
) -> Optional[PaymentAttempt]:
    """
    Find the matching PaymentAttempt using the provider checkout ID first,
    then fall back to metadata inside the payload.
    """
    if event.checkout_id:
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(
                provider=gateway.provider_name,
                provider_checkout_id=event.checkout_id,
            )
            .first()
        )
        if attempt:
            return attempt

    payload = event.payload or {}

    attempt_id = _deep_find_first_string(
        payload,
        ("attempt_id", "payment_attempt_id", "paymentAttemptId"),
    )
    if attempt_id:
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(
                provider=gateway.provider_name,
                attempt_id=attempt_id,
            )
            .first()
        )
        if attempt:
            return attempt

    milestone_id = _deep_find_first_string(payload, ("milestone_id", "milestoneId"))

    if milestone_id:
        attempt = (
            PaymentAttempt.objects.select_for_update()
            .filter(
                provider=gateway.provider_name,
                milestone_id=milestone_id,
            )
            .order_by("-attempt_number", "-created_at")
            .first()
        )
        if attempt:
            return attempt

    return None


@transaction.atomic
def process_payment_gateway_webhook(
    *,
    gateway: BasePaymentGateway,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> PaymentAttempt:
    """
    Provider-agnostic webhook processor.

    - verifies signature
    - finds the PaymentAttempt
    - records the webhook payload
    - settles/fails/reconciles the attempt
    """
    event = gateway.parse_webhook(raw_body=raw_body, headers=headers)

    if not event.signature_valid:
        raise PermissionDenied(_("Invalid webhook signature."))

    attempt = find_payment_attempt_for_gateway_event(
        gateway=gateway,
        event=event,
    )
    if not attempt:
        raise ValidationError({"detail": _("Payment attempt not found.")})

    normalized_status = gateway.normalize_status(event.status)

    # Always record the webhook first so we have an audit trail.
    attempt = PaymentAttempt.objects.record_webhook(
        attempt,
        payload=event.payload,
        provider_status=event.status,
        provider_snapshot=event.payload,
    )

    if attempt.internal_status == PaymentAttempt.InternalStatus.SETTLED:
        return attempt

    if normalized_status == "paid":
        return settle_payment_attempt(
            attempt=attempt,
            provider_snapshot=event.payload,
            webhook_payload=event.payload,
            provider_status=normalized_status,
        )

    if normalized_status in {"failed", "canceled", "expired"}:
        return fail_payment_attempt(
            attempt=attempt,
            reason=_("Provider checkout did not complete."),
            provider_status=normalized_status,
            provider_snapshot=event.payload,
            webhook_payload=event.payload,
        )

    # Unknown / processing / pending: keep the attempt updated and try to reconcile.
    attempt = PaymentAttempt.objects.reconcile_from_provider(
        attempt,
        provider_status=normalized_status,
        provider_snapshot=event.payload,
    )

    if normalized_status == "unknown" and attempt.provider_checkout_id:
        snapshot = gateway.fetch_checkout(checkout_id=attempt.provider_checkout_id)
        return reconcile_payment_attempt_from_provider(
            attempt=attempt,
            provider_status=snapshot.status,
            provider_snapshot=snapshot.raw,
            webhook_payload=event.payload,
        )

    return attempt

@transaction.atomic
def retry_payment_attempt_checkout(
    *,
    attempt: PaymentAttempt,
    gateway: BasePaymentGateway,
    idempotency_key: str,
    initiated_by=None,
    success_url: str,
    failure_url: str,
    webhook_url: str,
) -> PaymentAttempt:
    """
    Retry a payment attempt safely.

    Rules:
    - settled milestone: block
    - still-open attempt with live checkout: reuse it
    - final failed/canceled/expired attempt: create a new attempt row
    """
    current = (
        PaymentAttempt._base_manager.select_for_update()
        .select_related("contract", "milestone")
        .get(pk=attempt.pk)
    )

    latest = PaymentAttempt.objects.latest_for_milestone(current.milestone)

    if latest:
        # Refresh stale open attempts before deciding whether to reuse or retry.
        if not latest.is_final:
            latest = refresh_payment_attempt_from_provider(attempt=latest)

        latest = (
            PaymentAttempt._base_manager.select_for_update()
            .select_related("contract", "milestone")
            .get(pk=latest.pk)
        )

        if latest.internal_status == PaymentAttempt.InternalStatus.SETTLED:
            raise ValidationError({"detail": _("This milestone is already paid.")})

        if not latest.is_final:
            # Existing open checkout: keep the same attempt row.
            if latest.provider_checkout_id and latest.provider_checkout_url:
                return latest

            # Open attempt exists but checkout metadata is missing.
            target_attempt = latest
        else:
            if latest.internal_status not in {
                PaymentAttempt.InternalStatus.FAILED,
                PaymentAttempt.InternalStatus.CANCELED,
                PaymentAttempt.InternalStatus.EXPIRED,
            }:
                raise ValidationError(
                    {"detail": _("This attempt cannot be retried.")}
                )

            # Final failed/canceled/expired attempt: create a new row.
            target_attempt = PaymentAttempt.objects.create_retry(
                previous_attempt=latest,
                idempotency_key=idempotency_key,
                initiated_by=initiated_by,
                success_url=success_url,
                failure_url=failure_url,
            )
    else:
        # No prior attempt found for the milestone.
        # For safety, create a fresh row rather than mutating anything.
        target_attempt = PaymentAttempt.objects.create_attempt(
            milestone=current.milestone,
            idempotency_key=idempotency_key,
            initiated_by=initiated_by,
            provider=gateway.provider_name,
            success_url=success_url,
            failure_url=failure_url,
            retry_of=None,
            provider_snapshot={
                "milestone_public_id": current.milestone.public_id,
                "contract_public_id": current.contract.public_id,
                "provider": gateway.provider_name,
            },
        )

    description = (
        f"Freewise — {target_attempt.milestone.title} "
        f"(Contract #{target_attempt.contract.public_id})"
    )

    checkout = gateway.create_checkout(
        amount=target_attempt.amount,
        currency=target_attempt.currency,
        success_url=success_url,
        failure_url=failure_url,
        webhook_url=webhook_url,
        description=description,
        metadata={
            "attempt_id": str(target_attempt.attempt_id),
            "milestone_public_id": target_attempt.milestone.public_id,
            "contract_public_id": target_attempt.contract.public_id,
            "provider": target_attempt.provider,
            "retry_of_attempt_id": str(target_attempt.retry_of.attempt_id)
            if target_attempt.retry_of_id
            else "",
        },
        idempotency_key=idempotency_key,
    )

    target_attempt = attach_checkout_to_payment_attempt(
        attempt=target_attempt,
        provider_checkout=asdict(checkout),
        provider_status=checkout.status,
        expires_at=checkout.expires_at,
    )

    return target_attempt

@transaction.atomic
def refresh_payment_attempt_from_provider(*, attempt: PaymentAttempt) -> PaymentAttempt:
    """
    Re-read the provider checkout state and update Freewise if the attempt is still open.

    This is safe to call from read paths like:
    - payment status endpoint
    - milestone serializer
    - contract detail refresh
    """
    attempt = (
        PaymentAttempt.objects.select_for_update()
        .select_related("contract", "milestone")
        .get(pk=attempt.pk)
    )

    if attempt.is_final or not attempt.provider_checkout_id:
        return attempt

    try:
        gateway = get_payment_gateway(attempt.provider)
        snapshot = gateway.fetch_checkout(checkout_id=attempt.provider_checkout_id)
    except Exception:
        logger.exception(
            "Failed to refresh payment attempt %s from provider",
            attempt.attempt_id,
        )
        return attempt

    normalized_status = gateway.normalize_status(snapshot.status)

    if normalized_status == "paid":
        return settle_payment_attempt(
            attempt=attempt,
            provider_snapshot=snapshot.raw,
            provider_status="paid",
        )

    if normalized_status in {"failed", "canceled", "cancelled", "expired"}:
        return fail_payment_attempt(
            attempt=attempt,
            reason=_("Provider checkout did not complete."),
            provider_status=normalized_status,
            provider_snapshot=snapshot.raw,
        )

    return PaymentAttempt.objects.reconcile_from_provider(
        attempt,
        provider_status=normalized_status,
        provider_snapshot=snapshot.raw,
    )

@transaction.atomic
def milestone_has_settled_or_paid_payment(*, milestone: Milestone) -> bool:
    """
    Hard stop for funding/re-funding.

    Returns True if any payment attempt for this milestone is already:
    - settled in Freewise
    - paid by provider but not yet settled
    - otherwise still blocking a new checkout
    """
    latest = (
        PaymentAttempt.objects.for_milestone(milestone)
        .order_by("-attempt_number", "-created_at")
        .first()
    )

    if not latest:
        return False

    # If it's still open, refresh it first so provider-side expiry/paid states
    # are not left stale in the DB.
    if not latest.is_final:
        latest = refresh_payment_attempt_from_provider(attempt=latest)

    return latest.internal_status in {
        PaymentAttempt.InternalStatus.SETTLED,
        PaymentAttempt.InternalStatus.PAID_PROVIDER_NOT_SETTLED,
        PaymentAttempt.InternalStatus.PROCESSING,
        PaymentAttempt.InternalStatus.PENDING_PROVIDER,
        PaymentAttempt.InternalStatus.REDIRECTED,
        PaymentAttempt.InternalStatus.CREATED,
        PaymentAttempt.InternalStatus.RECONCILED,
    }


@transaction.atomic
def _sync_funding_state_after_settlement(*, milestone: Milestone) -> Milestone:
    """
    Bring milestone + contract into the correct funded state.

    This is idempotent and safe to call more than once.
    """
    milestone = Milestone.objects.select_for_update().select_related("contract").get(
        pk=milestone.pk
    )
    contract = milestone.contract
    now = timezone.now()

    if milestone.status == Milestone.Status.PENDING:
        milestone.status = Milestone.Status.FUNDED
        milestone.funded_at = milestone.funded_at or now
        milestone.submitted_at = None
        milestone.approved_at = None
        milestone.released_at = None
        milestone.refunded_at = None
        milestone.disputed_at = None
        milestone.full_clean()
        milestone.save(
            update_fields=[
                "status",
                "funded_at",
                "submitted_at",
                "approved_at",
                "released_at",
                "refunded_at",
                "disputed_at",
                "updated_at",
            ]
        )

    if contract.status in {Contract.Status.DRAFT, Contract.Status.PENDING_FUNDING}:
        contract.status = Contract.Status.IN_PROGRESS
        contract.active_at = contract.active_at or now
        contract.full_clean()
        contract.save(update_fields=["status", "active_at", "updated_at"])

    return milestone