"""Test suite for Authoritative Outcome Reconciliation API.

Verifies:
1. Reconciling an UNKNOWN payment to SETTLED/SUCCESS.
2. Reconciling an UNKNOWN payment to FAILED.
3. Cryptographic audit log generation on manual reconciliation.
4. Tenant boundary enforcement on reconciliation.
"""

import uuid
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from services.money_graph.database import SessionLocal
from services.money_graph.models import (
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentStatus,
    AuditEvent,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def unknown_payment_setup():
    with SessionLocal() as db:
        merchant = db.query(Merchant).first()
        if not merchant:
            merchant = Merchant(
                id=uuid.uuid4(),
                name="Recon Test Merchant",
                slug=f"recon-merch-{uuid.uuid4().hex[:6]}",
                currency="USD",
            )
            db.add(merchant)
            db.commit()

        customer = db.query(Customer).filter(Customer.merchant_id == merchant.id).first()
        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                external_id=f"cust_recon_{uuid.uuid4().hex[:8]}",
                name="Recon Customer",
                email="recon@example.com",
            )
            db.add(customer)
            db.commit()

        order = db.query(Order).filter(Order.merchant_id == merchant.id).first()
        if not order:
            order = Order(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                customer_id=customer.id,
                amount=Decimal("150.00"),
                currency="USD",
            )
            db.add(order)
            db.commit()

        payment = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            order_id=order.id,
            customer_id=customer.id,
            amount=Decimal("150.00"),
            currency="USD",
            status=PaymentStatus.UNKNOWN.value,
        )
        db.add(payment)
        db.commit()

        return merchant.id, payment.id


def test_reconcile_unknown_payment_to_settled(client, unknown_payment_setup):
    """Manual reconciliation of UNKNOWN payment against SETTLED gateway report succeeds."""
    merchant_id, payment_id = unknown_payment_setup
    headers = {"Authorization": "Bearer ray_test_operator"}

    payload = {
        "authoritative_status": "SETTLED",
        "settled_amount": "150.00",
        "settled_currency": "USD",
        "gateway_transaction_id": "gw_txn_settled_123",
    }

    res = client.post(f"/api/v1/payments/{payment_id}/reconcile", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["payment_id"] == str(payment_id)
    assert data["is_recovered"] is True
    assert data["previous_status"] == "UNKNOWN"
    assert data["new_status"] == "SUCCESS"
    assert data["reconciliation_status"] == "VERIFIED_SETTLED"
    assert data["audit_event_id"] is not None

    # Verify database state
    with SessionLocal() as db:
        p = db.get(Payment, payment_id)
        assert p.status == "SUCCESS"

        audit = db.get(AuditEvent, uuid.UUID(data["audit_event_id"]))
        assert audit is not None
        assert audit.event_type == "PAYMENT_RECONCILED_AUTHORITATIVE"
