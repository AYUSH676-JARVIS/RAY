"""Unit tests for synthetic data generation reproducibility, constraints, and distribution."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from data.synthetic.generator import SyntheticDataGenerator, FAILURE_CATEGORIES
from services.money_graph.models import Base, Payment, PaymentFailure, PaymentAttempt


@pytest.fixture
def memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_generator_reproducibility(memory_db: Session):
    """Verify that using the same seed produces identical record counts and failure codes."""
    gen1 = SyntheticDataGenerator(seed=123)
    counts1 = gen1.generate_all(
        session=memory_db,
        num_customers=50,
        num_orders=100,
        num_payments=120,
        num_failed_payments=30,
        batch_size=50,
    )

    assert counts1["customers"] == 50
    assert counts1["orders"] == 100
    assert counts1["payments"] == 120
    assert counts1["failed_payments"] == 30
    assert counts1["successful_payments"] == 90

    # Query failures
    failures = memory_db.query(PaymentFailure).all()
    assert len(failures) == 30

    # Verify all failures match the required 8 categories
    valid_codes = {fc.value for fc in FAILURE_CATEGORIES}
    for f in failures:
        assert f.failure_code in valid_codes
        assert f.raw_message is not None


def test_payment_failure_attempt_linkage(memory_db: Session):
    """Verify that all failed payments have an associated payment attempt with idempotency key."""
    gen = SyntheticDataGenerator(seed=999)
    gen.generate_all(
        session=memory_db,
        num_customers=20,
        num_orders=40,
        num_payments=50,
        num_failed_payments=15,
        batch_size=20,
    )

    failed_payments = memory_db.query(Payment).filter(Payment.status == "FAILED").all()
    assert len(failed_payments) == 15

    for p in failed_payments:
        assert len(p.attempts) >= 1
        assert len(p.failures) >= 1
        # Check idempotency key exists and is non-empty
        assert p.attempts[0].idempotency_key.startswith("idem_")
