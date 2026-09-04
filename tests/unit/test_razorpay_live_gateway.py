import decimal
from decimal import Decimal
import pytest
import httpx
from unittest.mock import MagicMock, patch

from services.action_layer.gateway import (
    RazorpayGateway,
    GatewayStatus,
    ConfigurationError,
    Stage1ExecutionBlockedError,
)


def test_razorpay_fail_closed_on_missing_credentials_in_live_mode():
    """Live mode must fail closed if required credentials are missing."""
    with pytest.raises(ConfigurationError) as exc_info:
        RazorpayGateway(
            key_id="rzp_test_placeholder",
            key_secret="secret_placeholder",
            live_execution_enabled=True,
            stage_1_safety_lock=False,
        )
    assert "requires valid RAZORPAY_KEY_ID" in str(exc_info.value)


def test_razorpay_stage1_lock_blocks_execution():
    """Stage 1 lock takes priority and blocks live execution even with credentials."""
    gw = RazorpayGateway(
        key_id="rzp_live_real_key",
        key_secret="real_secret_123456",
        live_execution_enabled=True,
        stage_1_safety_lock=True,
    )
    with pytest.raises(Stage1ExecutionBlockedError):
        gw.execute_retry(
            amount=Decimal("2500.00"),
            currency="INR",
            idempotency_key="idem_test_1",
            customer_id="cust_1",
        )


def test_razorpay_live_successful_retry():
    """Mocked live retry execution returning successful captured payment."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = b'{"id":"pay_live_123456","status":"captured","amount":250000,"currency":"INR"}'
    mock_response.json.return_value = {
        "id": "pay_live_123456",
        "status": "captured",
        "amount": 250000,
        "currency": "INR",
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key="idem_live_succ",
        customer_id="cust_123",
        correlation_id="corr_live_1",
    )

    assert res.status == GatewayStatus.SUCCEEDED.value
    assert res.transaction_id == "pay_live_123456"
    assert res.raw_code == "captured"
    assert res.is_retryable is False
    assert gw.invocation_count == 1
    mock_client.post.assert_called_once()


def test_razorpay_live_retryable_error():
    """Gateway/Server errors (500, 503) must be classified as is_retryable=True."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 503
    mock_response.content = b'{"error":{"code":"GATEWAY_ERROR","description":"Bank network timeout"}}'
    mock_response.json.return_value = {
        "error": {"code":"GATEWAY_ERROR","description":"Bank network timeout"}
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key="idem_live_err",
        customer_id="cust_123",
    )

    assert res.status == GatewayStatus.FAILED.value
    assert res.raw_code == "GATEWAY_ERROR"
    assert res.is_retryable is True


def test_razorpay_live_non_retryable_error():
    """Terminal client declines (CARD_EXPIRED, BAD_REQUEST_ERROR) must be is_retryable=False."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 400
    mock_response.content = b'{"error":{"code":"CARD_EXPIRED","description":"Card has expired"}}'
    mock_response.json.return_value = {
        "error": {"code":"CARD_EXPIRED","description":"Card has expired"}
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key="idem_live_decline",
        customer_id="cust_123",
    )

    assert res.status == GatewayStatus.FAILED.value
    assert res.raw_code == "CARD_EXPIRED"
    assert res.is_retryable is False


def test_razorpay_live_timeout_transitions_to_unknown():
    """Socket timeout during live execution must transition to UNKNOWN, NOT FAILED."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.side_effect = httpx.TimeoutException("Socket read timed out")

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("2500.00"),
        currency="INR",
        idempotency_key="idem_live_timeout",
        customer_id="cust_123",
    )

    # Invariant: UNKNOWN != FAILED
    assert res.status == GatewayStatus.UNKNOWN.value
    assert res.raw_code == "GATEWAY_TIMEOUT"
    assert res.is_retryable is False


def test_razorpay_secret_redaction():
    """Payloads returned by the gateway must have secrets and credentials redacted."""
    gw = RazorpayGateway()
    dirty_dict = {
        "id": "pay_123",
        "key_secret": "super_secret_string",
        "card_number": "4111111111111111",
        "cvv": "123",
        "nested": {
            "token": "tok_abcdef",
            "safe_field": "public_data",
        }
    }
    cleaned = gw._redact_secrets(dirty_dict)
    assert cleaned["key_secret"] == "[REDACTED]"
    assert cleaned["card_number"] == "[REDACTED]"
    assert cleaned["cvv"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["nested"]["safe_field"] == "public_data"


def test_razorpay_live_401_unauthorized():
    """401 Unauthorized must be treated as non-retryable configuration failure."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 401
    mock_response.content = b'{"error":{"code":"BAD_REQUEST_ERROR","description":"Invalid Key or Secret"}}'
    mock_response.json.return_value = {
        "error": {"code": "BAD_REQUEST_ERROR", "description": "Invalid Key or Secret"}
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("100.00"),
        currency="INR",
        idempotency_key="idem_401_test",
        customer_id="cust_123",
    )
    assert res.status == GatewayStatus.FAILED.value
    assert res.is_retryable is False


def test_razorpay_live_429_rate_limit():
    """429 Rate Limit must be treated as is_retryable=True."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 429
    mock_response.content = b'{"error":{"code":"RATE_LIMIT_EXCEEDED","description":"Too many requests"}}'
    mock_response.json.return_value = {
        "error": {"code": "RATE_LIMIT_EXCEEDED", "description": "Too many requests"}
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_response

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("100.00"),
        currency="INR",
        idempotency_key="idem_429_test",
        customer_id="cust_123",
    )
    assert res.status == GatewayStatus.FAILED.value
    assert res.is_retryable is True


def test_razorpay_live_network_error_connection_drop():
    """Network connection drop must return FAILED with is_retryable=True."""
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.post.side_effect = httpx.NetworkError("Connection reset by peer")

    gw = RazorpayGateway(
        key_id="rzp_live_abc123",
        key_secret="mock_test_secret_for_unit_tests",
        live_execution_enabled=True,
        stage_1_safety_lock=False,
        http_client=mock_client,
    )

    res = gw.execute_retry(
        amount=Decimal("100.00"),
        currency="INR",
        idempotency_key="idem_network_drop",
        customer_id="cust_123",
    )
    assert res.status == GatewayStatus.FAILED.value
    assert res.raw_code == "NETWORK_ERROR"
    assert res.is_retryable is True

