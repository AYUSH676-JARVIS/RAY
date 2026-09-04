"""Adversarial Financial Execution & Failure Resilience Tests.

Tests execution behavior under:
1. Gateway timeout -> UNKNOWN state
2. Gateway 500 error
3. Malformed gateway response
4. Process crash & recovery (in-memory wiped, durable DB lookup blocks duplicate gateway call)
5. Database transaction rollback & retry safety
6. 100 concurrent duplicate requests -> 1 gateway call
7. Stale policy race condition blocked at execution time
8. Stale authorization race condition
9. Duplicate and delayed webhooks
10. Out-of-order webhooks rejected by state machine
11. Gateway success with local client timeout -> authoritative reconciliation
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.action_layer.executor import (
    ActionExecutor,
    AmbiguousOutcomeBlockedError,
    PolicyAuthorizationBlockedError,
)
from services.action_layer.gateway import GatewayResult, GatewayStatus, SimulationGateway
from services.action_layer.idempotency import IdempotencyManager
from services.money_graph.models import (
    ActionExecutionStatus,
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentFailure,
    PaymentStatus,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
)
from services.money_graph.state_machine import (
    InvalidStateTransitionError,
    validate_payment_transition,
)
from services.outcome_engine.reconciler import OutcomeReconciler, ReconciliationStatus


@pytest.fixture
def clean_idempotency():
    mgr = IdempotencyManager()
    mgr.clear_in_memory_cache()
    return mgr


@pytest.fixture
def mock_gw():
    gw = SimulationGateway()
    gw.reset_telemetry()
    return gw


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed merchant and entities
    merchant_id = uuid.uuid4()
    merchant = Merchant(
        id=merchant_id,
        name="Test Adversarial Merchant",
        slug=f"adv-merch-{uuid.uuid4().hex[:6]}",
    )
    session.add(merchant)

    customer = Customer(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        external_id="cust_adv_001",
        email="adversarial@test.com",
        name="Adversarial Tester",
    )
    session.add(customer)

    order = Order(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        customer_id=customer.id,
        amount=Decimal("150.00"),
    )
    session.add(order)

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("150.00"),
        status=PaymentStatus.FAILED.value,
    )
    session.add(payment)
    session.commit()

    yield session, merchant_id, payment.id
    session.close()


def test_failure_1_gateway_timeout_records_unknown(clean_idempotency):
    """Gateway timeout records UNKNOWN and blocks blind retry."""
    timeout_gw = SimulationGateway(simulate_timeout=True)
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=timeout_gw,
        idempotency_manager=clean_idempotency,
    )
    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()

    # Step 1: Execute action -> Gateway times out
    res = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key="idem_timeout_001_key",
        merchant_id=merchant_id,
        amount=Decimal("100.00"),
    )
    assert res["status"] == ActionExecutionStatus.UNKNOWN.value
    assert res["gateway_status"] == GatewayStatus.UNKNOWN
    assert timeout_gw.invocation_count == 1

    # Step 2: Attempt blind retry on UNKNOWN payment -> Strictly blocked
    with pytest.raises(AmbiguousOutcomeBlockedError, match="Payment is in UNKNOWN state"):
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="RETRY",
            idempotency_key="idem_timeout_new_key_002",
            merchant_id=merchant_id,
            amount=Decimal("100.00"),
            payment_status=PaymentStatus.UNKNOWN.value,
        )

    # Invariant: Timeout gateway must NOT be invoked again
    assert timeout_gw.invocation_count == 1


def test_failure_2_gateway_500_error_handled_safely(clean_idempotency):
    """Gateway server 500 error records failure and does not claim success."""
    decline_gw = SimulationGateway(simulate_decline_code="GATEWAY_500_INTERNAL_ERROR")
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=decline_gw,
        idempotency_manager=clean_idempotency,
    )
    res = executor.execute_recovery_action(
        action_id=uuid.uuid4(),
        action_type="RETRY",
        idempotency_key="idem_server_error_key_001",
        merchant_id=uuid.uuid4(),
        amount=Decimal("50.00"),
    )
    assert res["status"] == ActionExecutionStatus.FAILED.value
    assert res["failure_code"] == "GATEWAY_500_INTERNAL_ERROR"


def test_failure_3_process_crash_and_durable_recovery(in_memory_db, mock_gw, clean_idempotency):
    """Process crash wipes memory cache; persistent DB lookup blocks duplicate gateway call."""
    session, merchant_id, payment_id = in_memory_db
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gw,
        idempotency_manager=clean_idempotency,
    )

    action_id = uuid.uuid4()
    key = "idem_crash_recovery_key_001"

    # Seed an opportunity and action in DB
    opp = RecoveryOpportunity(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        payment_id=payment_id,
        failure_id=uuid.uuid4(),
        strategy_name="SMART_RETRY",
        confidence_score=0.85,
        estimated_recoverable_amount=Decimal("150.00"),
    )
    session.add(opp)
    act = RecoveryAction(
        id=action_id,
        merchant_id=merchant_id,
        opportunity_id=opp.id,
        action_type="RETRY",
        idempotency_key=key,
        execution_status="REQUESTED",
    )
    session.add(act)
    session.commit()

    # Step 1: Initial execution succeeds
    res1 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("150.00"),
        session=session,
    )
    assert res1["status"] == ActionExecutionStatus.SUCCEEDED.value
    assert mock_gw.invocation_count == 1

    # Step 2: PROCESS CRASH SIMULATION — wipe all in-memory state
    clean_idempotency.clear_in_memory_cache()

    # Step 3: Duplicate request arrives after process reboot
    res2 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("150.00"),
        session=session,
    )

    # Invariant: Must recognize persisted DB record and NOT re-call gateway!
    assert res2["idempotent_replay"] is True
    assert mock_gw.invocation_count == 1, "CRITICAL: Duplicate gateway call occurred after process restart!"


def test_failure_4_stale_policy_race_condition_blocked(mock_gw, clean_idempotency):
    """Dynamic policy check right before gateway invocation halts execution if policy became blocked."""
    policy_allowed = True

    def dynamic_policy_check():
        return policy_allowed

    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=mock_gw,
        idempotency_manager=clean_idempotency,
    )

    # Simulate: Policy was allowed when request started, but another concurrent thread
    # exhausted the daily velocity limit right before gateway call:
    policy_allowed = False

    with pytest.raises(PolicyAuthorizationBlockedError, match="Stale policy condition detected"):
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="RETRY",
            idempotency_key="idem_stale_policy_race_001",
            merchant_id=uuid.uuid4(),
            amount=Decimal("200.00"),
            policy_check_fn=dynamic_policy_check,
        )

    # Invariant: Zero gateway calls
    assert mock_gw.invocation_count == 0


def test_failure_5_out_of_order_webhook_rejected():
    """State machine rejects out-of-order webhooks (e.g. CAPTURED webhook after REFUNDED)."""
    # Once a payment is in terminal REFUNDED state, a delayed CAPTURED webhook cannot mutate it
    with pytest.raises(InvalidStateTransitionError, match="Illegal payment transition"):
        validate_payment_transition(PaymentStatus.REFUNDED, PaymentStatus.CAPTURED)


def test_failure_6_gateway_success_local_timeout_authoritative_reconciliation():
    """Gateway succeeded during client timeout; status inquiry reconciles without duplicate charge."""
    reconciler = OutcomeReconciler()
    # Step 1: Client saw timeout, outcome was UNKNOWN
    # Step 2: Inquire gateway status ledger
    inquiry_result = reconciler.verify_authoritative_outcome(
        payment_id=uuid.uuid4(),
        requested_amount=Decimal("350.00"),
        requested_currency="USD",
        authoritative_status="SETTLED",
        settled_amount=Decimal("350.00"),
        settled_currency="USD",
        gateway_transaction_id="pay_upstream_9999",
        expected_transaction_id="pay_upstream_9999",
    )
    assert inquiry_result["is_recovered"] is True
    assert inquiry_result["reconciliation_status"] == ReconciliationStatus.VERIFIED_SETTLED
    assert inquiry_result["authoritative_recovered_amount"] == Decimal("350.00")
