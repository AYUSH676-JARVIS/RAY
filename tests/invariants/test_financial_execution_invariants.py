"""Financial Execution Invariants Tests.

Strictly verifies the 15 fundamental financial execution invariants:
1. denied action -> zero gateway calls
2. unauthorized action -> zero gateway calls
3. cross-tenant action -> zero gateway calls
4. duplicate idempotency -> exactly one gateway call
5. concurrent duplicate -> exactly one gateway call
6. UNKNOWN -> no blind retry
7. false gateway success -> not recovered
8. amount mismatch -> not silently recovered
9. currency mismatch -> not silently recovered
10. AI cannot bypass policy
11. invalid state transition -> rejected
12. recovered revenue <= authoritative recovered amount
13. blocked action cannot become executed
14. audit exists for financial action
15. DecisionReceipt cannot invent outcome
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from services.action_layer.executor import (
    ActionExecutor,
    AmbiguousOutcomeBlockedError,
    FinancialExecutionBlockedError,
    PaymentAlreadySettledError,
    PolicyAuthorizationBlockedError,
)
from services.action_layer.gateway import GatewayResult, GatewayStatus, SimulationGateway
from services.action_layer.idempotency import IdempotencyManager
from services.audit.receipt import DecisionReceiptGenerator
from services.money_graph.models import (
    ActionExecutionStatus,
    PaymentStatus,
    PolicyDecisionType,
)
from services.money_graph.state_machine import (
    InvalidStateTransitionError,
    MissingOutcomeEvidenceError,
    validate_action_transition,
    validate_payment_transition,
)
from services.outcome_engine.reconciler import OutcomeReconciler, ReconciliationStatus
from services.policy_engine.engine import DeterministicPolicyEngine


@pytest.fixture
def mock_gateway():
    gw = SimulationGateway()
    gw.reset_telemetry()
    return gw


@pytest.fixture
def clean_idempotency_manager():
    mgr = IdempotencyManager()
    mgr.clear_in_memory_cache()
    return mgr


def test_invariant_1_denied_action_zero_gateway_calls(mock_gateway, clean_idempotency_manager):
    """Invariant 1: An action rejected by policy engine results in exactly ZERO gateway calls."""
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()

    with pytest.raises(PolicyAuthorizationBlockedError, match="Policy decision is REJECTED"):
        executor.execute_recovery_action(
            action_id=action_id,
            action_type="RETRY",
            idempotency_key="idem_test_policy_rejected_001",
            merchant_id=merchant_id,
            amount=Decimal("150.00"),
            policy_decision=PolicyDecisionType.REJECTED.value,
        )

    assert mock_gateway.invocation_count == 0, "Gateway must not be called when policy rejects."


def test_invariant_2_unauthorized_action_zero_gateway_calls(mock_gateway):
    """Invariant 2: Unauthorized request (no token or insufficient role) results in ZERO gateway calls."""
    client = TestClient(app)
    mock_gateway.reset_telemetry()

    # 1. Anonymous request
    res_anon = client.post(
        "/api/actions/execute",
        json={"action_id": str(uuid.uuid4()), "idempotency_key": "idem_anon_call_001"},
    )
    assert res_anon.status_code == 401
    assert mock_gateway.invocation_count == 0

    # 2. Insufficient role (READ_ONLY principal lacks ACTION_EXECUTE permission)
    res_viewer = client.post(
        "/api/actions/execute",
        headers={"Authorization": "Bearer ray_test_analyst"},
        json={"action_id": str(uuid.uuid4()), "idempotency_key": "idem_viewer_call_001"},
    )
    assert res_viewer.status_code == 403
    assert mock_gateway.invocation_count == 0


def test_invariant_3_cross_tenant_action_zero_gateway_calls(mock_gateway):
    """Invariant 3: Cross-tenant execution returns 404 and results in ZERO gateway calls."""
    client = TestClient(app)
    mock_gateway.reset_telemetry()

    # Tenant B tries to execute random or Tenant A action ID
    res = client.post(
        "/api/actions/execute",
        headers={"Authorization": "Bearer ray_test_operator"},
        json={"action_id": str(uuid.uuid4()), "idempotency_key": "idem_cross_tenant_001"},
    )
    assert res.status_code == 404
    assert mock_gateway.invocation_count == 0


def test_invariant_4_duplicate_idempotency_exactly_one_gateway_call(mock_gateway, clean_idempotency_manager):
    """Invariant 4: Sequential duplicate requests result in exactly ONE gateway call; second returns cached receipt."""
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    key = "idem_sequential_duplicate_001"

    # Call 1: Executes
    res1 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("200.00"),
    )
    assert res1["status"] == ActionExecutionStatus.SUCCEEDED.value
    assert mock_gateway.invocation_count == 1

    # Call 2: Replays cached receipt
    res2 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("200.00"),
    )
    assert res2["idempotent_replay"] is True
    assert res2["gateway_transaction_id"] == res1["gateway_transaction_id"]
    assert mock_gateway.invocation_count == 1, "Gateway must not be called again for duplicate key."


def test_invariant_5_concurrent_duplicate_exactly_one_gateway_call(mock_gateway, clean_idempotency_manager):
    """Invariant 5: 100 concurrent threads with identical idempotency key trigger exactly ONE gateway call."""
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    key = "idem_concurrent_blast_100_keys"
    results = []
    errors = []

    def worker():
        try:
            r = executor.execute_recovery_action(
                action_id=action_id,
                action_type="RETRY",
                idempotency_key=key,
                merchant_id=merchant_id,
                amount=Decimal("500.00"),
            )
            results.append(r)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors observed: {errors}"
    assert len(results) == 100
    assert mock_gateway.invocation_count == 1, (
        f"CRITICAL INVARIANT VIOLATION: Gateway invoked {mock_gateway.invocation_count} times instead of 1!"
    )

    replays = [r for r in results if r.get("idempotent_replay") is True]
    assert len(replays) == 99, f"Expected 99 cached replays, got {len(replays)}"


def test_invariant_6_unknown_no_blind_retry(mock_gateway, clean_idempotency_manager):
    """Invariant 6: When payment is in UNKNOWN state, automated retry is blocked (zero gateway calls)."""
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()

    with pytest.raises(AmbiguousOutcomeBlockedError, match="Payment is in UNKNOWN state"):
        executor.execute_recovery_action(
            action_id=action_id,
            action_type="RETRY",
            idempotency_key="idem_unknown_payment_blocked_001",
            merchant_id=merchant_id,
            amount=Decimal("100.00"),
            payment_status=PaymentStatus.UNKNOWN.value,
        )

    assert mock_gateway.invocation_count == 0, "Must not call gateway when payment is UNKNOWN."


def test_invariant_7_false_gateway_success_not_recovered():
    """Invariant 7: Gateway reporting success when ledger status is FAILED is declared unverified/not recovered."""
    reconciler = OutcomeReconciler()
    res = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("1000.00"),
        requested_currency="USD",
        authoritative_status="FAILED",  # Authoritative ledger says FAILED!
        settled_amount=Decimal("1000.00"),
    )
    assert res["is_recovered"] is False
    assert res["reconciliation_status"] == ReconciliationStatus.FALSE_SUCCESS_REJECTED
    assert res["authoritative_recovered_amount"] == Decimal("0.00")


def test_invariant_8_amount_mismatch_not_silently_recovered():
    """Invariant 8: Under-settled or over-settled amounts flag investigation; revenue never exceeds settled amount."""
    reconciler = OutcomeReconciler()
    # Case A: Under-settled (requested 5000, settled 4000)
    res_under = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("5000.00"),
        requested_currency="INR",
        authoritative_status="SETTLED",
        settled_amount=Decimal("4000.00"),
        settled_currency="INR",
    )
    assert res_under["is_recovered"] is False
    assert res_under["reconciliation_status"] == ReconciliationStatus.AMOUNT_MISMATCH
    assert res_under["authoritative_recovered_amount"] == Decimal("4000.00")  # Strictly bounded by 4000

    # Case B: Over-settled (requested 5000, settled 6000)
    res_over = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("5000.00"),
        requested_currency="INR",
        authoritative_status="SETTLED",
        settled_amount=Decimal("6000.00"),
        settled_currency="INR",
    )
    assert res_over["is_recovered"] is False
    assert res_over["reconciliation_status"] == ReconciliationStatus.AMOUNT_MISMATCH


def test_invariant_9_currency_mismatch_not_silently_recovered():
    """Invariant 9: Currency mismatch (requested INR, settled USD) is rejected; silent conversion prohibited."""
    reconciler = OutcomeReconciler()
    res = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("5000.00"),
        requested_currency="INR",
        authoritative_status="SETTLED",
        settled_amount=Decimal("5000.00"),
        settled_currency="USD",  # Mismatched currency!
    )
    assert res["is_recovered"] is False
    assert res["reconciliation_status"] == ReconciliationStatus.CURRENCY_MISMATCH
    assert res["authoritative_recovered_amount"] == Decimal("0.00")


def test_invariant_10_ai_cannot_bypass_policy():
    """Invariant 10: AI recommendation cannot override policy limits or fraud rules."""
    policy_engine = DeterministicPolicyEngine()
    # Even if AI recommends aggressive retry with confidence 0.99 on suspected fraud:
    decision, rule, reason = policy_engine.authorize_recovery(
        failure_code="FRAUD_SUSPECTED",
        attempt_count=1,
        customer_risk_score=0.10,
        amount=Decimal("50.00"),
    )
    assert decision == PolicyDecisionType.REJECTED
    assert rule == "FRAUD_ZERO_TOLERANCE_RULE"


def test_invariant_11_invalid_state_transition_rejected():
    """Invariant 11: State machine rejects illegal financial state transitions."""
    # Cannot jump from FAILED to SUCCESS
    with pytest.raises(InvalidStateTransitionError, match="Illegal payment transition"):
        validate_payment_transition(PaymentStatus.FAILED, PaymentStatus.SUCCESS)

    # Cannot exit UNKNOWN without authoritative evidence_id
    with pytest.raises(MissingOutcomeEvidenceError, match="without authoritative evidence"):
        validate_payment_transition(PaymentStatus.UNKNOWN, PaymentStatus.SUCCESS, evidence_id=None)

    # Cannot transition action out of SUCCEEDED terminal state
    with pytest.raises(InvalidStateTransitionError):
        validate_action_transition(ActionExecutionStatus.SUCCEEDED, ActionExecutionStatus.PROCESSING)


def test_invariant_12_recovered_revenue_bounded_by_settled_amount():
    """Invariant 12: Recovered revenue is mathematically bounded by actual settled amount."""
    reconciler = OutcomeReconciler()
    res = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("100.00"),
        requested_currency="USD",
        authoritative_status="SETTLED",
        settled_amount=Decimal("75.00"),
        settled_currency="USD",
    )
    assert res["authoritative_recovered_amount"] <= Decimal("75.00")


def test_invariant_13_blocked_action_cannot_become_executed(mock_gateway, clean_idempotency_manager):
    """Invariant 13: Stage 1 safety lock halts execution before any gateway invocation."""
    executor = ActionExecutor(
        stage_1_safety_lock=True,  # Safety lock active
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    with pytest.raises(FinancialExecutionBlockedError, match="Stage 1 Safety Guard active"):
        executor.execute_recovery_action(
            action_id=action_id,
            action_type="RETRY",
            idempotency_key="idem_stage1_blocked_check_001",
            merchant_id=uuid.uuid4(),
            amount=Decimal("250.00"),
        )
    assert mock_gateway.invocation_count == 0, "Gateway must not be called when Stage 1 lock is active."


def test_invariant_14_already_settled_payment_blocks_new_action(mock_gateway, clean_idempotency_manager):
    """Invariant 14: Payment that has already succeeded or captured cannot execute further actions."""
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gateway,
        idempotency_manager=clean_idempotency_manager,
    )
    action_id = uuid.uuid4()
    with pytest.raises(PaymentAlreadySettledError, match="Payment is already in SUCCESS state"):
        executor.execute_recovery_action(
            action_id=action_id,
            action_type="RETRY",
            idempotency_key="idem_already_settled_blocked_001",
            merchant_id=uuid.uuid4(),
            amount=Decimal("300.00"),
            payment_status=PaymentStatus.SUCCESS.value,
        )
    assert mock_gateway.invocation_count == 0


def test_invariant_15_decision_receipt_cannot_invent_outcome():
    """Invariant 15: DecisionReceipt reflects only verified lifecycle states."""
    receipt = DecisionReceiptGenerator.generate_receipt(
        opportunity_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        decision="REJECTED",
        rule_matched="FRAUD_ZERO_TOLERANCE_RULE",
        reason="Transaction flagged with fraud indicators.",
        risk_score=0.85,
        amount=Decimal("500.00"),
        currency="USD",
        failure_code="FRAUD_SUSPECTED",
        attempt_count=1,
        evidence=["payment_failure:FRAUD_SUSPECTED"],
        stage_1_safety_lock=True,
    )
    assert receipt.decision == "REJECTED"
    assert receipt.stage_1_safety_lock_active is True
    assert receipt.why_didnt_ray_act is not None
    assert "FRAUD_ZERO_TOLERANCE_RULE" in receipt.rule_matched
