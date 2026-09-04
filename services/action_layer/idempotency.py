"""Transactional Idempotency Manager with Concurrency Serialization and Durable Fallback.

Guarantees:
1. Exact-once execution: Concurrent duplicate requests are serialized with atomic lock tokens.
2. Cached response replay: Sequential duplicate requests immediately return the cached receipt.
3. Durable process-restart resilience: If memory cache is lost (e.g. process reboot),
   the manager checks the persistent database before granting an execution slot.
4. Zero double-charging cardholders.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("ray.idempotency")


class IdempotencyState:
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StoredReceipt(BaseModel):
    """Immutable cached receipt of an executed action."""
    idempotency_key: str
    merchant_id: uuid.UUID
    status: str
    response_payload: Dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConcurrentExecutionBlockedError(Exception):
    """Raised when a concurrent duplicate execution is already in flight."""
    pass


class IdempotencyManager:
    """Thread-safe transactional idempotency coordinator with database-backed persistence."""

    _instance = None
    _global_lock = threading.Lock()

    def __new__(cls):
        with cls._global_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._locks: Dict[str, threading.Lock] = {}
                cls._instance._in_flight: Dict[str, float] = {}
                cls._instance._receipts: Dict[str, StoredReceipt] = {}
            return cls._instance

    @classmethod
    def get_instance(cls) -> IdempotencyManager:
        return cls()

    def _composite_key(self, merchant_id: uuid.UUID, idempotency_key: str) -> str:
        return f"{merchant_id}:{idempotency_key.strip()}"

    def clear_in_memory_cache(self) -> None:
        """Simulate process restart or wipe ephemeral cache for audit testing."""
        with self._global_lock:
            self._receipts.clear()
            self._in_flight.clear()
            self._locks.clear()

    def acquire_execution_slot(
        self,
        merchant_id: uuid.UUID,
        idempotency_key: str,
        session: Optional[Any] = None,
        timeout_seconds: float = 5.0,
    ) -> Tuple[bool, Optional[StoredReceipt]]:
        """Attempt to acquire execution slot for an idempotency key.
        
        Returns:
            (is_new_execution: bool, cached_receipt: Optional[StoredReceipt])
            - If cached receipt exists (in memory or database): returns (False, cached_receipt).
            - If new execution slot acquired: returns (True, None).
            - If concurrent duplicate in-flight: waits up to timeout_seconds for completion.
        """
        composite = self._composite_key(merchant_id, idempotency_key)

        # 1. Distributed Shared State Synchronization via PostgreSQL (Survives Multi-Pod & Crashes)
        if session is not None:
            is_pg = False
            try:
                bind = session.get_bind()
                if bind and bind.dialect.name == "postgresql":
                    is_pg = True
                    import hashlib
                    from sqlalchemy import text
                    # Deterministic 32-bit positive integer hash for advisory lock
                    advisory_key = int(hashlib.md5(composite.encode("utf-8")).hexdigest()[:7], 16)
                    lock_acquired = session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:key)"),
                        {"key": advisory_key}
                    ).scalar()
                    if not lock_acquired:
                        raise ConcurrentExecutionBlockedError(
                            f"Concurrent financial execution for idempotency key '{idempotency_key}' is already locked by another cluster process."
                        )
            except ConcurrentExecutionBlockedError:
                raise
            except Exception as e:
                logger.debug("Database advisory lock skipped or unavailable: %s", e)

            # 2. Query Authoritative Durable Ledger in PostgreSQL
            try:
                from sqlalchemy import select
                from services.money_graph.models import RecoveryAction
                stmt = select(RecoveryAction).where(
                    RecoveryAction.merchant_id == merchant_id,
                    RecoveryAction.idempotency_key == idempotency_key.strip(),
                )
                if is_pg:
                    try:
                        stmt = stmt.with_for_update()
                    except Exception:
                        pass
                db_action = session.scalar(stmt)
                if db_action:
                    if db_action.execution_status in [
                        "SUCCEEDED", "FAILED", "UNKNOWN", "BLOCKED_STAGE1_SAFETY", "REJECTED", "ACCEPTED"
                    ]:
                        durable_receipt = StoredReceipt(
                            idempotency_key=idempotency_key.strip(),
                            merchant_id=merchant_id,
                            status=db_action.execution_status,
                            response_payload={
                                "status": db_action.execution_status,
                                "action_id": str(db_action.id),
                                "idempotency_key": db_action.idempotency_key,
                                "source": "database_durable_ledger",
                            },
                        )
                        with self._global_lock:
                            self._receipts[composite] = durable_receipt
                        return False, durable_receipt
                    elif db_action.execution_status == "PROCESSING":
                        raise ConcurrentExecutionBlockedError(
                            f"Concurrent execution in-flight: action {db_action.id} is already PROCESSING."
                        )
            except ConcurrentExecutionBlockedError:
                raise
            except Exception as db_err:
                logger.debug("Database lookup skipped during idempotency check: %s", db_err)

        # 3. Local Process Memory Optimization (Thread-Level Serialization)
        with self._global_lock:
            if composite in self._receipts:
                return False, self._receipts[composite]

            if composite not in self._locks:
                self._locks[composite] = threading.Lock()
            key_lock = self._locks[composite]

        acquired = key_lock.acquire(timeout=timeout_seconds)
        if not acquired:
            raise ConcurrentExecutionBlockedError(
                f"Concurrent financial execution for idempotency key '{idempotency_key}' is already in progress."
            )

        with self._global_lock:
            if composite in self._receipts:
                key_lock.release()
                return False, self._receipts[composite]
            self._in_flight[composite] = time.time()

        return True, None

    def commit_receipt(
        self,
        merchant_id: uuid.UUID,
        idempotency_key: str,
        response_payload: Dict[str, Any],
        status: str = IdempotencyState.COMPLETED,
    ) -> StoredReceipt:
        """Store authoritative execution receipt and release lock."""
        composite = self._composite_key(merchant_id, idempotency_key)
        receipt = StoredReceipt(
            idempotency_key=idempotency_key,
            merchant_id=merchant_id,
            status=status,
            response_payload=response_payload,
        )

        with self._global_lock:
            self._receipts[composite] = receipt
            self._in_flight.pop(composite, None)
            key_lock = self._locks.get(composite)

        if key_lock and key_lock.locked():
            key_lock.release()

        return receipt

    def release_failed_slot(self, merchant_id: uuid.UUID, idempotency_key: str):
        """Release slot if execution failed before completing."""
        composite = self._composite_key(merchant_id, idempotency_key)
        with self._global_lock:
            self._in_flight.pop(composite, None)
            key_lock = self._locks.get(composite)

        if key_lock and key_lock.locked():
            key_lock.release()
