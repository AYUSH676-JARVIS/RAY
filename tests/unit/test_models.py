"""Unit tests for SQLAlchemy models in services/money_graph."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from services.money_graph.models import (
    ActionExecutionStatus,
    ActionType,
    ActorType,
    AgentRun,
    AgentRunStatus,
    AuditEvent,
    Base,
    Customer,
    FailureCategory,
    Merchant,
    OpportunityStatus,
    Order,
    OrderStatus,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    PolicyDecision,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
    ToolCall,
)


@pytest.fixture
def db_session():
    """Create a fresh in-memory SQLite database for fast isolated unit testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_uuid_and_utc_timestamps_default(db_session: Session):
    """Verify models default to valid UUIDs and timezone-aware UTC timestamps."""
    merchant = Merchant(name="Test Corp", slug="test-corp")
    db_session.add(merchant)
    db_session.commit()

    assert isinstance(merchant.id, uuid.UUID)
    assert isinstance(merchant.created_at, datetime)
    assert merchant.created_at.tzinfo is not None
    assert merchant.status == "ACTIVE"
    assert merchant.currency == "USD"


def test_customer_and_order_creation(db_session: Session):
    """Verify customer and order creation with foreign key linkage."""
    merchant = Merchant(name="Acme Inc", slug="acme-inc")
    db_session.add(merchant)
    db_session.flush()

    customer = Customer(
        merchant_id=merchant.id,
        external_id="ext_001",
        email="test@acme.com",
        name="John Doe",
        risk_score=0.125,
    )
    db_session.add(customer)
    db_session.flush()

    order = Order(
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount=Decimal("99.99"),
        currency="USD",
        status=OrderStatus.COMPLETED.value,
    )
    db_session.add(order)
    db_session.commit()

    assert order.amount == Decimal("99.99")
    assert order.customer_id == customer.id
    assert order.merchant_id == merchant.id


def test_all_twelve_models_persisted(db_session: Session):
    """Verify all 12 core models can be persisted and linked."""
    # 1. Merchant
    merchant = Merchant(name="Apex", slug="apex")
    db_session.add(merchant)
    db_session.flush()

    # 2. Customer
    customer = Customer(merchant_id=merchant.id, external_id="c_1", email="c1@apex.com", name="Alice")
    db_session.add(customer)
    db_session.flush()

    # 3. Order
    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("150.00"))
    db_session.add(order)
    db_session.flush()

    # 4. Payment
    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("150.00"),
        status=PaymentStatus.FAILED.value,
    )
    db_session.add(payment)
    db_session.flush()

    # 5. PaymentAttempt
    attempt = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        idempotency_key="idem_test_123",
        gateway_name="Stripe",
        status="FAILED",
    )
    db_session.add(attempt)
    db_session.flush()

    # 6. PaymentFailure
    failure = PaymentFailure(
        payment_id=payment.id,
        payment_attempt_id=attempt.id,
        failure_code=FailureCategory.INSUFFICIENT_FUNDS.value,
        raw_message="Insufficient funds",
        is_retryable=True,
    )
    db_session.add(failure)
    db_session.flush()

    # 7. RecoveryOpportunity
    opp = RecoveryOpportunity(
        merchant_id=merchant.id,
        payment_id=payment.id,
        failure_id=failure.id,
        strategy_name="SMART_RETRY",
        confidence_score=0.85,
        estimated_recoverable_amount=Decimal("150.00"),
        status=OpportunityStatus.OPEN.value,
    )
    db_session.add(opp)
    db_session.flush()

    # 8. RecoveryAction (safeguarded execution status)
    action = RecoveryAction(
        opportunity_id=opp.id,
        action_type=ActionType.SMART_RETRY.value,
        idempotency_key="idem_act_test_456",
        execution_status=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
    )
    db_session.add(action)
    db_session.flush()

    # 9. PolicyDecision
    policy = PolicyDecision(
        opportunity_id=opp.id,
        action_id=action.id,
        decision=PolicyDecisionType.APPROVED.value,
        rule_matched="MAX_DAILY_RETRY_LIMIT",
        reason="Velocity rule passed",
        risk_score=0.15,
    )
    db_session.add(policy)
    db_session.flush()

    # 10. AgentRun
    run = AgentRun(
        agent_name="recovery",
        trigger_type="AUTOMATED_SCAN",
        status=AgentRunStatus.COMPLETED.value,
    )
    db_session.add(run)
    db_session.flush()

    # 11. ToolCall
    tool = ToolCall(
        run_id=run.id,
        tool_name="evaluate_recovery_policy",
        input_payload_json={"payment_id": str(payment.id)},
        output_payload_json={"approved": True},
        status="SUCCESS",
        duration_ms=42,
    )
    db_session.add(tool)
    db_session.flush()

    # 12. AuditEvent
    audit = AuditEvent(
        run_id=run.id,
        entity_type="PAYMENT",
        entity_id=payment.id,
        event_type="PAYMENT_ANALYZED",
        actor_type=ActorType.AI_AGENT.value,
        actor_id="agent:recovery",
    )
    db_session.add(audit)
    db_session.commit()

    # Query back to verify persistence
    assert db_session.scalar(select(Merchant).where(Merchant.id == merchant.id)) is not None
    assert db_session.scalar(select(Payment).where(Payment.id == payment.id)) is not None
    assert db_session.scalar(select(RecoveryAction).where(RecoveryAction.id == action.id)).execution_status == ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value
