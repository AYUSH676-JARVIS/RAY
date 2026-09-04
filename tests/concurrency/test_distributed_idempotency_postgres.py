"""Multi-Worker & Distributed Idempotency Tests.

Verifies:
1. Concurrency coordination relies on durable PostgreSQL state, not ephemeral RAM.
2. In-flight execution slots block concurrent attempts across separate sessions.
3. Cache wipe (simulating process restart) cleanly recovers prior receipts from the database ledger.
4. Exact-once guarantee is preserved under duplicate requests.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest

from services.action_layer.idempotency import IdempotencyManager, ConcurrentExecutionBlockedError
from services.money_graph.database import SessionLocal
from services.money_graph.models import (
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    RecoveryOpportunity,
    RecoveryAction,
    ActionType,
)


@pytest.fixture
def test_merchant_id():
    m_id = uuid.uuid4()
    with SessionLocal() as s:
        m = Merchant(
            id=m_id,
            name="Distributed Idem Merchant",
            slug=f"dist-idem-{uuid.uuid4().hex[:6]}",
            currency="USD",
            status="ACTIVE",
        )
        s.add(m)
        s.commit()
    return m_id


def create_recovery_action_record(session, merchant_id: uuid.UUID, idempotency_key: str, status: str = "SUCCEEDED"):
    cust = Customer(
        merchant_id=merchant_id,
        external_id=f"cust_{uuid.uuid4().hex[:6]}",
        email="idem@test.internal",
        name="Idempotency Test Customer",
    )
    session.add(cust)
    session.flush()

    order = Order(merchant_id=merchant_id, customer_id=cust.id, amount=Decimal("100.00"), currency="USD")
    session.add(order)
    session.flush()

    p = Payment(merchant_id=merchant_id, order_id=order.id, customer_id=cust.id, amount=Decimal("100.00"), status="FAILED")
    session.add(p)
    session.flush()

    att = PaymentAttempt(
        payment_id=p.id,
        merchant_id=merchant_id,
        attempt_number=1,
        gateway_name="Simulation",
        status="FAILED",
        idempotency_key=f"att_idem_{uuid.uuid4().hex[:8]}",
    )
    session.add(att)
    session.flush()

    failure = PaymentFailure(
        payment_id=p.id,
        payment_attempt_id=att.id,
        failure_code="BANK_TIMEOUT",
        raw_message="Bank timed out",
        is_retryable=True,
    )
    session.add(failure)
    session.flush()

    opp = RecoveryOpportunity(
        merchant_id=merchant_id,
        payment_id=p.id,
        failure_id=failure.id,
        strategy_name="SMART_RETRY",
        confidence_score=0.95,
        estimated_recoverable_amount=Decimal("100.00"),
    )
    session.add(opp)
    session.flush()

    action = RecoveryAction(
        merchant_id=merchant_id,
        opportunity_id=opp.id,
        action_type=ActionType.SMART_RETRY.value,
        idempotency_key=idempotency_key,
        execution_status=status,
    )
    session.add(action)
    session.commit()
    return action


def test_durable_idempotency_survives_process_restart(test_merchant_id):
    """Assert that wiping in-memory cache still returns cached receipt from PostgreSQL ledger."""
    mgr = IdempotencyManager.get_instance()
    mgr.clear_in_memory_cache()

    idem_key = f"idem_crash_{uuid.uuid4().hex[:8]}"

    # Step 1: Record an executed action in PostgreSQL
    with SessionLocal() as s:
        create_recovery_action_record(s, test_merchant_id, idempotency_key=idem_key, status="SUCCEEDED")

    # Step 2: Simulate complete container / worker process restart
    mgr.clear_in_memory_cache()

    # Step 3: Attempt to acquire execution slot in a new worker process with a fresh session
    with SessionLocal() as s2:
        is_new, receipt = mgr.acquire_execution_slot(
            merchant_id=test_merchant_id,
            idempotency_key=idem_key,
            session=s2,
        )

        assert is_new is False, "Expected replay of durable record, but treated as new execution!"
        assert receipt is not None
        assert receipt.status == "SUCCEEDED"
        assert receipt.response_payload["source"] == "database_durable_ledger"


def test_concurrent_in_flight_action_blocked(test_merchant_id):
    """Assert that an in-flight REQUESTED or PROCESSING action in PostgreSQL blocks concurrent execution."""
    mgr = IdempotencyManager.get_instance()
    mgr.clear_in_memory_cache()

    idem_key = f"idem_inflight_{uuid.uuid4().hex[:8]}"

    # Insert an in-flight action into PostgreSQL
    with SessionLocal() as s:
        create_recovery_action_record(s, test_merchant_id, idempotency_key=idem_key, status="PROCESSING")

    # Simulate another worker pod attempting to acquire the same idempotency slot
    mgr.clear_in_memory_cache()
    with SessionLocal() as s2:
        with pytest.raises(ConcurrentExecutionBlockedError) as exc_info:
            mgr.acquire_execution_slot(
                merchant_id=test_merchant_id,
                idempotency_key=idem_key,
                session=s2,
            )
        assert "Concurrent execution in-flight" in str(exc_info.value)
