"""100-Thread Adversarial Concurrency & Race-Condition Audit (Phase 9).

Verifies:
1. 100 concurrent threads firing identical idempotency key -> exactly 1 gateway invocation, 99 replays.
2. 100 concurrent threads writing to transactional outbox -> 100 distinct records, 0 deadlocks.
3. 100 concurrent webhook delivery submissions of the exact same event -> exactly 1 accepted/processed, 99 harmless DUPLICATE responses.
4. 100 concurrent policy evaluations -> deterministic, zero race conditions.
"""

import concurrent.futures
import decimal
import hashlib
import hmac
import json
import threading
import time
import uuid
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from services.action_layer.executor import ActionExecutor
from services.action_layer.gateway import GatewayResult, GatewayStatus, PaymentGateway, SimulationGateway
from services.action_layer.idempotency import IdempotencyManager
from services.money_graph.database import SessionLocal
from services.money_graph.models import Customer, Merchant, Order, Payment, PaymentStatus
from services.outbox.service import OutboxService
from services.policy_engine.engine import DeterministicPolicyEngine
from services.money_graph.models import PolicyDecisionType


class CountingGateway(SimulationGateway):
    def __init__(self):
        super().__init__()
        self.count = 0
        self._lock = threading.Lock()

    def execute_retry(self, amount, currency, idempotency_key, customer_id, metadata=None):
        with self._lock:
            self.count += 1
            time.sleep(0.002)  # Simulate small I/O delay
            return GatewayResult(
                gateway_name="CountingGateway",
                transaction_id=f"tx_count_{self.count}",
                status=GatewayStatus.SUCCEEDED.value,
                raw_code="00",
                raw_message="Approved",
                latency_ms=5,
            )

    def query_status(self, transaction_id, merchant_id=None, correlation_id=None):
        return GatewayResult(
            gateway_name="CountingGateway",
            transaction_id=transaction_id,
            status=GatewayStatus.SUCCEEDED.value,
            raw_code="00",
            raw_message="Approved",
            latency_ms=5,
        )


def test_100_thread_concurrent_idempotency_claims():
    """100 concurrent threads firing identical idempotency key execute exactly once."""
    gw = CountingGateway()
    mgr = IdempotencyManager()
    executor = ActionExecutor(
        stage_1_safety_lock=False,
        gateway=gw,
        idempotency_manager=mgr,
    )

    action_id = uuid.uuid4()
    merchant_id = uuid.uuid4()
    key = f"idem_100_attack_{uuid.uuid4().hex}"

    def attack():
        return executor.execute_recovery_action(
            action_id=action_id,
            action_type="SMART_RETRY",
            idempotency_key=key,
            merchant_id=merchant_id,
            amount=decimal.Decimal("250.00"),
            currency="USD",
        )

    num_threads = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(attack) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    assert gw.count == 1, f"Expected exactly 1 execution, got {gw.count}!"
    assert len(results) == 100

    replays = [r for r in results if r.get("idempotent_replay") is True]
    assert len(replays) == 99


def test_100_thread_concurrent_outbox_writes():
    """100 concurrent threads writing to transactional outbox commit without deadlock."""
    with SessionLocal() as db:
        m = db.query(Merchant).first()
        mid = m.id

    def write_outbox(idx: int):
        with SessionLocal() as db:
            evt = OutboxService.record_event(
                session=db,
                aggregate_type="TRANSACTION",
                aggregate_id=uuid.uuid4(),
                event_type="TX_COMMITTED",
                payload={"index": idx},
                merchant_id=mid,
            )
            db.commit()
            return evt.id

    num_threads = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(write_outbox, i) for i in range(num_threads)]
        event_ids = [f.result() for f in futures]

    assert len(event_ids) == 100
    assert len(set(event_ids)) == 100


def test_100_thread_concurrent_webhook_delivery_replay_attack():
    """100 concurrent requests sending the identical webhook event are deduplicated safely."""
    client = TestClient(app)

    # Setup merchant and payment
    with SessionLocal() as db:
        m = db.query(Merchant).first()
        c = db.query(Customer).filter(Customer.merchant_id == m.id).first()
        o = db.query(Order).filter(Order.merchant_id == m.id).first()
        p = Payment(
            id=uuid.uuid4(),
            merchant_id=m.id,
            customer_id=c.id,
            order_id=o.id,
            amount=decimal.Decimal("99.00"),
            currency="USD",
            status=PaymentStatus.PENDING.value,
        )
        db.add(p)
        db.commit()
        pid = str(p.id)
        mid = str(m.id)

    event_id = f"evt_100_attack_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.captured",
        "data": {"payment_id": pid, "amount": "99.00", "currency": "USD"},
    }
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(b"ray_dev_webhook_secret_key_998877", body, hashlib.sha256).hexdigest()
    headers = {"X-Razorpay-Signature": sig}

    def post_webhook():
        return client.post(f"/api/webhooks/simulation?merchant_id={mid}", content=body, headers=headers)

    num_threads = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(post_webhook) for _ in range(num_threads)]
        responses = [f.result() for f in futures]

    statuses = [r.status_code for r in responses]
    assert all(s == 200 for s in statuses)

    results = [r.json() for r in responses]
    processed = [r for r in results if r.get("status") == "PROCESSED"]
    duplicates = [r for r in results if r.get("status") == "DUPLICATE"]

    # Exactly 1 processed, 99 duplicates
    assert len(processed) == 1
    assert len(duplicates) == 99


def test_100_thread_concurrent_policy_evaluations():
    """100 concurrent threads evaluating deterministic policy are thread-safe with 0 race conditions."""
    engine = DeterministicPolicyEngine()

    def eval_policy(idx: int):
        is_fraud = (idx % 2 == 0)
        code = "FRAUD_SUSPECTED" if is_fraud else "BANK_TIMEOUT"
        risk = 0.9 if is_fraud else 0.1
        decision, rule, _ = engine.authorize_recovery(
            failure_code=code,
            attempt_count=1,
            customer_risk_score=risk,
            amount=decimal.Decimal("50.00"),
        )
        return decision

    num_threads = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(eval_policy, i) for i in range(num_threads)]
        results = [f.result() for f in futures]

    assert len(results) == 100
    # Even indices should be REJECTED (fraud), odd indices should be APPROVED
    for i, dec in enumerate(results):
        if i % 2 == 0:
            assert dec == PolicyDecisionType.REJECTED
        else:
            assert dec == PolicyDecisionType.APPROVED
