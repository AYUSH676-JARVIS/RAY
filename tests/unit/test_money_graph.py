"""Unit tests for the Money Graph Service."""

import uuid
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
    PaymentStatus,
)
from services.money_graph.service import MoneyGraphService


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


def test_money_graph_full_context_retrieval(db: Session):
    """Verify MoneyGraphService reconstructs complete connected context."""
    merchant = Merchant(name="Apex", slug="apex-mg")
    db.add(merchant)
    db.flush()

    customer = Customer(
        merchant_id=merchant.id,
        external_id="ext_mg_1",
        email="mg1@apex.com",
        name="Alice Graph",
        risk_score=0.15,
    )
    db.add(customer)
    db.flush()

    order = Order(
        merchant_id=merchant.id,
        customer_id=customer.id,
        amount=Decimal("199.99"),
        currency="USD",
    )
    db.add(order)
    db.flush()

    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("199.99"),
        status=PaymentStatus.FAILED.value,
    )
    db.add(payment)
    db.flush()

    attempt = PaymentAttempt(
        payment_id=payment.id,
        attempt_number=1,
        idempotency_key="idem_test_mg_1",
        gateway_name="Stripe",
        status="FAILED",
        latency_ms=1500,
    )
    db.add(attempt)
    db.flush()

    failure = PaymentFailure(
        payment_id=payment.id,
        payment_attempt_id=attempt.id,
        failure_code="BANK_TIMEOUT",
        raw_message="Gateway timeout",
        is_retryable=True,
    )
    db.add(failure)
    db.commit()

    service = MoneyGraphService(session=db)
    ctx = service.get_full_money_context(payment.id)

    assert ctx is not None
    assert ctx.payment.id == payment.id
    assert ctx.payment.amount == Decimal("199.99")
    assert ctx.payment.status == "FAILED"
    assert ctx.customer.name == "Alice Graph"
    assert ctx.customer.risk_score == 0.15
    assert ctx.order.id == order.id
    assert ctx.failure is not None
    assert ctx.failure.failure_code == "BANK_TIMEOUT"
    assert ctx.failure.is_retryable is True
    assert len(ctx.attempts) == 1
    assert ctx.attempts[0].idempotency_key == "idem_test_mg_1"


def test_money_graph_non_fabrication_on_missing_entities(db: Session):
    """Verify missing failure records or entities return None, never fabricated values."""
    merchant = Merchant(name="Apex", slug="apex-nf")
    db.add(merchant)
    db.flush()

    customer = Customer(
        merchant_id=merchant.id,
        external_id="ext_nf_1",
        email="nf@apex.com",
        name="Bob NoFailure",
    )
    db.add(customer)
    db.flush()

    order = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("50.00"))
    db.add(order)
    db.flush()

    # Successful payment has NO failure record
    payment = Payment(
        merchant_id=merchant.id,
        order_id=order.id,
        customer_id=customer.id,
        amount=Decimal("50.00"),
        status=PaymentStatus.SUCCESS.value,
    )
    db.add(payment)
    db.commit()

    service = MoneyGraphService(session=db)
    ctx = service.get_full_money_context(payment.id)

    assert ctx is not None
    # Failure context MUST be None — not fabricated!
    assert ctx.failure is None
    assert len(ctx.attempts) == 0


def test_customer_payment_history_metrics(db: Session):
    """Verify aggregation of customer payment history and retry success rates."""
    merchant = Merchant(name="Apex", slug="apex-hist")
    db.add(merchant)
    db.flush()

    customer = Customer(merchant_id=merchant.id, external_id="ext_h", email="h@apex.com", name="Charlie Hist")
    db.add(customer)
    db.flush()

    order1 = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("100.00"))
    order2 = Order(merchant_id=merchant.id, customer_id=customer.id, amount=Decimal("200.00"))
    db.add_all([order1, order2])
    db.flush()

    p1 = Payment(merchant_id=merchant.id, order_id=order1.id, customer_id=customer.id, amount=Decimal("100.00"), status="SUCCESS")
    p2 = Payment(merchant_id=merchant.id, order_id=order2.id, customer_id=customer.id, amount=Decimal("200.00"), status="FAILED")
    db.add_all([p1, p2])
    db.commit()

    service = MoneyGraphService(session=db)
    history = service.get_customer_payment_history(customer.id)

    assert history is not None
    assert history.total_payments == 2
    assert history.total_successful == 1
    assert history.total_failed == 1
    assert history.average_ticket_size == Decimal("150.00")
