"""Unit tests verifying foreign key integrity and bidirectional relationship graphs."""

from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.money_graph.models import (
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    RecoveryOpportunity,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_payment_relationship_graph(db: Session):
    """Verify complete bidirectional traversal of the Money Graph."""
    # 1. Merchant
    merchant = Merchant(name="Nexus Global", slug="nexus-global")
    db.add(merchant)
    db.flush()

    # 2. Customer
    customer = Customer(merchant_id=merchant.id, external_id="cust_rel_1", email="rel@nexus.com", name="Rel Tester")
    db.add(customer)
    db.flush()

    # 3. Order
    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("249.99"))
    db.add(order)
    db.flush()

    # 4. Payment
    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("249.99"),
        status="FAILED",
    )
    db.add(payment)
    db.flush()

    # 5. PaymentAttempt
    attempt = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        idempotency_key="idem_rel_attempt_1",
        gateway_name="Stripe",
        status="FAILED",
    )
    db.add(attempt)
    db.flush()

    # 6. PaymentFailure
    failure = PaymentFailure(
        payment_id=payment.id,
        payment_attempt_id=attempt.id,
        failure_code="ISSUER_DECLINED",
        raw_message="Do not honor",
        is_retryable=True,
    )
    db.add(failure)
    db.flush()

    # 7. RecoveryOpportunity
    opp = RecoveryOpportunity(
        merchant_id=merchant.id,
        payment_id=payment.id,
        failure_id=failure.id,
        strategy_name="ROUTING_CASCADE",
        confidence_score=0.82,
        estimated_recoverable_amount=Decimal("249.99"),
    )
    db.add(opp)
    db.commit()

    # Refresh payment from database
    db.expire_all()
    queried_payment = db.get(Payment, payment.id)

    # Test forward traversal
    assert queried_payment.customer.name == "Rel Tester"
    assert queried_payment.order.amount == Decimal("249.99")
    assert len(queried_payment.attempts) == 1
    assert queried_payment.attempts[0].idempotency_key == "idem_rel_attempt_1"
    assert len(queried_payment.failures) == 1
    assert queried_payment.failures[0].failure_code == "ISSUER_DECLINED"
    assert len(queried_payment.opportunities) == 1
    assert queried_payment.opportunities[0].strategy_name == "ROUTING_CASCADE"

    # Test reverse traversal
    assert failure.payment.id == payment.id
    assert failure.attempt.id == attempt.id
    assert opp.failure.failure_code == "ISSUER_DECLINED"
    assert customer.payments[0].id == payment.id
    assert order.payments[0].id == payment.id
