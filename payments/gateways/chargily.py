
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from .base import (
    BasePaymentGateway,
    GatewayCheckoutResult,
    GatewayCheckoutSnapshot,
    GatewayWebhookEvent,
)


class ChargilyGatewayError(RuntimeError):
    pass


class ChargilyGateway(BasePaymentGateway):
    """
    Chargily adapter for the provider-agnostic gateway interface.

    Keep all Chargily-specific behavior here so adding a second gateway later
    does not pollute views, services, or settlement logic.
    """

    provider_name = "chargily"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        webhook_signature_header: Optional[str] = None,
        timeout: int = 20,
    ):
        self.api_key = api_key or getattr(settings, "CHARGILY_SECRET_KEY", "")
        self.secret_key = secret_key or getattr(settings, "CHARGILY_SECRET_KEY", "")
        self.base_url = (
            base_url
            or getattr(settings, "CHARGILY_API_BASE_URL", "")
            or "https://pay.chargily.net/test/api/v2"
        ).rstrip("/")
        self.webhook_signature_header = (
            webhook_signature_header
            or getattr(settings, "CHARGILY_WEBHOOK_SIGNATURE_HEADER", "X-Chargily-Signature")
        )
        self.timeout = timeout

        if not self.api_key:
            raise ChargilyGatewayError("Chargily secret key is not configured.")

    def _headers(self, *, idempotency_key: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _unwrap_payload(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        if isinstance(payload, dict):
            return payload
        return {}

    def _first_non_empty(self, data: Any, keys: tuple[str, ...]) -> str:
        """
        Recursively search dicts/lists and return the first matching string.
        """
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            for value in data.values():
                found = self._first_non_empty(value, keys)
                if found:
                    return found

        elif isinstance(data, list):
            for item in data:
                found = self._first_non_empty(item, keys)
                if found:
                    return found

        return ""

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None

        if isinstance(value, datetime):
            return value

        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed is not None:
                return parsed
            try:
                value = value.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(value)
                return parsed
            except Exception:
                return None

        return None

    def _parse_amount(self, value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0.00")

    def _provider_status(self, payload: dict[str, Any]) -> str:
        status = self._first_non_empty(payload, ("status", "state", "payment_status"))
        return status.strip().lower() if status else "unknown"

    def _provider_checkout_id(self, payload: dict[str, Any]) -> str:
        return self._first_non_empty(
            payload,
            ("id", "checkout_id", "checkoutId", "reference", "checkout_reference"),
        )

    def _provider_checkout_url(self, payload: dict[str, Any]) -> str:
        return self._first_non_empty(
            payload,
            ("checkout_url", "url", "payment_url", "redirect_url", "redirectUrl"),
        )

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
        payload = {
            "amount": str(amount),
            "currency": (currency or "DZD").strip().lower(),
            "success_url": success_url,
            "failure_url": failure_url,
            "webhook_endpoint": webhook_url,
            "description": description,
            "metadata": metadata or {},
        }

        response = requests.post(
            self._endpoint("/checkouts"),
            headers=self._headers(idempotency_key=idempotency_key),
            json=payload,
            timeout=self.timeout,
        )

        if not response.ok:
            raise ChargilyGatewayError(
                f"Chargily checkout creation failed ({response.status_code}): {response.text}"
            )

        raw_json = response.json()
        data = self._unwrap_payload(raw_json)

        checkout_id = self._provider_checkout_id(data)
        checkout_url = self._provider_checkout_url(data)
        status = self._provider_status(data)

        if not checkout_id:
            raise ChargilyGatewayError("Chargily did not return a checkout ID.")
        if not checkout_url:
            raise ChargilyGatewayError("Chargily did not return a checkout URL.")

        expires_at = self._parse_datetime(
            data.get("expires_at")
            or data.get("expiry")
            or data.get("expired_at")
            or data.get("expiresAt")
        )

        return GatewayCheckoutResult(
            provider=self.provider_name,
            checkout_id=checkout_id,
            checkout_url=checkout_url,
            status=status,
            amount=self._parse_amount(data.get("amount", amount)),
            currency=str(data.get("currency") or currency or "DZD").upper(),
            expires_at=expires_at,
            raw=data,
        )

    def fetch_checkout(self, *, checkout_id: str) -> GatewayCheckoutSnapshot:
        if not checkout_id:
            raise ChargilyGatewayError("checkout_id is required.")

        response = requests.get(
            self._endpoint(f"/checkouts/{checkout_id}"),
            headers=self._headers(),
            timeout=self.timeout,
        )

        if not response.ok:
            raise ChargilyGatewayError(
                f"Chargily checkout fetch failed ({response.status_code}): {response.text}"
            )

        raw_json = response.json()
        data = self._unwrap_payload(raw_json)
        status = self._provider_status(data)

        return GatewayCheckoutSnapshot(
            provider=self.provider_name,
            checkout_id=checkout_id,
            status=status,
            amount=self._parse_amount(data.get("amount")) if data.get("amount") is not None else None,
            currency=str(data.get("currency") or "").upper() or None,
            expires_at=self._parse_datetime(
                data.get("expires_at")
                or data.get("expiry")
                or data.get("expired_at")
                or data.get("expiresAt")
            ),
            raw=data,
        )

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> bool:
        if not self.secret_key:
            return False

        signature = (
            headers.get("signature")
            or headers.get("Signature")
            or headers.get("SIGNATURE")
            or ""
        ).strip()

        if not signature:
            return False

        expected = hmac.new(
            key=self.secret_key.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    def parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> GatewayWebhookEvent:
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except Exception as exc:
            raise ChargilyGatewayError(f"Invalid Chargily webhook payload: {exc}") from exc

        if not isinstance(payload, dict):
            payload = {}

        body = self._unwrap_payload(payload)
        provider_status = self._provider_status(body or payload)
        checkout_id = self._provider_checkout_id(body or payload)
        event_name = self._first_non_empty(
            body or payload,
            ("event", "event_name", "type", "name"),
        ) or provider_status or "unknown"

        return GatewayWebhookEvent(
            provider=self.provider_name,
            event_name=event_name,
            checkout_id=checkout_id,
            status=provider_status,
            signature_valid=self.verify_webhook(raw_body=raw_body, headers=headers),
            payload=payload,
            headers={str(k): str(v) for k, v in headers.items()},
            raw_body=raw_body,
        )

    def normalize_status(self, provider_status: str) -> str:
        status = (provider_status or "").strip().lower()

        if status in {"paid", "success", "succeeded", "completed"}:
            return "paid"
        if status in {"failed", "declined", "rejected"}:
            return "failed"
        if status in {"canceled", "cancelled"}:
            return "canceled"
        if status in {"expired", "timeout", "timed_out"}:
            return "expired"
        if status in {"processing", "pending", "in_progress"}:
            return "processing"

        return "unknown"