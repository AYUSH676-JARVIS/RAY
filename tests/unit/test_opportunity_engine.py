"""Unit tests for the Financial Opportunity Engine covering all 11 required core scenarios."""

import uuid
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, sessionmaker

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
)
from services.money_graph.service import MoneyGraphService
from services.opportunities.engine import FinancialOpportunityEngine, detect_recovery_opportunity
from services.opportunities.failure_intelligence import CandidateStrategy


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


def _setup_payment(
    db: Session,
    amount: Decimal = Decimal("150.00"),
    status: str = "FAILED",
    failure_code: str = "BANK_TIMEOUT",
    retry_count: int = 1,
    customer_risk_score: float = 0.10,
    successful_prior_payments: int = 2,
) -> Payment:
    """Helper to create connected money graph records."""
    merchant = Merchant(name="Apex Store", slug=f"apex-{uuid.uuid4().hex[:6]}")
    db.add(merchant)
    db.flush()

    customer = Customer(
        merchant_id=merchant.id,
        external_id=f"cust_{uuid.uuid4().hex[:6]}",
        email="test@example.com",
        name="Test Customer",
        risk_score=customer_risk_score,
    )
    db.add(customer)
    db.flush()

    # Prior payments for history if requested
    for i in range(successful_prior_payments):
        o_prior = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("100.00"))
        db.add(o_prior)
        db.flush()
        p_prior = Payment(
            merchant_id=merchant.id,
            order_id=o_prior.id,
            customer_id=customer.id,
            amount=Decimal("100.00"),
            status=PaymentStatus.SUCCESS.value,
        )
        db.add(p_prior)
        db.flush()

    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=amount)
    db.add(order)
    db.flush()

    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        customer_id=customer.id,
        amount=amount,
        status=status,
    )
    db.add(payment)
    db.flush()

    for attempt_num in range(1, retry_count + 1):
        attempt = PaymentAttempt(
            payment_id=payment.id,
            attempt_number=attempt_num,
            idempotency_key=f"idem_att_{payment.id}_{attempt_num}",
            gateway_name="Stripe",
            status="FAILED" if status == "FAILED" else "SUCCESS",
        )
        db.add(attempt)
        db.flush()

        if status == "FAILED":
            failure = PaymentFailure(
                payment_id=payment.id,
                payment_attempt_id=attempt.id,
                failure_code=failure_code,
                raw_message=f"Failure code {failure_code}",
                is_retryable=failure_code not in ["CARD_EXPIRED", "FRAUD_SUSPECTED"],
            )
            db.add(failure)
            db.flush()

    db.commit()
    return payment


# Scenario 1: Successful payment cannot create recovery opportunity
def test_successful_payment_cannot_create_recovery_opportunity(db: Session):
    payment = _setup_payment(db, status="SUCCESS")
    opp = detect_recovery_opportunity(payment.id, session=db)

    assert opp.is_eligible is False
    assert opp.recommended_strategy == CandidateStrategy.NO_ACTION.value
    assert opp.opportunity_score == 0.0
    assert "Only FAILED payments can generate recovery opportunities" in opp.explanation.reason


# Scenario 2: BANK_TIMEOUT creates a recoverable opportunity
def test_bank_timeout_creates_recoverable_opportunity(db: Session):
    payment = _setup_payment(db, failure_code="BANK_TIMEOUT")
    opp = detect_recovery_opportunity(payment.id, session=db)

    assert opp.is_eligible is True
    assert opp.failure_category == "BANK_TIMEOUT"
    assert opp.recommended_strategy in [CandidateStrategy.WAIT_AND_RETRY.value, CandidateStrategy.RETRY_NOW.value]
    assert opp.opportunity_score > 0.0
    assert opp.score_breakdown.success_probability >= 0.70
    assert opp.explanation.urgency == "HIGH"


# Scenario 3: CARD_EXPIRED does not recommend direct retry
def test_card_expired_does_not_recommend_direct_retry(db: Session):
    payment = _setup_payment(db, failure_code="CARD_EXPIRED")
    opp = detect_recovery_opportunity(payment.id, session=db)

    assert opp.is_eligible is True
    assert opp.recommended_strategy == CandidateStrategy.UPDATE_PAYMENT_METHOD.value
    assert CandidateStrategy.RETRY_NOW.value in opp.blocked_strategies
    assert CandidateStrategy.WAIT_AND_RETRY.value in opp.blocked_strategies
    assert "expired" in opp.explanation.reason.lower()


# Scenario 4: FRAUD_SUSPECTED does not permit autonomous recovery
def test_fraud_suspected_does_not_permit_autonomous_recovery(db: Session):
    payment = _setup_payment(db, failure_code="FRAUD_SUSPECTED")
    opp = detect_recovery_opportunity(payment.id, session=db)

    assert opp.is_eligible is True
    assert opp.recommended_strategy == CandidateStrategy.HUMAN_REVIEW.value
    assert CandidateStrategy.RETRY_NOW.value in opp.blocked_strategies
    assert CandidateStrategy.WAIT_AND_RETRY.value in opp.blocked_strategies
    assert CandidateStrategy.SEND_PAYMENT_LINK.value in opp.blocked_strategies
    assert opp.explanation.risk == "CRITICAL"


# Scenario 5: Multiple previous retries affect recommendation
def test_multiple_previous_retries_affect_recommendation(db: Session):
    # Payment with 3 prior attempts (reaching MAX_RETRIES_LIMIT)
    payment = _setup_payment(db, failure_code="INSUFFICIENT_FUNDS", retry_count=3)
    opp = detect_recovery_opportunity(payment.id, session=db)

    # When retry count reaches 3, direct retries must be blocked in favor of outreach
    assert CandidateStrategy.RETRY_NOW.value in opp.blocked_strategies
    assert CandidateStrategy.WAIT_AND_RETRY.value in opp.blocked_strategies
    assert opp.recommended_strategy == CandidateStrategy.SEND_PAYMENT_LINK.value
    assert "limit" in opp.explanation.reason.lower()


# Scenario 6: Customer history affects expected recovery
def test_customer_history_affects_expected_recovery(db: Session):
    # Customer A: established with 5 prior successes
    p_good = _setup_payment(db, amount=Decimal("200.00"), failure_code="BANK_TIMEOUT", successful_prior_payments=5)
    opp_good = detect_recovery_opportunity(p_good.id, session=db)

    # Customer B: brand new with 0 prior successes and high risk score
    p_bad = _setup_payment(
        db,
        amount=Decimal("200.00"),
        failure_code="BANK_TIMEOUT",
        successful_prior_payments=0,
        customer_risk_score=0.75,
    )
    opp_bad = detect_recovery_opportunity(p_bad.id, session=db)

    assert opp_good.score_breakdown.success_probability > opp_bad.score_breakdown.success_probability
    assert opp_good.score_breakdown.expected_value > opp_bad.score_breakdown.expected_value
    assert opp_good.opportunity_score > opp_bad.opportunity_score


# Scenario 7: Opportunity score is deterministic
def test_opportunity_score_is_deterministic(db: Session):
    payment = _setup_payment(db, amount=Decimal("350.00"), failure_code="BANK_TIMEOUT")

    opp1 = detect_recovery_opportunity(payment.id, session=db)
    opp2 = detect_recovery_opportunity(payment.id, session=db)

    assert opp1.opportunity_score == opp2.opportunity_score
    assert opp1.score_breakdown.raw_score == opp2.score_breakdown.raw_score
    assert opp1.score_breakdown.expected_value == opp2.score_breakdown.expected_value
    assert opp1.score_breakdown.success_probability == opp2.score_breakdown.success_probability
    assert opp1.recommended_strategy == opp2.recommended_strategy


# Scenario 8: Evidence references actual payment/customer/failure data
def test_evidence_references_actual_money_graph_data(db: Session):
    payment = _setup_payment(
        db,
        amount=Decimal("412.50"),
        failure_code="BANK_TIMEOUT",
        customer_risk_score=0.142,
        successful_prior_payments=3,
    )
    opp = detect_recovery_opportunity(payment.id, session=db)

    evidence_str = " ".join(opp.explanation.evidence)
    assert "failure_category=BANK_TIMEOUT" in evidence_str
    assert "payment_amount=412.50 USD" in evidence_str
    assert "customer_risk_score=0.142" in evidence_str
    assert "previous_successful_payments=3" in evidence_str


# Scenario 9: Unknown data does not cause fabricated values
def test_unknown_failure_handled_without_fabrication(db: Session):
    payment = _setup_payment(db, failure_code="NON_EXISTENT_CUSTOM_CODE")
    opp = detect_recovery_opportunity(payment.id, session=db)

    assert opp.failure_category == "NON_EXISTENT_CUSTOM_CODE"
    assert opp.recommended_strategy == CandidateStrategy.HUMAN_REVIEW.value
    assert "Unrecognized failure code" in opp.explanation.reason


# Scenario 10: Dynamic opportunity detection does not mutate financial state
def test_opportunity_detection_does_not_mutate_financial_state(db: Session):
    payment = _setup_payment(db, amount=Decimal("180.00"), status="FAILED", failure_code="BANK_TIMEOUT")

    # Capture state before detection
    status_before = payment.status
    amount_before = payment.amount
    attempts_count_before = db.scalar(select(func.count(PaymentAttempt.id)).where(PaymentAttempt.payment_id == payment.id))
    payments_count_before = db.scalar(select(func.count(Payment.id)))

    # Execute dynamic detection
    opp = detect_recovery_opportunity(payment.id, session=db)
    assert opp.is_financial_action_executed is False

    # Verify state after detection remains perfectly identical
    db.expire_all()
    queried_payment = db.get(Payment, payment.id)
    attempts_count_after = db.scalar(select(func.count(PaymentAttempt.id)).where(PaymentAttempt.payment_id == payment.id))
    payments_count_after = db.scalar(select(func.count(Payment.id)))

    assert queried_payment.status == status_before
    assert queried_payment.amount == amount_before
    assert attempts_count_after == attempts_count_before
    assert payments_count_after == payments_count_before


# Scenario 11: Existing Stage 1 execution lock remains intact
def test_stage_1_safety_lock_remains_intact():
    from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
    executor = ActionExecutor(stage_1_safety_lock=True)
    with pytest.raises(FinancialExecutionBlockedError):
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="SMART_RETRY",
            idempotency_key="idem_test_lock_check",
        )
