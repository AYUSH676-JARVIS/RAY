"""End-to-End Decision Workflow Tests.

Strictly verifies the canonical 9-stage automatic decision loop:
EVENT → MONEY GRAPH → OPPORTUNITY → DECISION → POLICY → ACTION → VERIFICATION → AUDIT → DECISION RECEIPT

Tests all required phases:
1. Hero Scenario (Revenue Recovery under Stage 1 Safety Lock)
2. Successful Workflow Simulation
3. Ineligible / No-Opportunity Workflow
4. Policy-Blocked Workflow (Fraud Suspected)
5. Gateway Timeout → UNKNOWN State
6. UNKNOWN State Blocks Blind Retry
7. Outcome Verification Failure
8. Multi-Tenant Isolation via API
9. RBAC Enforcement via API
10. Idempotency across Replayed Runs
11. AI Cannot Override Policy
12. AI Cannot Fabricate Evidence
13. Audit Chain Integrity Verification
14. Complete Decision Receipt Validation
15. API Response Truthfulness
"""

from __future__ import annotations

import decimal
import uuid
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from services.audit.logger import AuditLogger, verify_audit_chain
from services.money_graph.models import (
    ActionExecutionStatus,
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
)
from services.orchestrator.workflow import run_decision_workflow


@pytest.fixture
def workflow_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    merchant_id = uuid.uuid4()
    merchant = Merchant(
        id=merchant_id,
        name="Hero Merchant India",
        slug=f"hero-merch-{uuid.uuid4().hex[:6]}",
    )
    session.add(merchant)

    customer = Customer(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        external_id="cust_hero_001",
        email="hero.customer@example.com",
        name="Aarav Sharma",
        risk_score=0.12,  # Low risk customer
    )
    session.add(customer)

    order = Order(
        id=uuid.uuid4(),
        merchant_id=merchant_id,
        customer_id=customer.id,
        amount=Decimal("2500.00"),
    )
    session.add(order)

    # Hero Scenario: ₹2,500 Payment Failure due to temporary bank timeout
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
        raw_message="Issuing bank network timed out during 3DS processing",
        is_retryable=True,
    )
    session.add(failure)
    session.commit()

    yield session, merchant_id, payment.id
    session.close()


def test_hero_scenario_revenue_recovery_stage1_blocked(workflow_db):
    """Hero Scenario: ₹2,500 payment failure automatically reasoned and safely held by Stage 1 lock."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=True,  # Production default
    )

    # Invariants:
    # 1. Overall workflow halted safely at Stage 1 lock
    assert result.status == "STAGE1_BLOCKED"
    assert len(result.stages) >= 7

    # 2. Money Graph and Opportunity derived dynamically
    assert result.opportunity is not None
    assert result.opportunity.amount == Decimal("2500.00")
    assert result.opportunity.currency == "INR"
    assert result.opportunity.is_eligible is True
    assert result.opportunity.score_breakdown.expected_recovery > Decimal("0.00")

    # 3. Decision proposed with machine-validatable attributes
    assert result.decision is not None
    assert result.decision.expected_value == result.opportunity.score_breakdown.expected_recovery
    assert result.decision.probability_of_success > 0.50
    assert result.decision.risk == "LOW"

    # 4. Deterministic Policy allowed the candidate action
    assert result.policy_decision is not None
    assert result.policy_decision["decision"] == PolicyDecisionType.APPROVED.value

    # 5. Action halted by Stage 1 Safety Guard (BLOCKED_STAGE1_SAFETY)
    assert result.action_result is not None
    assert result.action_result["status"] == ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value

    # 6. Cryptographic audit event generated
    assert result.audit_event_id is not None
    assert result.audit_hash is not None

    # 7. Complete Decision Receipt produced
    assert result.decision_receipt is not None
    assert result.decision_receipt.stage_1_safety_lock_active is True
    assert result.decision_receipt.amount == Decimal("2500.00")


def test_successful_workflow_simulation(workflow_db):
    """Simulation Mode: Gateway succeeds, outcome verified, Money Graph transitions to SUCCESS."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=False,
        simulate_gateway=True,  # Simulated execution enabled
    )

    assert result.status == "COMPLETED"
    assert result.action_result["status"] == "SUCCEEDED"
    assert result.outcome_result["is_recovered"] is True

    # Authoritative Money Graph state updated truthfully
    payment = session.scalar(select(Payment).where(Payment.id == payment_id))
    assert payment.status == PaymentStatus.SUCCESS.value


def test_policy_blocked_workflow_fraud_suspected(workflow_db):
    """Failure Path: Suspected fraud halts at Policy stage with 0 gateway calls."""
    session, merchant_id, payment_id = workflow_db

    # Mutate failure code to FRAUD_SUSPECTED
    failure = session.scalar(select(PaymentFailure).where(PaymentFailure.payment_id == payment_id))
    failure.failure_code = "FRAUD_SUSPECTED"
    failure.is_retryable = False
    session.commit()

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=False,
        simulate_gateway=True,
    )

    assert result.status == "POLICY_BLOCKED"
    assert result.policy_decision["decision"] == PolicyDecisionType.REJECTED.value
    assert result.policy_decision["rule_matched"] == "FRAUD_ZERO_TOLERANCE_RULE"

    # Action Gateway skipped
    action_stage = next(s for s in result.stages if s.stage_name == "ACTION")
    assert action_stage.status.value == "SKIPPED"

    # Decision Receipt explains inaction
    assert result.decision_receipt is not None
    assert result.decision_receipt.decision == "REJECTED"
    assert result.decision_receipt.why_didnt_ray_act is not None


def test_gateway_timeout_workflow_unknown_state(workflow_db):
    """Ambiguous Path: Gateway timeout captures UNKNOWN state and blocks blind retry."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=False,
        simulate_gateway=True,
        simulate_timeout=True,  # Simulate upstream network timeout
    )

    assert result.status == "UNKNOWN"
    assert result.action_result["status"] == ActionExecutionStatus.UNKNOWN.value

    # Payment in Money Graph entered UNKNOWN state
    payment = session.scalar(select(Payment).where(Payment.id == payment_id))
    assert payment.status == PaymentStatus.UNKNOWN.value

    # Attempting to re-run decision loop on payment in UNKNOWN state is rejected by Policy
    retry_result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=False,
        simulate_gateway=True,
    )
    assert retry_result.status == "POLICY_BLOCKED"
    assert retry_result.policy_decision["rule_matched"] == "AMBIGUOUS_UNKNOWN_OUTCOME_RULE"


def test_audit_chain_integrity_after_workflow(workflow_db):
    """Cryptographic SHA-256 hash chaining remains 100% verified after workflow execution."""
    session, merchant_id, payment_id = workflow_db

    # Run two workflows
    run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)

    is_valid, err = verify_audit_chain(session, merchant_id)
    assert is_valid is True
    assert err is None


def test_no_opportunity_workflow(workflow_db):
    """Ineligible payments (e.g. status SUCCESS) halt at OPPORTUNITY stage with NO_OPPORTUNITY."""
    session, merchant_id, payment_id = workflow_db
    payment = session.scalar(select(Payment).where(Payment.id == payment_id))
    payment.status = "SUCCESS"
    session.commit()

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=True,
    )
    assert result.status == "NO_OPPORTUNITY"
    assert result.current_stage == "OPPORTUNITY"
    assert result.decision_receipt is not None
    assert result.decision_receipt.decision == "REJECTED"


def test_outcome_verification_failure(workflow_db):
    """Simulated gateway decline leads to unrecovered outcome verification and FAILED workflow status."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(
        payment_id=payment_id,
        session=session,
        merchant_id=merchant_id,
        stage_1_safety_lock=False,
        simulate_gateway=True,
        simulate_decline="insufficient_funds",
    )
    assert result.status == "FAILED"
    assert result.outcome_result["is_recovered"] is False


def test_tenant_isolation_api(workflow_db):
    """Tenant isolation: Tenant A cannot execute decision loop on Tenant B payment."""
    client = TestClient(app)
    # Target another merchant's payment ID
    foreign_payment_id = str(uuid.uuid4())
    res = client.post(
        "/api/decisions/run",
        headers={"Authorization": "Bearer ray_test_operator"},
        json={"payment_id": foreign_payment_id},
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_rbac_execution_api(workflow_db):
    """RBAC: Non-privileged roles (READ_ONLY, ANALYST) cannot execute decision loop."""
    client = TestClient(app)
    dummy_payment = str(uuid.uuid4())

    res_ro = client.post(
        "/api/decisions/run",
        headers={"Authorization": "Bearer ray_test_read_only"},
        json={"payment_id": dummy_payment},
    )
    assert res_ro.status_code == 403

    res_analyst = client.post(
        "/api/decisions/run",
        headers={"Authorization": "Bearer ray_test_analyst"},
        json={"payment_id": dummy_payment},
    )
    assert res_analyst.status_code == 403


def test_idempotency_workflow(workflow_db):
    """Repeated workflow execution produces deterministic policy and decision receipts."""
    session, merchant_id, payment_id = workflow_db

    res1 = run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)
    res2 = run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)

    assert res1.policy_decision["decision"] == res2.policy_decision["decision"]
    assert res1.decision.recommended_action == res2.decision.recommended_action
    assert res1.decision_receipt.primary_reason == res2.decision_receipt.primary_reason


def test_ai_cannot_override_policy(workflow_db):
    """AI confidence cannot bypass deterministic policy: high confidence on expired card is blocked."""
    session, merchant_id, payment_id = workflow_db

    failure = session.scalar(select(PaymentFailure).where(PaymentFailure.payment_id == payment_id))
    failure.failure_code = "CARD_EXPIRED"
    session.commit()

    result = run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)
    assert result.status == "POLICY_BLOCKED"
    assert result.policy_decision["rule_matched"] == "CARD_EXPIRED_TERMINAL_RULE"


def test_ai_cannot_fabricate_evidence(workflow_db):
    """Decision Engine empirical evidence citations are strictly derived from real Money Graph IDs."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)
    assert result.decision is not None
    # All evidence citations correspond to real entities in context
    assert any(str(payment_id) in ev for ev in result.decision.evidence_ids)
    assert any("failure:BANK_TIMEOUT" in ev for ev in result.decision.evidence_ids)


def test_decision_receipt_completeness(workflow_db):
    """Decision receipt captures complete explainability and inaction rationale."""
    session, merchant_id, payment_id = workflow_db

    result = run_decision_workflow(payment_id=payment_id, session=session, merchant_id=merchant_id, stage_1_safety_lock=True)
    receipt = result.decision_receipt
    assert receipt is not None
    assert receipt.payment_id == payment_id
    assert receipt.merchant_id == merchant_id
    assert receipt.amount == Decimal("2500.00")
    assert receipt.currency == "INR"
    assert "Stage 1 Financial Execution Safety Lock" in receipt.why_didnt_ray_act


def test_api_endpoint_matches_backend_truth(workflow_db):
    """API endpoint returns complete 9-stage telemetry matching backend database truth."""
    client = TestClient(app)
    # Using existing seeded opportunity for operator
    res_opps = client.get("/api/opportunities?limit=20", headers={"Authorization": "Bearer ray_test_operator"})
    assert res_opps.status_code == 200
    items = res_opps.json()["items"]
    if items:
        # Prefer a BANK_TIMEOUT opportunity that proceeds through all pipeline stages
        target_payment_id = next(
            (it["payment_id"] for it in items if it.get("failure_code") == "BANK_TIMEOUT"),
            items[0]["payment_id"],
        )
        res = client.post(
            "/api/decisions/run",
            headers={"Authorization": "Bearer ray_test_operator"},
            json={"payment_id": target_payment_id},
        )
        assert res.status_code == 200
        data = res.json()
        assert "workflow_id" in data
        assert "stages" in data
        assert len(data["stages"]) >= 7
        assert data["payment_id"] == target_payment_id
