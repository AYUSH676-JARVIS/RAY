"""Unit & Financial Invariant Tests for Phase 2 Reconciliation Hardening.

Verifies:
1. UNKNOWN payments NEVER automatically become SUCCESS without authoritative gateway confirmation.
2. UNKNOWN -> SUCCESS when gateway confirms capture/success and all financial fields match.
3. UNKNOWN -> FAILED when gateway returns definitive decline.
4. UNKNOWN remains UNKNOWN on gateway timeout, network drop, or ambiguous response.
5. Amount, currency, merchant ID, and transaction ID mismatches are strictly rejected.
6. Repeated reconciliation runs are idempotent.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pytest

from services.action_layer.gateway import GatewayResult, GatewayStatus, SimulationGateway
from services.money_graph.database import SessionLocal
from services.money_graph.models import Customer, Merchant, Order, Payment, PaymentAttempt, PaymentStatus
from services.worker.reconciliation_worker import ReconciliationWorker


class MockAuthoritativeGateway(SimulationGateway):
    """Configurable gateway adapter for authoritative reconciliation testing."""

    def __init__(
        self,
        status: str = GatewayStatus.SUCCEEDED.value,
        raw_code: str = "captured",
        tx_id_override: str = None,
        merchant_id_override: str = None,
        payload_override: dict = None,
        raise_timeout: bool = False,
    ):
        super().__init__()
        self.gateway_name = "MockAuthoritative"
        self.target_status = status
        self.raw_code = raw_code
        self.tx_id_override = tx_id_override
        self.merchant_id_override = merchant_id_override
        self.payload_override = payload_override
        self.raise_timeout = raise_timeout

    def query_status(self, transaction_id: str, merchant_id: str = None, correlation_id: str = None) -> GatewayResult:
        if self.raise_timeout:
            raise TimeoutError("Simulated gateway network socket timeout")

        tx_id = self.tx_id_override or transaction_id
        mid = self.merchant_id_override or merchant_id
        payload = self.payload_override or {"amount": "100.00", "currency": "USD", "status": self.raw_code}

        return GatewayResult(
            gateway_name=self.gateway_name,
            transaction_id=tx_id,
            status=self.target_status,
            raw_code=self.raw_code,
            raw_message="Mock authoritative gateway response",
            latency_ms=50,
            is_retryable=False,
            merchant_id=mid,
            payload=payload,
        )


@pytest.fixture
def recon_db_fixture():
    session = SessionLocal()
    m = Merchant(
        id=uuid.uuid4(),
        name="Recon Test Merchant",
        slug=f"recon-test-{uuid.uuid4().hex[:6]}",
        currency="USD",
        status="ACTIVE",
    )
    cust = Customer(
        id=uuid.uuid4(),
        merchant_id=m.id,
        external_id=f"cust_{uuid.uuid4().hex[:6]}",
        email="recon@test.internal",
        name="Recon Test Customer",
    )
    order = Order(
        id=uuid.uuid4(),
        merchant_id=m.id,
        customer_id=cust.id,
        amount=Decimal("100.00"),
        currency="USD",
        status="PENDING",
    )
    session.add_all([m, cust, order])
    session.commit()

    yield session, m, cust, order

    session.close()


def create_unknown_payment(session, merchant, customer, order, tx_id="tx_gw_123456"):
    p = Payment(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        customer_id=customer.id,
        order_id=order.id,
        amount=Decimal("100.00"),
        currency="USD",
        status=PaymentStatus.UNKNOWN.value,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    session.add(p)
    session.flush()

    att = PaymentAttempt(
        id=uuid.uuid4(),
        payment_id=p.id,
        merchant_id=merchant.id,
        attempt_number=1,
        gateway_name="MockAuthoritative",
        gateway_transaction_id=tx_id,
        status="UNKNOWN",
        idempotency_key=f"idem_{uuid.uuid4().hex[:8]}",
    )
    session.add(att)
    session.commit()
    return p, att


def test_reconcile_unknown_to_success(recon_db_fixture):
    """Assert UNKNOWN transitions to SUCCESS when gateway authoritatively confirms."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_success_1")

    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        payload_override={"amount": "100.00", "currency": "USD", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    updated_att = session.query(PaymentAttempt).filter(PaymentAttempt.id == att.id).first()
    assert reconciled == 1
    assert updated_p.status == PaymentStatus.SUCCESS.value
    assert updated_att.status == "SUCCESS"


def test_reconcile_unknown_to_failed(recon_db_fixture):
    """Assert UNKNOWN transitions to FAILED when gateway authoritatively declines."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_declined_1")

    gw = MockAuthoritativeGateway(
        status=GatewayStatus.FAILED.value,
        raw_code="card_declined",
        payload_override={"amount": "100.00", "currency": "USD", "status": "failed"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    updated_att = session.query(PaymentAttempt).filter(PaymentAttempt.id == att.id).first()
    assert reconciled == 1
    assert updated_p.status == PaymentStatus.FAILED.value
    assert updated_att.status == "FAILED"


def test_reconcile_unknown_remains_unknown_when_gateway_unresolved(recon_db_fixture):
    """Assert UNKNOWN remains UNKNOWN when gateway cannot determine state."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_ambiguous_1")

    gw = MockAuthoritativeGateway(status=GatewayStatus.UNKNOWN.value)
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconcile_unknown_remains_unknown_on_gateway_timeout(recon_db_fixture):
    """Assert UNKNOWN remains UNKNOWN on gateway timeout/network drop."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_timeout_1")

    gw = MockAuthoritativeGateway(raise_timeout=True)
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconcile_rejects_amount_mismatch(recon_db_fixture):
    """Assert reconciliation rejects transition if amount does not match."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_amt_mismatch")

    # Gateway reports 50.00 instead of 100.00
    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        payload_override={"amount": "50.00", "currency": "USD", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconcile_rejects_currency_mismatch(recon_db_fixture):
    """Assert reconciliation rejects transition if currency does not match."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_curr_mismatch")

    # Gateway reports EUR instead of USD
    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        payload_override={"amount": "100.00", "currency": "EUR", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconcile_rejects_cross_merchant_mismatch(recon_db_fixture):
    """Assert reconciliation rejects transition if merchant identity does not match."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_merchant_mismatch")

    # Gateway reports different merchant
    fake_merchant = str(uuid.uuid4())
    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        merchant_id_override=fake_merchant,
        payload_override={"amount": "100.00", "currency": "USD", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconcile_rejects_tx_id_mismatch(recon_db_fixture):
    """Assert reconciliation rejects transition if transaction ID does not match attempt."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_expected_123")

    # Gateway returns a different transaction ID
    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        tx_id_override="tx_unexpected_999",
        payload_override={"amount": "100.00", "currency": "USD", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    reconciled = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)

    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert reconciled == 0
    assert updated_p.status == PaymentStatus.UNKNOWN.value


def test_reconciliation_is_idempotent(recon_db_fixture):
    """Assert running reconciliation repeatedly does not cause duplicate processing."""
    session, m, cust, order = recon_db_fixture
    p, att = create_unknown_payment(session, m, cust, order, tx_id="tx_idem_1")

    gw = MockAuthoritativeGateway(
        status=GatewayStatus.SUCCEEDED.value,
        payload_override={"amount": "100.00", "currency": "USD", "status": "captured"},
    )
    worker = ReconciliationWorker(session_factory=SessionLocal, gateway_adapter=gw)
    
    # Run 1: resolves payment
    first_run = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)
    assert first_run == 1
    updated_p = session.query(Payment).filter(Payment.id == p.id).first()
    assert updated_p.status == PaymentStatus.SUCCESS.value

    # Run 2: already settled; 0 processed
    second_run = worker.reconcile_pending_unknowns(cooldown_seconds=0, merchant_id=m.id)
    assert second_run == 0
    updated_p2 = session.query(Payment).filter(Payment.id == p.id).first()
    assert updated_p2.status == PaymentStatus.SUCCESS.value
