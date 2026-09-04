"""Unit tests for idempotency key validation and financial execution safeguards."""

import uuid
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from services.money_graph.models import (
    ActionExecutionStatus,
    ActionType,
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    RecoveryAction,
    RecoveryOpportunity,
)


@pytest.fixture
def db():
    """Isolated in-memory database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_payment_attempt_idempotency_uniqueness(db: Session):
    """Verify duplicate idempotency keys on payment attempts raise IntegrityError."""
    merchant = Merchant(name="M", slug="m")
    db.add(merchant)
    db.flush()

    customer = Customer(merchant_id=merchant.id, external_id="c", email="c@m.com", name="C")
    db.add(customer)
    db.flush()

    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("50.00"))
    db.add(order)
    db.flush()

    payment = Payment(merchant_id=merchant.id, order_id=order.id, customer_id=customer.id, amount=Decimal("50.00"))
    db.add(payment)
    db.flush()

    attempt1 = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        idempotency_key="unique_idem_key_999",
        gateway_name="Stripe",
        status="FAILED",
    )
    db.add(attempt1)
    db.commit()

    # Attempt to insert identical idempotency key
    attempt2 = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=2,
        idempotency_key="unique_idem_key_999",
        gateway_name="Stripe",
        status="FAILED",
    )
    db.add(attempt2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_recovery_action_idempotency_uniqueness(db: Session):
    """Verify duplicate idempotency keys on recovery actions raise IntegrityError."""
    merchant = Merchant(name="M2", slug="m2")
    db.add(merchant)
    db.flush()

    customer = Customer(merchant_id=merchant.id, external_id="c2", email="c2@m.com", name="C2")
    db.add(customer)
    db.flush()

    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("100.00"))
    db.add(order)
    db.flush()

    payment = Payment(merchant_id=merchant.id, order_id=order.id, customer_id=customer.id, amount=Decimal("100.00"), status="FAILED")
    db.add(payment)
    db.flush()

    attempt = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        idempotency_key="idem_att_2",
        gateway_name="Adyen",
        status="FAILED",
    )
    db.add(attempt)
    db.flush()

    failure = PaymentFailure(
        payment_id=payment.id,
        payment_attempt_id=attempt.id,
        failure_code="BANK_TIMEOUT",
        raw_message="Timeout",
        is_retryable=True,
    )
    db.add(failure)
    db.flush()

    opp = RecoveryOpportunity(
        merchant_id=merchant.id,
        payment_id=payment.id,
        failure_id=failure.id,
        strategy_name="SMART_RETRY",
        confidence_score=0.9,
        estimated_recoverable_amount=Decimal("100.00"),
    )
    db.add(opp)
    db.flush()

    action1 = RecoveryAction(
        opportunity_id=opp.id,
        action_type=ActionType.SMART_RETRY.value,
        idempotency_key="idem_rec_action_abc",
        execution_status=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
    )
    db.add(action1)
    db.commit()

    action2 = RecoveryAction(
        opportunity_id=opp.id,
        action_type=ActionType.ROUTING_FALLBACK.value,
        idempotency_key="idem_rec_action_abc",
        execution_status=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
    )
    db.add(action2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_financial_action_stage1_safety_invariant(db: Session):
    """Verify that financial recovery actions cannot execute in Stage 1 and default to BLOCKED_STAGE1_SAFETY."""
    merchant = Merchant(name="M3", slug="m3")
    db.add(merchant)
    db.flush()

    customer = Customer(merchant_id=merchant.id, external_id="c3", email="c3@m.com", name="C3")
    db.add(customer)
    db.flush()

    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("75.00"))
    db.add(order)
    db.flush()

    payment = Payment(merchant_id=merchant.id, order_id=order.id, customer_id=customer.id, amount=Decimal("75.00"), status="FAILED")
    db.add(payment)
    db.flush()

    attempt = PaymentAttempt(payment_id=payment.id, attempt_number=1, idempotency_key="idem_att_3", gateway_name="Stripe", status="FAILED")
    db.add(attempt)
    db.flush()

    failure = PaymentFailure(payment_id=payment.id, payment_attempt_id=attempt.id, failure_code="LIMIT_EXCEEDED", raw_message="Limit", is_retryable=True)
    db.add(failure)
    db.flush()

    opp = RecoveryOpportunity(
        merchant_id=merchant.id, payment_id=payment.id, failure_id=failure.id,
        strategy_name="NEXT_DAY_RETRY", confidence_score=0.7, estimated_recoverable_amount=Decimal("75.00")
    )
    db.add(opp)
    db.flush()

    action = RecoveryAction(
        opportunity_id=opp.id,
        action_type=ActionType.SMART_RETRY.value,
        idempotency_key="idem_rec_act_check",
    )
    db.add(action)
    db.commit()

    # Ensure default status is strictly BLOCKED_STAGE1_SAFETY
    assert action.execution_status == ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value
    assert action.executed_at is None


def test_action_executor_rejects_empty_idempotency_key():
    """Verify ActionExecutor enforces idempotency key length and validity."""
    from services.action_layer.executor import ActionExecutor, InvalidIdempotencyKeyError
    executor = ActionExecutor(stage_1_safety_lock=True)
    with pytest.raises(InvalidIdempotencyKeyError):
        executor.validate_idempotency_key("")
    with pytest.raises(InvalidIdempotencyKeyError):
        executor.validate_idempotency_key("short")


def test_action_executor_blocks_financial_execution_at_stage_1():
    """Verify ActionExecutor strictly prevents any financial action from executing at Stage 1."""
    from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
    executor = ActionExecutor(stage_1_safety_lock=True)
    with pytest.raises(FinancialExecutionBlockedError) as exc_info:
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="SMART_RETRY",
            idempotency_key="idem_valid_key_12345",
        )
    assert "BLOCKED_STAGE1_SAFETY" in str(exc_info.value)

