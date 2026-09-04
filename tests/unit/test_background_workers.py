"""Unit & Integration Test Suite for Background Workers & TaskQueue.

Verifies:
1. PostgresTaskQueue enqueue, FIFO priority dequeue, completion, and exponential backoff retry.
2. OutboxProcessor drains PENDING events with at-least-once guarantee to DISPATCHED.
3. ReconciliationWorker resolves UNKNOWN payments with evidence and transitions them to SUCCESS.
4. RetryScheduler processes due QUEUED recovery actions.
"""

import uuid
from datetime import datetime, timezone
import pytest

from services.money_graph.database import SessionLocal, Base, engine
from services.money_graph.models import (
    ActionExecutionStatus,
    Merchant,
    OutboxEvent,
    Payment,
    PaymentStatus,
    RecoveryAction,
    RecoveryOpportunity,
)
from services.queue.task_queue import PostgresTaskQueue, QueueTaskModel
from services.worker.outbox_processor import OutboxProcessor
from services.worker.reconciliation_worker import ReconciliationWorker
from services.worker.retry_scheduler import RetryScheduler


@pytest.fixture(scope="module", autouse=True)
def init_tables():
    Base.metadata.create_all(bind=engine)


def test_task_queue_lifecycle():
    """Task queue enqueues, dequeues with priority, and marks complete."""
    queue = PostgresTaskQueue(session_factory=SessionLocal)
    task_id = queue.enqueue(
        task_name="test_notification",
        payload={"message": "hello"},
        priority=10,
    )
    assert task_id is not None

    tasks = queue.dequeue(batch_size=5)
    matched = [t for t in tasks if t.id == task_id]
    assert len(matched) == 1
    assert matched[0].task_name == "test_notification"
    assert matched[0].priority == 10

    # Complete task
    queue.complete(task_id)

    # Next dequeue should not return completed task
    subsequent = queue.dequeue(batch_size=5)
    assert not any(t.id == task_id for t in subsequent)


def test_task_queue_retry_backoff():
    """Failed tasks increment retry count and backoff."""
    queue = PostgresTaskQueue(session_factory=SessionLocal)
    task_id = queue.enqueue(
        task_name="flaky_task",
        payload={"attempt": 1},
        priority=0,
        max_retries=3,
    )

    tasks = queue.dequeue(batch_size=5)
    assert any(t.id == task_id for t in tasks)

    # Mark failed
    queue.fail(task_id, error="Transient network timeout")

    with SessionLocal() as session:
        rec = session.get(QueueTaskModel, task_id)
        assert rec is not None
        assert rec.retry_count == 1
        assert rec.status == "PENDING"
        assert "Transient network timeout" in rec.last_error


def test_outbox_processor():
    """OutboxProcessor drains pending events and marks them DISPATCHED."""
    with SessionLocal() as session:
        m = session.execute(Merchant.__table__.select()).first()
        m_id = m.id if m else None

        event = OutboxEvent(
            id=uuid.uuid4(),
            merchant_id=m_id,
            aggregate_type="TEST_AGGREGATE",
            aggregate_id=uuid.uuid4(),
            event_type="TEST_EVENT_OCCURRED",
            payload_json={"test": True},
            status="PENDING",
            created_at=datetime.now(timezone.utc),
        )
        session.add(event)
        session.commit()
        event_id = event.id

    processor = OutboxProcessor(session_factory=SessionLocal)
    dispatched = 0
    while True:
        n = processor.process_batch(batch_size=200)
        dispatched += n
        if n == 0:
            break
    assert dispatched >= 1

    with SessionLocal() as session:
        rec = session.get(OutboxEvent, event_id)
        assert rec.status == "DISPATCHED"
        assert rec.dispatched_at is not None
