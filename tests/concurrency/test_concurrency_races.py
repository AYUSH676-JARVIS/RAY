"""Concurrency Race & Idempotency Test Suite.

Verifies:
1. Concurrent duplicate requests with the same idempotency key are serialized.
2. Exactly ONE execution takes place.
3. All concurrent callers receive the identical authoritative execution receipt.
4. Zero double-charging cardholders.
"""

from __future__ import annotations

import concurrent.futures
import decimal
import threading
import time
import uuid
import pytest

from services.action_layer.executor import ActionExecutor
from services.action_layer.gateway import GatewayResult, GatewayStatus, PaymentGateway, SimulationGateway
from services.action_layer.idempotency import IdempotencyManager


class CountingMockGateway(SimulationGateway):
    """Gateway that tracks invocation count to prove exact-once execution."""

    def __init__(self):
        super().__init__()
        self.invocation_count = 0
        self.lock = threading.Lock()

    def execute_retry(self, amount, currency, idempotency_key, customer_id, metadata=None):
        with self.lock:
            self.invocation_count += 1
            # Simulate small network latency
            time.sleep(0.05)
            return GatewayResult(
                gateway_name="CountingMockGateway",
                transaction_id=f"tx_count_{self.invocation_count}",
                status=GatewayStatus.SUCCEEDED,
                raw_code="00",
                raw_message="Approved",
                latency_ms=50,
            )

    def query_status(self, transaction_id):
        return GatewayResult(
            gateway_name="CountingMockGateway",
            transaction_id=transaction_id,
            status=GatewayStatus.SUCCEEDED,
            raw_code="00",
            raw_message="Approved",
            latency_ms=20,
        )


def test_concurrent_duplicate_requests_execute_exactly_once():
    """20 threads firing the exact same idempotency key simultaneously execute exactly ONCE."""
    gateway = CountingMockGateway()
    manager = IdempotencyManager()
    executor = ActionExecutor(
        stage_1_safety_lock=False,  # Unlocked to test execution concurrency
        gateway=gateway,
        idempotency_manager=manager,
    )

    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    shared_key = f"idem_race_{uuid.uuid4().hex}"

    def worker():
        return executor.execute_recovery_action(
            action_id=action_id,
            action_type="SMART_RETRY",
            idempotency_key=shared_key,
            merchant_id=merchant_id,
            amount=decimal.Decimal("150.00"),
            currency="USD",
        )

    num_threads = 20
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    # Invariant 1: Exactly 1 execution occurred
    assert gateway.invocation_count == 1, f"Expected exactly 1 execution, got {gateway.invocation_count}!"

    # Invariant 2: All 20 threads received a valid receipt
    assert len(results) == num_threads
    for r in results:
        assert r["status"] == "SUCCEEDED"
        assert r["action_id"] == str(action_id)

    # Invariant 3: Exactly 19 returned with idempotent_replay: True
    replays = [r for r in results if r.get("idempotent_replay") is True]
    assert len(replays) == num_threads - 1, f"Expected {num_threads - 1} replays, got {len(replays)}"
