"""Cryptographically Chained Tamper-Evident Audit Logging Engine.

Principles:
- Audit records everything.
- Every audit event is linked to its predecessor via SHA-256 hash chaining:
    event_hash = SHA256(prev_hash : merchant_id : sequence_number : event_type : actor_id : canonical_payload)
- Any modification, deletion, or reordering breaks the chain and is detected by verify_audit_chain().
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session
from services.money_graph.models import AuditEvent, ActorType


class AuditLogger:
    """Provides cryptographically chained, tamper-evident audit logging."""

    @staticmethod
    def _compute_hash(
        prev_hash: Optional[str],
        merchant_id: uuid.UUID,
        sequence_number: int,
        event_type: str,
        actor_id: str,
        payload_after: Optional[Dict[str, Any]],
    ) -> str:
        """Compute SHA-256 digest over canonicalized event components."""
        canonical_json = json.dumps(payload_after or {}, sort_keys=True)
        hash_input = (
            f"{prev_hash or 'GENESIS'}:"
            f"{merchant_id}:"
            f"{sequence_number}:"
            f"{event_type}:"
            f"{actor_id}:"
            f"{canonical_json}"
        )
        return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    @classmethod
    def record_event(
        cls,
        session: Session,
        merchant_id: uuid.UUID,
        entity_type: str,
        entity_id: Optional[uuid.UUID],
        event_type: str,
        actor_type: ActorType,
        actor_id: str,
        payload_before: Optional[Dict[str, Any]] = None,
        payload_after: Optional[Dict[str, Any]] = None,
        run_id: Optional[uuid.UUID] = None,
    ) -> AuditEvent:
        """Create, chain, and persist an immutable AuditEvent."""
        # Find latest event for this merchant
        last_event = (
            session.execute(
                select(AuditEvent)
                .where(AuditEvent.merchant_id == merchant_id)
                .order_by(AuditEvent.sequence_number.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

        sequence_number = (last_event.sequence_number + 1) if last_event else 1
        previous_hash = last_event.event_hash if last_event else None

        event_hash = cls._compute_hash(
            prev_hash=previous_hash,
            merchant_id=merchant_id,
            sequence_number=sequence_number,
            event_type=event_type,
            actor_id=actor_id,
            payload_after=payload_after,
        )

        event = AuditEvent(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            sequence_number=sequence_number,
            run_id=run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            actor_type=actor_type.value if isinstance(actor_type, ActorType) else actor_type,
            actor_id=actor_id,
            payload_before_json=payload_before,
            payload_after_json=payload_after,
            previous_event_hash=previous_hash,
            event_hash=event_hash,
            timestamp=datetime.now(timezone.utc),
        )
        session.add(event)
        session.flush()
        return event

    @classmethod
    def verify_audit_chain(cls, session: Session, merchant_id: uuid.UUID) -> Tuple[bool, Optional[str]]:
        """Verify the cryptographic integrity of a merchant's audit chain.
        
        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        events = (
            session.execute(
                select(AuditEvent)
                .where(AuditEvent.merchant_id == merchant_id)
                .order_by(AuditEvent.sequence_number.asc())
            )
            .scalars()
            .all()
        )

        if not events:
            return True, None

        expected_prev_hash: Optional[str] = None

        for idx, event in enumerate(events, start=1):
            # 1. Sequence number continuity check
            if event.sequence_number != idx:
                return (
                    False,
                    f"Audit chain break: Expected sequence number {idx}, found {event.sequence_number} (possible deletion or insertion).",
                )

            # 2. Previous hash linkage check
            if event.previous_event_hash != expected_prev_hash:
                return (
                    False,
                    f"Audit chain break at sequence {idx}: previous_hash mismatch. Expected '{expected_prev_hash}', got '{event.previous_event_hash}'.",
                )

            # 3. Cryptographic digest verification
            recomputed_hash = cls._compute_hash(
                prev_hash=expected_prev_hash,
                merchant_id=merchant_id,
                sequence_number=event.sequence_number,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload_after=event.payload_after_json,
            )

            if event.event_hash != recomputed_hash:
                return (
                    False,
                    f"Audit tampering detected at sequence {idx} (Event ID: {event.id}): "
                    f"Stored hash '{event.event_hash}' does not match recomputed hash '{recomputed_hash}'.",
                )

            expected_prev_hash = event.event_hash

        return True, None


verify_audit_chain = AuditLogger.verify_audit_chain
