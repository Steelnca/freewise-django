
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from payments.reconciliation import reconcile_attempt
from payments.views import PaymentAttemptStatusView, PaymentGatewayWebhookView
from payments.gateways.base import GatewayCheckoutSnapshot, GatewayWebhookEvent


class PaymentRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("payments.views.process_payment_gateway_webhook")
    @patch("payments.views.get_payment_gateway")
    def test_webhook_view_returns_200(self, mock_get_gateway, mock_process):
        mock_get_gateway.return_value = MagicMock(provider_name="chargily")

        fake_attempt = SimpleNamespace(
            attempt_id="11111111-1111-1111-1111-111111111111",
            internal_status="SETTLED",
            provider_status="paid",
        )
        mock_process.return_value = fake_attempt

        request = self.factory.post(
            "/api/payments/webhooks/chargily/",
            data=b"{}",
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        response = PaymentGatewayWebhookView.as_view()(request, provider_name="chargily")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["attempt_status"], "SETTLED")
        self.assertEqual(response.data["provider_status"], "paid")

    @patch("payments.reconciliation.reconcile_payment_attempt_from_provider")
    @patch("payments.reconciliation.get_payment_gateway")
    def test_reconcile_attempt_paid_path(self, mock_get_gateway, mock_reconcile_from_provider):
        attempt = SimpleNamespace(
            pk=1,
            attempt_id="11111111-1111-1111-1111-111111111111",
            provider="chargily",
            provider_checkout_id="chk_123",
            is_final=False,
        )

        gateway = MagicMock()
        gateway.normalize_status.return_value = "paid"
        gateway.fetch_checkout.return_value = GatewayCheckoutSnapshot(
            provider="chargily",
            checkout_id="chk_123",
            status="paid",
            raw={"status": "paid", "id": "chk_123"},
        )
        mock_get_gateway.return_value = gateway

        updated = SimpleNamespace(internal_status="SETTLED")
        mock_reconcile_from_provider.return_value = updated

        result = reconcile_attempt(attempt)

        self.assertEqual(result.internal_status, "SETTLED")
        gateway.fetch_checkout.assert_called_once_with(checkout_id="chk_123")
        mock_reconcile_from_provider.assert_called_once()

    @patch("payments.views.reconcile_attempt")
    @patch("payments.views.get_object_or_404")
    @patch("payments.views.ensure_party_access")
    def test_status_view_polls_and_reconciles(self, mock_access, mock_get_object, mock_reconcile):
        fake_contract = SimpleNamespace(id=9)
        fake_attempt = SimpleNamespace(
            attempt_id="11111111-1111-1111-1111-111111111111",
            contract=fake_contract,
            contract_id=9,
            milestone_id=5,
            provider="chargily",
            provider_checkout_id="chk_123",
            provider_checkout_url="https://checkout.example",
            internal_status="PAID_PROVIDER_NOT_SETTLED",
            provider_status="paid",
            is_final=False,
            amount="1200.00",
            currency="DZD",
            provider_paid_at=None,
            webhook_received_at=None,
            webhook_processed_at=None,
            reconciled_at=None,
            settled_at=None,
            failure_reason="",
        )
        mock_get_object.return_value = fake_attempt
        mock_reconcile.return_value = fake_attempt

        request = self.factory.get("/api/payments/attempts/uuid/status/")
        request.user = SimpleNamespace(is_authenticated=True)

        response = PaymentAttemptStatusView.as_view()(request, attempt_id=fake_attempt.attempt_id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["internal_status"], "PAID_PROVIDER_NOT_SETTLED")
        self.assertEqual(response.data["retryable"], False)
        mock_reconcile.assert_called_once()
        mock_access.assert_called_once()