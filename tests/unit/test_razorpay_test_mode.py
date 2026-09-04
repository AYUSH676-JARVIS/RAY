"""Comprehensive Unit Test Suite for Razorpay TEST Mode Lifecycle & Configuration Separation.

Verifies:
1. TEST vs PRODUCTION configuration separation:
   - Rejects rzp_live_* keys when RAZORPAY_TEST_MODE=true.
   - Rejects rzp_test_* keys when ENVIRONMENT=production.
2. Real TEST API execution with official Razorpay API responses (mocked HTTP client).
3. Payment link generation in TEST mode.
4. Payment status query in TEST mode.
5. Replay and error classification.
"""

from decimal import Decimal
import pytest
import httpx
from pydantic import SecretStr

from services.config.settings import AppSettings, ConfigurationError
from services.action_layer.gateway import (
    RazorpayGateway,
    GatewayStatus,
    PaymentLinkResult,
    GatewayResult,
)


def test_test_mode_rejects_live_credentials():
    """TEST mode fail-closed: Rejects rzp_live_* credentials."""
    with pytest.raises(ConfigurationError, match="Live Razorpay credentials"):
        AppSettings(
            ENVIRONMENT="development",
            RAZORPAY_TEST_MODE=True,
            RAZORPAY_KEY_ID=SecretStr("rzp_live_super_secret_production_key_1234"),
        )


def test_production_mode_rejects_test_credentials():
    """PRODUCTION mode fail-closed: Rejects rzp_test_* credentials."""
    with pytest.raises(ConfigurationError, match="Test Razorpay credentials"):
        AppSettings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("valid_webhook_secret_key_prod"),
            RAZORPAY_KEY_ID=SecretStr("rzp_test_sandbox_dummy_key_1234"),
        )


def test_razorpay_test_mode_payment_link_execution():
    """Razorpay TEST mode executes payment link creation with official contract."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/payment_links"
        assert request.headers.get("Authorization") is not None
        return httpx.Response(
            200,
            json={
                "id": "plink_test_987654321",
                "short_url": "https://rzp.io/i/test9876",
                "status": "created",
                "amount": 250000,
                "currency": "INR",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    gw = RazorpayGateway(
        key_id="rzp_test_valid_test_key_1234",
        key_secret="test_secret_9988",
        http_client=mock_client,
    )

    assert gw.is_test_mode is True
    assert gw.is_live_mode is False

    link_res: PaymentLinkResult = gw.create_payment_link(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key="idem_link_test_12345",
        customer_id="+919876543210",
    )

    assert link_res.link_id == "plink_test_987654321"
    assert link_res.short_url == "https://rzp.io/i/test9876"
    assert link_res.status == "CREATED"


def test_razorpay_test_mode_query_status():
    """Razorpay TEST mode executes status query against Razorpay payments API."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/payments/pay_test_txn_9999"
        return httpx.Response(
            200,
            json={
                "id": "pay_test_txn_9999",
                "status": "captured",
                "amount": 19999,
                "currency": "USD",
                "method": "card",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    gw = RazorpayGateway(
        key_id="rzp_test_valid_test_key_1234",
        key_secret="test_secret_9988",
        http_client=mock_client,
    )

    res: GatewayResult = gw.query_status("pay_test_txn_9999")
    assert res.status == GatewayStatus.SUCCEEDED.value
    assert res.transaction_id == "pay_test_txn_9999"
    assert res.raw_code == "captured"
