"""Integration tests for Payment Gateway Modes: SIMULATION, SANDBOX, and LIVE boundaries."""

from decimal import Decimal
import os
import uuid
import pytest
from unittest.mock import patch, MagicMock
import httpx

from services.action_layer.gateway import (
    GatewayMode,
    PaymentGateway,
    SimulationGateway,
    RazorpayGateway,
    GatewayResult,
    GatewayStatus,
    ConfigurationError,
    Stage1ExecutionBlockedError,
)


def test_simulation_gateway_mode_and_fetch():
    """Simulation gateway operates strictly in SIMULATION mode with zero network dependency."""
    gw = SimulationGateway()
    assert gw.mode == GatewayMode.SIMULATION

    # Execute retry in simulation
    resp = gw.execute_retry(
        amount=Decimal("1250.00"),
        currency="INR",
        idempotency_key=f"sim_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_sim_1",
    )
    assert resp.status == GatewayStatus.SUCCEEDED.value
    assert resp.transaction_id.startswith("sim_tx_")

    # Fetch payment in simulation
    fetched = gw.fetch_payment("pay_sim_test_123", merchant_id="merch_1")
    assert isinstance(fetched, GatewayResult)
    assert fetched.status == GatewayStatus.SUCCEEDED.value
    assert fetched.transaction_id == "pay_sim_test_123"


def test_simulation_gateway_timeout_to_unknown():
    """Simulation gateway simulating timeout returns UNKNOWN status."""
    gw = SimulationGateway(simulate_timeout=True)
    resp = gw.execute_retry(
        amount=Decimal("500.00"),
        currency="INR",
        idempotency_key=f"sim_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_sim_2",
    )
    assert resp.status == GatewayStatus.UNKNOWN.value
    assert resp.raw_code == "GATEWAY_TIMEOUT"
    assert resp.is_retryable is False  # Blind retry forbidden


def test_razorpay_gateway_sandbox_mode():
    """Razorpay gateway with test credentials operates in SANDBOX mode."""
    gw = RazorpayGateway(
        key_id="rzp_test_sampleKey123",
        key_secret="dummy_secret_for_test",
        stage_1_safety_lock=True,
    )
    assert gw.mode == GatewayMode.SANDBOX

    # In sandbox mode with Stage 1 safety lock, execute_retry returns sandbox simulation
    resp = gw.execute_retry(
        amount=Decimal("999.00"),
        currency="INR",
        idempotency_key=f"sbx_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_sbx_1",
    )
    assert resp.status == GatewayStatus.SUCCEEDED.value
    assert resp.raw_code == "authorized_sandbox"


def test_razorpay_gateway_fetch_payment_sandbox():
    """Sandbox fetch payment returns structured data via query_status."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.content = b'{"id":"pay_test_abc123","status":"captured","amount":99900,"currency":"INR"}'
    mock_resp.json.return_value = {
        "id": "pay_test_abc123",
        "status": "captured",
        "amount": 99900,
        "currency": "INR",
    }
    mock_client.get.return_value = mock_resp
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    gw = RazorpayGateway(
        key_id="rzp_test_sampleKey123",
        key_secret="dummy_secret_for_test",
        stage_1_safety_lock=True,
        http_client=mock_client,
    )

    result = gw.fetch_payment("pay_test_abc123", merchant_id="merch_1")
    assert isinstance(result, GatewayResult)
    assert result.transaction_id == "pay_test_abc123"
    assert result.status == GatewayStatus.SUCCEEDED.value
    assert result.raw_code == "captured"


def test_razorpay_gateway_live_mode_fail_closed():
    """Razorpay gateway with live credentials MUST fail closed if credentials missing or live not authorized."""
    # If live_execution_enabled is true, stage_1_safety_lock is False, but placeholder credentials -> ConfigurationError
    with pytest.raises(ConfigurationError, match="Production LIVE Razorpay execution requires valid RAZORPAY_KEY_ID"):
        RazorpayGateway(
            key_id="rzp_test_placeholder",
            key_secret="secret_placeholder",
            live_execution_enabled=True,
            stage_1_safety_lock=False,
        )

    # Valid live credentials with stage 1 lock = True -> mode is SIMULATION/SAFE, Stage 1 lock active
    gw_safe = RazorpayGateway(
        key_id="rzp_live_sampleLiveKey999",
        key_secret="dummy_live_secret",
        live_execution_enabled=True,
        stage_1_safety_lock=True,
    )
    # Even if live_execution_enabled=True, stage_1_safety_lock=True prevents LIVE mode
    assert gw_safe.mode != GatewayMode.LIVE

    # Attempting retry raises Stage1ExecutionBlockedError
    with pytest.raises(Stage1ExecutionBlockedError, match="Live autonomous monetary movement via Razorpay is blocked"):
        gw_safe.execute_retry(
            amount=Decimal("5000.00"),
            currency="INR",
            idempotency_key=f"live_idem_{uuid.uuid4().hex[:8]}",
            customer_id="cust_live_1",
        )


def test_razorpay_failed_sandbox_execution_non_retryable():
    """Failed sandbox execution with non-retryable error (e.g. BAD_REQUEST_ERROR or CARD_EXPIRED)."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.content = b'{"error":{"code":"BAD_REQUEST_ERROR","description":"Card has expired","field":"card_expiry"}}'
    mock_resp.json.return_value = {
        "error": {
            "code": "BAD_REQUEST_ERROR",
            "description": "Card has expired",
            "field": "card_expiry",
        }
    }
    mock_client.post.return_value = mock_resp
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    gw = RazorpayGateway(
        key_id="rzp_live_sampleLiveKey999",
        key_secret="dummy_live_secret",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    result = gw.execute_retry(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key=f"fail_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_fail_1",
        merchant_id="merch_finance_1",
    )

    assert result.status == GatewayStatus.FAILED.value
    assert result.raw_code == "BAD_REQUEST_ERROR"
    assert "Card has expired" in result.raw_message
    assert result.is_retryable is False
    assert result.merchant_id == "merch_finance_1"


def test_razorpay_retryable_gateway_error_classification():
    """Gateway returns 503 or GATEWAY_ERROR classified as retryable."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.content = b'{"error":{"code":"GATEWAY_ERROR","description":"Bank network temporarily unavailable"}}'
    mock_resp.json.return_value = {
        "error": {
            "code": "GATEWAY_ERROR",
            "description": "Bank network temporarily unavailable",
        }
    }
    mock_client.post.return_value = mock_resp
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    gw = RazorpayGateway(
        key_id="rzp_live_sampleLiveKey999",
        key_secret="dummy_live_secret",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    result = gw.execute_retry(
        amount=Decimal("1500.00"),
        currency="INR",
        idempotency_key=f"retryable_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_retryable_1",
    )

    assert result.status == GatewayStatus.FAILED.value
    assert result.raw_code == "GATEWAY_ERROR"
    assert result.is_retryable is True


def test_razorpay_duplicate_idempotency_replay_tracking():
    """Duplicate requests with identical idempotency key are recorded and tracked."""
    gw = RazorpayGateway(
        key_id="rzp_test_sampleKey123",
        key_secret="dummy_secret_for_test",
        stage_1_safety_lock=True,
    )
    idem_key = f"dup_key_{uuid.uuid4().hex}"

    # First invocation
    res1 = gw.execute_retry(
        amount=Decimal("100.00"),
        currency="INR",
        idempotency_key=idem_key,
        customer_id="cust_1",
    )
    assert idem_key in gw.observed_idempotency_keys
    assert gw.observed_idempotency_keys.count(idem_key) == 1

    # Second invocation replay
    res2 = gw.execute_retry(
        amount=Decimal("100.00"),
        currency="INR",
        idempotency_key=idem_key,
        customer_id="cust_1",
    )
    assert gw.observed_idempotency_keys.count(idem_key) == 2
    assert gw.invocation_count == 2
    assert res1.status == res2.status


def test_razorpay_malformed_gateway_response_handling():
    """Gateway returning malformed/empty payload handled gracefully without uncaught exceptions."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.content = b"<html>502 Bad Gateway</html>"
    mock_resp.text = "<html>502 Bad Gateway</html>"
    mock_resp.json.side_effect = Exception("JSONDecodeError")
    mock_client.post.return_value = mock_resp
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    gw = RazorpayGateway(
        key_id="rzp_live_sampleLiveKey999",
        key_secret="dummy_live_secret",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    result = gw.execute_retry(
        amount=Decimal("500.00"),
        currency="INR",
        idempotency_key=f"malformed_idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_malformed_1",
    )

    assert result.status == GatewayStatus.FAILED.value
    assert result.raw_code in ("HTTP_502", "SERVER_ERROR")


def test_razorpay_webhook_signature_verification():
    """Cryptographic HMAC-SHA256 signature verification accepts valid signatures and rejects invalid."""
    secret = "rzp_webhook_secret_production_32chars!"
    gw = RazorpayGateway(
        key_id="rzp_test_sampleKey123",
        key_secret="dummy_secret",
        webhook_secret=secret,
        stage_1_safety_lock=True,
    )

    raw_body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_123","amount":50000}}}}'
    import hmac, hashlib
    valid_sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    # Valid signature
    assert gw.verify_webhook_signature(raw_body, valid_sig) is True

    # Tampered body
    assert gw.verify_webhook_signature(raw_body + b"tampered", valid_sig) is False

    # Invalid signature
    assert gw.verify_webhook_signature(raw_body, "invalid_hex_signature") is False

    # Empty inputs
    assert gw.verify_webhook_signature(b"", valid_sig) is False
    assert gw.verify_webhook_signature(raw_body, "") is False


def test_razorpay_webhook_normalization():
    """Razorpay webhook event correctly normalizes paise amounts and statuses."""
    gw = RazorpayGateway(
        key_id="rzp_test_sampleKey123",
        key_secret="dummy_secret",
        stage_1_safety_lock=True,
    )

    raw_event = {
        "event_id": "evt_test_webhook_001",
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_test_xyz789",
                    "order_id": "order_test_abc456",
                    "amount": 75000,  # 750.00 INR in paise
                    "currency": "INR",
                    "status": "captured",
                }
            }
        }
    }

    normalized = gw.normalize_webhook_event(raw_event)
    assert normalized.gateway_name == "Razorpay"
    assert normalized.event_id == "evt_test_webhook_001"
    assert normalized.event_type == "payment.captured"
    assert normalized.payment_id == "pay_test_xyz789"
    assert normalized.order_id == "order_test_abc456"
    assert normalized.amount == Decimal("750.00")
    assert normalized.currency == "INR"
    assert normalized.status == GatewayStatus.SUCCEEDED.value
