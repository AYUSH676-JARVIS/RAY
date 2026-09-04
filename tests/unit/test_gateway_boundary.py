"""Tests for Payment Gateway Abstraction & Simulation Boundary (Phase 1).

Verifies:
1. All 7 deterministic SimulationGateway scenarios:
   - SUCCESS
   - TERMINAL_DECLINE
   - NETWORK_ERROR
   - TIMEOUT
   - UNKNOWN
   - DUPLICATE_REQUEST
   - MALFORMED_RESPONSE
2. Asynchronous actions: create_payment_link, update_payment_method
3. Status inquiry
4. Webhook event normalization
5. Razorpay constant-time HMAC signature verification and Stage 1 isolation.
"""

import hmac
import hashlib
import uuid
from decimal import Decimal
import pytest

from services.action_layer.gateway import (
    GatewayStatus,
    SimulationScenario,
    SimulationGateway,
    RazorpayGateway,
    MalformedGatewayResponseError,
    Stage1ExecutionBlockedError,
)


def test_simulation_scenario_success():
    gw = SimulationGateway(scenario=SimulationScenario.SUCCESS)
    res = gw.execute_retry(
        amount=Decimal("150.00"),
        currency="USD",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_1",
    )
    assert res.status == GatewayStatus.SUCCEEDED.value
    assert res.raw_code == "00"
    assert res.transaction_id is not None
    assert gw.invocation_count == 1


def test_simulation_scenario_terminal_decline():
    gw = SimulationGateway(scenario=SimulationScenario.TERMINAL_DECLINE, simulate_decline_code="CARD_EXPIRED")
    res = gw.execute_retry(
        amount=Decimal("150.00"),
        currency="USD",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_1",
    )
    assert res.status == GatewayStatus.FAILED.value
    assert res.raw_code == "CARD_EXPIRED"
    assert res.is_retryable is False


def test_simulation_scenario_network_error():
    gw = SimulationGateway(scenario=SimulationScenario.NETWORK_ERROR)
    res = gw.execute_retry(
        amount=Decimal("150.00"),
        currency="USD",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_1",
    )
    assert res.status == GatewayStatus.FAILED.value
    assert res.raw_code == "NETWORK_ERROR"
    assert res.is_retryable is True  # Network errors permit retry


def test_simulation_scenario_timeout():
    gw = SimulationGateway(scenario=SimulationScenario.TIMEOUT)
    res = gw.execute_retry(
        amount=Decimal("150.00"),
        currency="USD",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_1",
    )
    assert res.status == GatewayStatus.UNKNOWN.value
    assert res.raw_code == "GATEWAY_TIMEOUT"
    assert res.is_retryable is False  # Blind retry forbidden


def test_simulation_scenario_unknown():
    gw = SimulationGateway(scenario=SimulationScenario.UNKNOWN)
    res = gw.execute_retry(
        amount=Decimal("150.00"),
        currency="USD",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
        customer_id="cust_1",
    )
    assert res.status == GatewayStatus.UNKNOWN.value
    assert res.raw_code == "AMBIGUOUS_GATEWAY_STATE"
    assert res.is_retryable is False


def test_simulation_scenario_duplicate_request():
    gw = SimulationGateway(scenario=SimulationScenario.SUCCESS)
    key = f"idem_dup_{uuid.uuid4().hex[:8]}"
    
    # First invocation succeeds
    res1 = gw.execute_retry(Decimal("100.00"), "USD", key, "cust_1")
    assert res1.status == GatewayStatus.SUCCEEDED.value
    
    # Second invocation with same key returns cached result
    res2 = gw.execute_retry(Decimal("100.00"), "USD", key, "cust_1")
    assert res2.status == GatewayStatus.SUCCEEDED.value
    assert res2.transaction_id == res1.transaction_id
    assert gw.invocation_count == 2


def test_simulation_scenario_malformed_response():
    gw = SimulationGateway(scenario=SimulationScenario.MALFORMED_RESPONSE)
    with pytest.raises(MalformedGatewayResponseError):
        gw.execute_retry(Decimal("100.00"), "USD", f"idem_{uuid.uuid4().hex[:8]}", "cust_1")


def test_payment_link_and_update_method():
    gw = SimulationGateway()
    link = gw.create_payment_link(Decimal("250.00"), "USD", "idem_plink", "cust_2")
    assert link.status == "CREATED"
    assert "https://" in link.short_url

    update = gw.update_payment_method("cust_2", "idem_update")
    assert update.status == "PENDING_CUSTOMER_ACTION"
    assert "https://" in update.update_url


def test_status_inquiry():
    gw = SimulationGateway()
    status_res = gw.query_status("tx_123456")
    assert status_res.status == GatewayStatus.SUCCEEDED.value
    assert status_res.transaction_id == "tx_123456"


def test_razorpay_hmac_signature_verification():
    secret = "rzp_webhook_secret_xyz123"
    rzp = RazorpayGateway(webhook_secret=secret)
    body = b'{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_123","amount":50000}}}}'
    
    # Valid HMAC
    valid_sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert rzp.verify_webhook_signature(body, valid_sig) is True

    # Invalid HMAC
    assert rzp.verify_webhook_signature(body, "invalid_bogus_signature") is False
    assert rzp.verify_webhook_signature(b"", valid_sig) is False
    assert rzp.verify_webhook_signature(body, "") is False


def test_razorpay_webhook_normalization():
    rzp = RazorpayGateway()
    raw = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_fail_999",
                    "order_id": "order_777",
                    "amount": 250000,  # 2500.00 INR in paise
                    "currency": "INR",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Payment was declined by issuing bank",
                }
            }
        }
    }
    normalized = rzp.normalize_webhook_event(raw)
    assert normalized.gateway_name == "Razorpay"
    assert normalized.payment_id == "pay_fail_999"
    assert normalized.amount == Decimal("2500.00")
    assert normalized.currency == "INR"
    assert normalized.status == GatewayStatus.FAILED.value
    assert normalized.failure_code == "BAD_REQUEST_ERROR"


def test_razorpay_stage1_isolation_blocks_live_money_movement():
    rzp = RazorpayGateway(live_execution_enabled=False)
    res = rzp.execute_retry(Decimal("2500.00"), "INR", "idem_rzp_safe", "cust_1")
    assert res.payload.get("simulated") is True
    assert res.raw_code == "authorized_sandbox"

    rzp_live = RazorpayGateway(live_execution_enabled=True)
    with pytest.raises(Stage1ExecutionBlockedError):
        rzp_live.execute_retry(Decimal("2500.00"), "INR", "idem_rzp_live", "cust_1")
