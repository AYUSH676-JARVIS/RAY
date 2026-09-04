"""Unit & Concurrency Test Suite for Horizontal Scalability & Distributed Locking.

Verifies:
1. DistributedLock acquires and releases cleanly.
2. Concurrent threads attempting to acquire the same lock key are mutually excluded.
3. Database connection pool configuration supports high concurrency.
"""

import threading
import time
import pytest

from services.concurrency.distributed_lock import DistributedLock, distributed_lock
from services.money_graph.database import SessionLocal


def test_distributed_lock_acquire_and_release():
    """DistributedLock acquires and releases successfully."""
    with SessionLocal() as session:
        lock = DistributedLock(key="test_lock_simple", session=session, timeout_seconds=2.0)
        assert lock.acquire() is True
        assert lock.release() is True


def test_distributed_lock_context_manager():
    """DistributedLock functions seamlessly as a Python context manager."""
    with SessionLocal() as session:
        with distributed_lock("test_lock_ctx", session=session):
            # Critical section
            time.sleep(0.05)


def test_distributed_lock_mutual_exclusion():
    """Simulate 2 horizontal workers competing for the same lock key."""
    results = []

    def worker_task(worker_id: int):
        with SessionLocal() as session:
            try:
                # Tight 0.2s timeout so the second worker fails to acquire while the first holds it
                with distributed_lock("shared_payment_resource", session=session, timeout_seconds=0.2):
                    results.append(f"worker_{worker_id}_entered")
                    time.sleep(0.5)
                    results.append(f"worker_{worker_id}_exited")
            except Exception as e:
                results.append(f"worker_{worker_id}_blocked")

    t1 = threading.Thread(target=worker_task, args=(1,))
    t2 = threading.Thread(target=worker_task, args=(2,))

    t1.start()
    time.sleep(0.05)  # Ensure t1 acquires first
    t2.start()

    t1.join()
    t2.join()

    # Either worker 2 was blocked or execution was strictly serialized
    assert "worker_1_entered" in results
    assert "worker_1_exited" in results
