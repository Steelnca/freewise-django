"""
Chargily Pay service wrapper for Freewise.

Thin adapter only:
- create checkout sessions
- verify webhook signatures

Business logic belongs in services.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings

DEFAULT_TEST_BASE_URL = "https://pay.chargily.net/test/api/v2"
DEFAULT_LIVE_BASE_URL = "https://pay.chargily.net/api/v2"
DEFAULT_CURRENCY = "dzd"
MONEY_QUANTIZER = Decimal("0.01")


def get_base_url() -> str:
    return getattr(
        settings,
        "CHARGILY_API_BASE_URL",
        DEFAULT_TEST_BASE_URL if settings.DEBUG else DEFAULT_LIVE_BASE_URL,
    ).rstrip("/")


def get_secret_key() -> str:
    secret_key = getattr(settings, "CHARGILY_SECRET_KEY", "")
    if not secret_key:
        raise RuntimeError("CHARGILY_SECRET_KEY is not configured.")
    return secret_key


def _to_decimal(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _to_checkout_amount(amount: Decimal | str | int | float) -> int:
    """
    Chargily expects an integer checkout amount.
    Keep the Freewise amount as the source of truth and do not scale twice.
    """
    return int(
        Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def build_checkout_payload(
    *,
    amount: Decimal | str | int | float,
    description: str,
    success_url: str,
    failure_url: str,
    webhook_url: str,
    metadata: dict[str, Any] | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any]:
    return {
        "amount": _to_checkout_amount(amount),
        "currency": currency.lower().strip(),
        "success_url": success_url,
        "failure_url": failure_url,
        "webhook_endpoint": webhook_url,
        "description": description,
        "metadata": metadata or {},
    }


def create_checkout(
    *,
    amount: Decimal | str | int | float,
    description: str,
    success_url: str,
    failure_url: str,
    webhook_url: str,
    metadata: dict[str, Any] | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any]:
    payload = build_checkout_payload(
        amount=amount,
        description=description,
        success_url=success_url,
        failure_url=failure_url,
        webhook_url=webhook_url,
        metadata=metadata,
        currency=currency,
    )

    import requests

    url = f"{get_base_url()}/checkouts"
    headers = {
        "Authorization": f"Bearer {get_secret_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    response = requests.post(url, json=payload, headers=headers, timeout=20)
    response.raise_for_status()

    checkout = response.json()
    return {
        **checkout,
        "amount": str(_to_decimal(amount)),
        "currency": payload["currency"],
    }


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    *,
    secret: str | None = None,
) -> bool:
    if not payload or not signature:
        return False

    signing_secret = (secret or get_secret_key()).encode("utf-8")
    digest = hmac.new(signing_secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def normalize_webhook_signature(signature: str) -> str:
    return (signature or "").strip()