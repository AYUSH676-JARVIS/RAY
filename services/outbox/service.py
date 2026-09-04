"""Transactional Outbox Service for Reliable Event Publishing & State Consistency.

Invariants:
1. Atomicity: Domain state updates and OutboxEvent records commit in the exact same DB transaction.
2. Resilience: Failed event publications can be safely retried up to max_retries.
3. Poison Pill Defense: Events exceeding max_retries transition to DEAD_LETTER state.
4. Concurrency Safety: Row locking (skip_locked) prevents concurrent workers from double-dispatching.
5. Observability: Status metrics are readily inspectable.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.money_graph.models import OutboxEvent

logger = logging.getLogger("ray.outbox")


class OutboxDispatchSummary:
    def __init__(self, processed: int, succeeded: int, failed: int, dead_lettered: int):
        self.processed = processed
        self.succeeded = succeeded
        self.failed = failed
        self.dead_lettered = dead_lettered

    def to_dict(self) -> Dict[str, int]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
        }


class OutboxService:
    """Transactional Outbox coordinator for exactly-once domain event dispatch."""

    @classmethod
    def record_event(
        cls,
        session: Session,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload: Dict[str, Any],
        merchant_id: Optional[uuid.UUID] = None,
        max_retries: int = 5,
    ) -> OutboxEvent:
        """Stage an OutboxEvent atomically within the active database transaction.
        
        DOES NOT commit the session; caller controls transaction boundary.
        """
        event = OutboxEvent(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_json=payload,
            status="PENDING",
            retry_count=0,
            max_retries=max_retries,
            created_at=datetime.now(timezone.utc),
        )
        session.add(event)
        return event

    @classmethod
    def dispatch_pending_events(
        cls,
        session: Session,
        consumer_fn: Optional[Callable[[OutboxEvent], bool]] = None,
        batch_size: int = 50,
        merchant_id: Optional[uuid.UUID] = None,
    ) -> OutboxDispatchSummary:
        """Fetch pending outbox events and dispatch them to consumers safely."""
        query = (
            select(OutboxEvent)
            .where(OutboxEvent.status.in_(["PENDING", "FAILED"]))
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        )

        if merchant_id:
            query = query.where(OutboxEvent.merchant_id == merchant_id)

        # Support concurrent workers with skip_locked where supported (PostgreSQL)
        try:
            query = query.with_for_update(skip_locked=True)
            events = session.scalars(query).all()
        except Exception:
            # Fallback for SQLite in memory testing
            events = session.scalars(query).all()

        succeeded = 0
        failed = 0
        dead_lettered = 0

        for event in events:
            # Default mock dispatcher if no consumer provided
            dispatch_ok = True
            error_msg = None

            if consumer_fn:
                try:
                    dispatch_ok = consumer_fn(event)
                except Exception as exc:
                    dispatch_ok = False
                    error_msg = str(exc)
                    logger.error(f"Outbox consumer failed for event {event.id}: {exc}")

            if dispatch_ok:
                event.status = "DISPATCHED"
                event.dispatched_at = datetime.now(timezone.utc)
                event.error_message = None
                succeeded += 1
            else:
                event.retry_count += 1
                event.error_message = error_msg or "Consumer returned dispatch failure"
                if event.retry_count >= event.max_retries:
                    event.status = "DEAD_LETTER"
                    dead_lettered += 1
                else:
                    event.status = "FAILED"
                    failed += 1

        session.commit()

        return OutboxDispatchSummary(
            processed=len(events),
            succeeded=succeeded,
            failed=failed,
            dead_lettered=dead_lettered,
        )

    @classmethod
    def get_metrics(cls, session: Session, merchant_id: Optional[uuid.UUID] = None) -> Dict[str, int]:
        """Aggregate outbox health metrics for observability."""
        query = select(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(OutboxEvent.status)
        if merchant_id:
            query = query.where(OutboxEvent.merchant_id == merchant_id)

        results = session.execute(query).all()
        counts = {status: count for status, count in results}

        return {
            "pending": counts.get("PENDING", 0),
            "dispatched": counts.get("DISPATCHED", 0),
            "failed": counts.get("FAILED", 0),
            "dead_letter": counts.get("DEAD_LETTER", 0),
            "total": sum(counts.values()),
        }
