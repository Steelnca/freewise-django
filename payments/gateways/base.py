
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional


@dataclass(slots=True)
class GatewayCheckoutResult:
    """
    Provider-agnostic checkout payload returned by any payment gateway.
    """

    provider: str
    checkout_id: str
    checkout_url: str
    status: str
    amount: Decimal
    currency: str
    expires_at: Optional[datetime] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GatewayCheckoutSnapshot:
    """
    Latest known checkout status from the provider.
    """

    provider: str
    checkout_id: str
    status: str
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    expires_at: Optional[datetime] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GatewayWebhookEvent:
    """
    Normalized webhook event that Freewise can understand regardless of provider.
    """

    provider: str
    event_name: str
    checkout_id: str
    status: str
    signature_valid: bool
    payload: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    raw_body: bytes = b""


class BasePaymentGateway(ABC):
    """
    Provider contract for all payment gateways.

    Freewise code should depend on this interface, not on Chargily directly.
    """

    provider_name: str

    @abstractmethod
    def create_checkout(
        self,
        *,
        amount: Decimal,
        currency: str,
        success_url: str,
        failure_url: str,
        webhook_url: str,
        description: str,
        metadata: Optional[dict[str, Any]] = None,
        idempotency_key: str,
    ) -> GatewayCheckoutResult:
        """
        Create a checkout session on the provider.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_checkout(self, *, checkout_id: str) -> GatewayCheckoutSnapshot:
        """
        Fetch the latest checkout status from the provider.
        """
        raise NotImplementedError

    @abstractmethod
    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> bool:
        """
        Verify that the webhook really came from the provider.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayWebhookEvent:
        """
        Normalize a provider webhook into a provider-agnostic event.
        """
        raise NotImplementedError

    def normalize_status(self, provider_status: str) -> str:
        """
        Map a provider status to a generic Freewise payment status.

        Override per provider if needed.
        """
        status = (provider_status or "").strip().lower()

        if status in {"paid", "succeeded", "success", "completed", "settled", "done", "captured"}:
            return "paid"
        if status in {"failed", "declined", "rejected", "error"}:
            return "failed"
        if status in {"canceled", "cancelled", "voided", "abandoned"}:
            return "canceled"
        if status in {"expired", "timeout", "timed_out", "out_of_time", "ended"}:
            return "expired"
        if status in {"processing", "pending", "in_progress", "authorized", "awaiting_payment", "requires_payment_method"}:
            return "processing"

        return "unknown"

    def can_settle(self, provider_status: str) -> bool:
        return self.normalize_status(provider_status) == "paid"

    def can_fail(self, provider_status: str) -> bool:
        return self.normalize_status(provider_status) in {
            "failed",
            "canceled",
            "expired",
        }