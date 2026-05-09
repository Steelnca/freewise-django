
"""
Chargily Pay service wrapper.
Handles checkout creation and webhook signature verification.
"""
import hashlib
import hmac

from django.conf import settings

from chargily_pay import ChargilyClient
from chargily_pay.settings import CHARGILIY_URL, CHARGILIY_TEST_URL

PLATFORM_FEE_PERCENT = getattr(settings, 'FREEWISE_PLATFORM_FEE_PERCENT', 10)


def get_client() -> ChargilyClient:
    return ChargilyClient(
        key    = settings.CHARGILY_API_KEY,
        secret = settings.CHARGILY_API_SECRET,
        url    = CHARGILIY_URL if not settings.DEBUG else CHARGILIY_TEST_URL,
    )


def calculate_fees(amount: float) -> dict:
    """Return platform fee and freelancer payout amounts."""
    fee            = round(amount * PLATFORM_FEE_PERCENT / 100, 2)
    freelancer_gets = round(amount - fee, 2)
    return {'platform_fee': fee, 'freelancer_gets': freelancer_gets}


def create_checkout(milestone, success_url: str, failure_url: str, webhook_url: str) -> dict:
    """
    Create a Chargily checkout for a milestone payment.
    Returns the checkout object from Chargily.
    """
    client = get_client()
    fees   = calculate_fees(float(milestone.amount))

    checkout = client.create_checkout({
        'amount':       int(milestone.amount * 100),  # Chargily uses centimes
        'currency':     'dzd',
        'success_url':  success_url,
        'failure_url':  failure_url,
        'webhook_url':  webhook_url,
        'description':  f'Freewise — {milestone.title} (Contract #{milestone.contract.pk})',
        'metadata': {
            'milestone_id': milestone.pk,
            'contract_id':  milestone.contract.pk,
        },
    })
    return {**checkout, **fees}


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify that the webhook came from Chargily."""
    secret  = settings.CHARGILY_API_SECRET.encode()
    digest  = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)