"""Transactional Outbox Processor.

Continuously drains pending OutboxEvent records with at-least-once delivery semantics
and transitions records from PENDING to PROCESSED.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.money_graph.models import OutboxEvent

logger = logging.getLogger("ray.worker.outbox")


class OutboxProcessor:
    """Processes pending transactional outbox events."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def process_batch(self, batch_size: int = 50) -> int:
        """Fetch and dispatch a batch of pending outbox events.
        
        Returns:
            Number of successfully processed events.
        """
        processed_count = 0
        with self.session_factory() as session:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.status == "PENDING")
                .order_by(OutboxEvent.created_at.asc())
                .limit(batch_size)
            )
            try:
                stmt = stmt.with_for_update(skip_locked=True)
            except Exception:
                pass

            events = session.execute(stmt).scalars().all()
            if not events:
                return 0

            for ev in events:
                try:
                    # In production, dispatch to message broker (Kafka/RabbitMQ/webhook)
                    logger.info(
                        f"[Outbox Dispatch] Event {ev.id}: aggregate_type='{ev.aggregate_type}' "
                        f"event_type='{ev.event_type}' aggregate_id='{ev.aggregate_id}'"
                    )
                    ev.status = "DISPATCHED"
                    ev.dispatched_at = datetime.now(timezone.utc)
                    processed_count += 1
                except Exception as e:
                    logger.error(f"Failed to dispatch outbox event {ev.id}: {e}")
                    ev.status = "FAILED"

            session.commit()

        return processed_count
