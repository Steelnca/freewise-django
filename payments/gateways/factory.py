
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BasePaymentGateway
from .chargily import ChargilyGateway

GATEWAY_REGISTRY: dict[str, type[BasePaymentGateway]] = {
    "chargily": ChargilyGateway,
}


def get_payment_gateway(name: str | None = None) -> BasePaymentGateway:
    """
    Return the configured payment gateway.
    Defaults to Chargily for now, but the view never hardcodes it.
    """
    gateway_name = (
        name
        or getattr(settings, "FREEWISE_DEFAULT_PAYMENT_GATEWAY", "chargily")
    ).strip().lower()

    gateway_cls = GATEWAY_REGISTRY.get(gateway_name)
    if gateway_cls is None:
        raise ImproperlyConfigured(
            f"Unknown payment gateway '{gateway_name}'. "
            f"Add it to payments/gateways/factory.py."
        )

    return gateway_cls()