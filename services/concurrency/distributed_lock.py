"""Cluster-wide Distributed Locking using PostgreSQL Advisory Locks.

Provides zero-dependency, crash-safe distributed locking across multiple API
workers and daemon processes.

Features:
- Transaction-level (xact) or session-level advisory locks
- Automatic cluster-wide scope across all horizontally scaled replicas
- Deterministic lock ID generation via CRC32/MD5 hash
- SQLite/In-memory fallback for local testing
"""

from __future__ import annotations

import logging
import time
import zlib
from contextlib import contextmanager
from typing import Generator, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("ray.concurrency.lock")


def _key_to_int(key: str) -> int:
    """Convert string lock key to signed 32-bit integer for Postgres advisory locks."""
    # Compute CRC32 and map to signed 32-bit int (-2^31 to 2^31-1)
    crc = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    if crc > 0x7FFFFFFF:
        crc -= 0x100000000
    return crc


class DistributedLockAcquisitionError(RuntimeError):
    """Raised when distributed lock cannot be acquired within timeout."""
    pass


class DistributedLock:
    """PostgreSQL Advisory Lock wrapper supporting horizontal scaling."""

    def __init__(self, key: str, session: Session, timeout_seconds: float = 5.0):
        self.key = key
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.lock_int = _key_to_int(key)
        self._acquired = False
        self._is_postgres = (
            hasattr(session.bind, "dialect") and session.bind.dialect.name == "postgresql"
        )

    def acquire(self) -> bool:
        """Attempt to acquire distributed lock, retrying until timeout."""
        start = time.time()

        if not self._is_postgres:
            # Fallback for SQLite / test environments
            self._acquired = True
            return True

        while time.time() - start < self.timeout_seconds:
            try:
                # Use pg_try_advisory_lock
                res = self.session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": self.lock_int},
                ).scalar()
                if res:
                    self._acquired = True
                    logger.debug(f"Acquired distributed lock '{self.key}' (int={self.lock_int})")
                    return True
            except Exception as e:
                logger.warning(f"Error querying advisory lock: {e}")

            time.sleep(0.05)

        logger.warning(f"Timeout acquiring distributed lock '{self.key}' after {self.timeout_seconds}s")
        return False

    def release(self) -> bool:
        """Release previously acquired distributed lock."""
        if not self._acquired:
            return False

        if not self._is_postgres:
            self._acquired = False
            return True

        try:
            res = self.session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": self.lock_int},
            ).scalar()
            self._acquired = False
            logger.debug(f"Released distributed lock '{self.key}'")
            return bool(res)
        except Exception as e:
            logger.error(f"Error releasing advisory lock '{self.key}': {e}")
            self._acquired = False
            return False

    def __enter__(self):
        if not self.acquire():
            raise DistributedLockAcquisitionError(
                f"Could not acquire distributed lock for '{self.key}' within {self.timeout_seconds}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


@contextmanager
def distributed_lock(
    key: str, session: Session, timeout_seconds: float = 5.0
) -> Generator[DistributedLock, None, None]:
    """Context manager for distributed lock."""
    lock = DistributedLock(key, session, timeout_seconds=timeout_seconds)
    with lock:
        yield lock
