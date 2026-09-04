"""Integration tests verifying the complete Razorpay idempotency chain."""

from decimal import Decimal
import uuid
import threading
from unittest.mock import MagicMock
import pytest

from services.action_layer.executor import ActionExecutor
from services.action_layer.idempotency import IdempotencyManager
from services.action_layer.gateway import RazorpayGateway, GatewayStatus
from services.money_graph.models import PaymentStatus


class CountingRazorpayGateway(RazorpayGateway):
    """Subclass of RazorpayGateway that tracks external retry calls."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.call_count = 0
        self.lock = threading.Lock()

    def execute_retry(self, amount, currency, idempotency_key, customer_id, metadata=None):
        with self.lock:
            self.call_count += 1
            return super().execute_retry(amount, currency, idempotency_key, customer_id, metadata)


def test_idempotency_chain_blocks_duplicate_gateway_calls():
    """Identical idempotency key produces exactly ONE gateway call."""
    gw = CountingRazorpayGateway(
        key_id="rzp_test_dummy_key_1234",
        key_secret="dummy_secret",
        stage_1_safety_lock=True,
    )
    idem_mgr = IdempotencyManager()
    executor = ActionExecutor(stage_1_safety_lock=False, gateway=gw, idempotency_manager=idem_mgr)

    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    key = f"idem_test_{uuid.uuid4().hex[:12]}"

    # First attempt
    res1 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("199.99"),
        currency="USD",
        customer_id="cust_123",
        payment_status=PaymentStatus.FAILED.value,
    )
    assert res1.get("idempotent_replay") is not True

    # Second attempt with identical idempotency key
    res2 = executor.execute_recovery_action(
        action_id=action_id,
        action_type="RETRY",
        idempotency_key=key,
        merchant_id=merchant_id,
        amount=Decimal("199.99"),
        currency="USD",
        customer_id="cust_123",
        payment_status=PaymentStatus.FAILED.value,
    )
    assert res2.get("idempotent_replay") is True
    # Gateway was invoked only once
    assert gw.call_count == 1


def test_concurrent_duplicate_idempotency_claims():
    """Multiple concurrent workers sharing idempotency manager execute exactly once."""
    gw = CountingRazorpayGateway(
        key_id="rzp_test_dummy_key_1234",
        key_secret="dummy_secret",
        stage_1_safety_lock=True,
    )
    idem_mgr = IdempotencyManager()
    executor = ActionExecutor(stage_1_safety_lock=False, gateway=gw, idempotency_manager=idem_mgr)

    merchant_id = uuid.uuid4()
    key = f"idem_concurrent_{uuid.uuid4().hex[:12]}"
    results = []
    errors = []

    def worker():
        try:
            res = executor.execute_recovery_action(
                action_id=uuid.uuid4(),
                action_type="RETRY",
                idempotency_key=key,
                merchant_id=merchant_id,
                amount=Decimal("50.00"),
                currency="USD",
                customer_id="cust_multi",
                payment_status=PaymentStatus.FAILED.value,
            )
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10
    # Exactly one executed original, 9 idempotent replays
    assert gw.call_count == 1
    replays = [r for r in results if r.get("idempotent_replay") is True]
    assert len(replays) == 9
