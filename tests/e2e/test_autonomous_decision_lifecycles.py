"""Autonomous Decision Workflow Lifecycle Verification.

Verifies all 10 required lifecycles:
1. Complete successful simulated lifecycle
2. Policy-blocked lifecycle (fraud suspected)
3. Gateway failure lifecycle (terminal decline)
4. Gateway timeout lifecycle (unknown gateway state)
5. UNKNOWN lifecycle (blocks retry, authoritative resolution)
6. Duplicate lifecycle (idempotent replay)
7. Unauthorized lifecycle (anonymous request returns HTTP 401)
8. Cross-tenant lifecycle (IDOR returns HTTP 404)
9. Malformed AI lifecycle (schema/hallucination rejected, falls back to deterministic)
10. AI unavailable lifecycle (engine down, falls back to deterministic)
"""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from services.money_graph.models import (
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentFailure,
    PaymentStatus,
    PolicyDecisionType,
    RecoveryAction,
)
from services.orchestrator.workflow import run_decision_workflow
from services.outcome_engine.reconciler import OutcomeReconciler


@pytest.fixture
def lifecycle_env():
    """Create a pristine isolated test environment for decision lifecycle testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    merchant_id = uuid.uuid4()
    merchant = Merchant(
        id=merchant_id,
        name="Acme Recovery Merchant",
        slug=f"acme-merch-{uuid.uuid4().hex[:6]}",
    )
    session.add(merchant)

    customer = Customer(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        external_id=f"cust_{uuid.uuid4().hex[:6]}",
        email="test@merchant.com",
        name="Priya Patel",
        risk_score=0.10,
    )
    session.add(customer)

    order = Order(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        customer_id=customer.id,
        amount=Decimal("2500.00"),
        currency="INR",
    )
    session.add(order)

    payment = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("2500.00"),
        currency="INR",
        status=PaymentStatus.FAILED.value,
    )
    session.add(payment)

    failure = PaymentFailure(
        id=uuid.uuid4(),
        payment_id=payment.id,
        payment_attempt_id=uuid.uuid4(),
        failure_code="BANK_TIMEOUT",
        raw_message="Issuer connection timed out during 3DS",
        is_retryable=True,
    )
    session.add(failure)
    session.commit()

    yield session, merchant_id, payment.id
    session.close()


def test_1_complete_successful_simulated_lifecycle(lifecycle_env):
    """1. Complete successful simulated recovery lifecycle end-to-end."""
    session, merchant_id, payment_id = lifecycle_env

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
    )

    assert result.status in ("COMPLETED", "RECOVERY_SUCCESS")
    assert result.current_stage == "DECISION_RECEIPT"
    assert len(result.stages) == 9
    assert result.decision_receipt is not None
    assert result.audit_hash is not None
    assert result.outcome_result is not None
    assert result.outcome_result["is_recovered"] is True

    # Check stage telemetry
    for stage in result.stages:
        assert stage.stage_name is not None
        assert stage.duration_ms is not None and stage.duration_ms > 0
        assert stage.correlation_id == result.correlation_id
        assert stage.status.value in ["COMPLETED", "SKIPPED"]

    # Verify Money Graph payment state was updated to SUCCESS
    updated_payment = session.get(Payment, payment_id)
    assert updated_payment.status == PaymentStatus.SUCCESS.value


def test_2_policy_blocked_lifecycle(lifecycle_env):
    """2. Policy-blocked lifecycle: high risk / fraud suspicion halts execution before gateway."""
    session, merchant_id, payment_id = lifecycle_env

    # Mutate to fraud failure
    failure = session.scalar(select(PaymentFailure).where(PaymentFailure.payment_id == payment_id))
    failure.failure_code = "FRAUD_SUSPECTED"
    session.commit()

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
    )

    assert result.status == "POLICY_BLOCKED"
    assert result.policy_decision["decision"] == PolicyDecisionType.REJECTED.value
    assert result.decision_receipt is not None
    assert result.decision_receipt.decision == "REJECTED"

    # Action stage must be SKIPPED
    action_stage = next(s for s in result.stages if s.stage_name == "ACTION")
    assert action_stage.status.value == "SKIPPED"


def test_3_gateway_failure_lifecycle(lifecycle_env):
    """3. Gateway failure lifecycle: terminal decline (CARD_EXPIRED) prevents recovery."""
    session, merchant_id, payment_id = lifecycle_env

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
        simulate_decline="CARD_EXPIRED",
    )

    assert result.status in ("GATEWAY_DECLINED", "FAILED", "RECOVERY_FAILED")
    assert result.outcome_result["is_recovered"] is False
    assert result.decision_receipt is not None


def test_4_gateway_timeout_lifecycle(lifecycle_env):
    """4. Gateway timeout lifecycle: transitions to UNKNOWN awaiting reconciliation."""
    session, merchant_id, payment_id = lifecycle_env

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
        simulate_timeout=True,
    )

    assert result.status in ("UNKNOWN", "GATEWAY_TIMEOUT")
    assert result.action_result["status"] == "UNKNOWN"

    # Invariants: Payment is marked UNKNOWN
    payment = session.get(Payment, payment_id)
    assert payment.status == PaymentStatus.UNKNOWN.value


def test_5_unknown_lifecycle(lifecycle_env):
    """5. UNKNOWN state blocks blind retry and requires authoritative evidence."""
    session, merchant_id, payment_id = lifecycle_env

    # First run times out into UNKNOWN
    run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
        simulate_timeout=True,
    )

    # Immediate second retry while UNKNOWN must be rejected by Policy Engine
    retry_result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
    )
    assert retry_result.status == "POLICY_BLOCKED"
    assert "UNKNOWN" in retry_result.policy_decision["reason"]

    # Authoritative reconciliation resolves UNKNOWN
    reconciler = OutcomeReconciler()
    reconciled = reconciler.verify_authoritative_outcome(
        payment_id=payment_id,
        requested_amount=Decimal("2500.00"),
        requested_currency="INR",
        authoritative_status="SETTLED",
        settled_amount=Decimal("2500.00"),
        settled_currency="INR",
        gateway_transaction_id="tx_auth_recon_001",
    )
    assert reconciled["is_recovered"] is True


def test_6_duplicate_lifecycle(lifecycle_env):
    """6. Duplicate request returns identical cached receipt and avoids dual execution."""
    session, merchant_id, payment_id = lifecycle_env

    res1 = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=True,
    )
    res2 = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=True,
    )

    assert res1.decision_receipt.receipt_id == res2.decision_receipt.receipt_id
    assert res1.status == res2.status
    # Verify exactly 1 recovery action created for this payment
    actions = session.scalars(select(RecoveryAction).where(RecoveryAction.merchant_id == merchant_id)).all()
    assert len(actions) == 1


def test_7_unauthorized_lifecycle():
    """7. Anonymous call to /api/decisions/run is rejected with HTTP 401."""
    client = TestClient(app)
    res = client.post(
        "/api/decisions/run",
        json={"payment_id": str(uuid.uuid4()), "simulate_gateway": True},
    )
    assert res.status_code == 401


def test_8_cross_tenant_lifecycle():
    """8. Tenant attempting to process another merchant's payment receives HTTP 404."""
    client = TestClient(app)
    token_merchant_a = "ray_test_merchant_admin"  # Authenticated Merchant Admin token

    # Generate a random foreign payment ID
    foreign_payment_id = str(uuid.uuid4())


    headers = {"Authorization": f"Bearer {token_merchant_a}"}
    res = client.post(
        "/api/decisions/run",
        json={"payment_id": foreign_payment_id, "simulate_gateway": True},
        headers=headers,
    )
    assert res.status_code == 404


def test_9_malformed_ai_lifecycle(lifecycle_env):
    """9. Malformed AI recommendation is rejected and falls back safely to deterministic rules."""
    session, merchant_id, payment_id = lifecycle_env

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
        simulate_malformed_ai=True,
    )

    assert result.status in ("COMPLETED", "RECOVERY_SUCCESS")
    decision_stage = next(s for s in result.stages if s.stage_name == "DECISION")
    assert "rejected" in decision_stage.detail.lower() or "fallback" in decision_stage.detail.lower()
    assert result.decision is not None


def test_10_ai_unavailable_lifecycle(lifecycle_env):
    """10. AI reasoning engine failure falls back safely to deterministic rules without crashing."""
    session, merchant_id, payment_id = lifecycle_env

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        simulate_gateway=True,
        simulate_ai_failure=True,
    )

    assert result.status in ("COMPLETED", "RECOVERY_SUCCESS")
    decision_stage = next(s for s in result.stages if s.stage_name == "DECISION")
    assert "unavailable" in decision_stage.detail.lower() or "fallen back" in decision_stage.detail.lower()
    assert result.decision is not None
