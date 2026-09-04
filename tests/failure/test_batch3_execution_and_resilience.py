"""Financial Execution Resilience & State Machine Failure Tests.

Verifies:
1. Gateway timeouts enter UNKNOWN status rather than declaring false failure.
2. UNKNOWN state cannot transition to SUCCESS without authoritative evidence.
3. Illegal state transitions are strictly rejected.
4. Gateway abstraction contract conformance.
5. Stage 1 Safety Lock remains impenetrable.
"""

from __future__ import annotations

import decimal
import uuid
import pytest

from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
from services.action_layer.gateway import GatewayStatus, RazorpayGatewayStub, SimulationGateway
from services.money_graph.models import ActionExecutionStatus, PaymentStatus
from services.money_graph.state_machine import (
    InvalidStateTransitionError,
    MissingOutcomeEvidenceError,
    validate_action_transition,
    validate_payment_transition,
)


def test_gateway_timeout_records_unknown_state():
    """Ambiguous gateway network timeout yields UNKNOWN status."""
    timeout_gateway = SimulationGateway(simulate_timeout=True)
    executor = ActionExecutor(stage_1_safety_lock=False, gateway=timeout_gateway)

    result = executor.execute_recovery_action(
        action_id=uuid.uuid4(),
        action_type="SMART_RETRY",
        idempotency_key=f"idem_timeout_{uuid.uuid4().hex[:8]}",
        amount=decimal.Decimal("250.00"),
    )

    assert result["status"] == ActionExecutionStatus.UNKNOWN.value
    assert result["gateway_status"] == GatewayStatus.UNKNOWN
    assert "timed out" in result["detail"]


def test_payment_state_machine_enforces_valid_transitions():
    """Payment transitions strictly enforce state machine topology."""
    # Legal transitions
    assert validate_payment_transition(PaymentStatus.CREATED, PaymentStatus.PROCESSING) is True
    assert validate_payment_transition(PaymentStatus.PROCESSING, PaymentStatus.SUCCESS) is True
    assert validate_payment_transition(PaymentStatus.SUCCESS, PaymentStatus.REFUNDED) is True

    # Illegal jump: CREATED -> CAPTURED directly
    with pytest.raises(InvalidStateTransitionError, match="Illegal payment transition"):
        validate_payment_transition(PaymentStatus.CREATED, PaymentStatus.CAPTURED)

    # Illegal transition from terminal state: FAILED -> SUCCESS
    with pytest.raises(InvalidStateTransitionError, match="Illegal payment transition"):
        validate_payment_transition(PaymentStatus.FAILED, PaymentStatus.SUCCESS)


def test_unknown_outcome_requires_authoritative_evidence_to_resolve():
    """Resolving an UNKNOWN payment requires evidence, blocking blind success resolution."""
    # Without evidence -> MUST raise MissingOutcomeEvidenceError
    with pytest.raises(MissingOutcomeEvidenceError, match="without authoritative evidence"):
        validate_payment_transition(
            PaymentStatus.UNKNOWN,
            PaymentStatus.SUCCESS,
            evidence_id=None,
        )

    # With authoritative evidence ID -> permitted
    assert validate_payment_transition(
        PaymentStatus.UNKNOWN,
        PaymentStatus.SUCCESS,
        evidence_id="gw_inquiry_evt_9981",
    ) is True


def test_action_state_machine_rejects_illegal_transitions():
    """RecoveryAction transitions enforce lifecycle constraints."""
    # Legal
    assert validate_action_transition(ActionExecutionStatus.REQUESTED, ActionExecutionStatus.AUTHORIZED) is True
    assert validate_action_transition(ActionExecutionStatus.AUTHORIZED, ActionExecutionStatus.PROCESSING) is True

    # Illegal jump: REQUESTED -> SUCCEEDED without authorization and execution
    with pytest.raises(InvalidStateTransitionError, match="Illegal action transition"):
        validate_action_transition(ActionExecutionStatus.REQUESTED, ActionExecutionStatus.SUCCEEDED)


def test_razorpay_gateway_stub_contract():
    """RazorpayGatewayStub implements PaymentGateway contract correctly."""
    stub = RazorpayGatewayStub()
    res = stub.execute_retry(
        amount=decimal.Decimal("99.99"),
        currency="USD",
        idempotency_key="idem_rzp_contract_test",
        customer_id="cust_rzp_1",
    )
    assert res.gateway_name == "Razorpay"
    assert res.status == GatewayStatus.SUCCEEDED
    assert res.transaction_id is not None
    assert res.latency_ms > 0

    status_res = stub.query_status(res.transaction_id)
    assert status_res.status == GatewayStatus.SUCCEEDED


def test_stage_1_safety_lock_default_active():
    """ActionExecutor by default has Stage 1 lock active and blocks execution."""
    executor = ActionExecutor()
    assert executor.stage_1_safety_lock is True

    with pytest.raises(FinancialExecutionBlockedError, match="Stage 1 Safety Guard active"):
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="SMART_RETRY",
            idempotency_key=f"idem_safe_{uuid.uuid4().hex[:8]}",
        )
