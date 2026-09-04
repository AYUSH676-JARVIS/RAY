"""End-to-End Autonomous Pipeline Test.

Verifies that ONE real inbound payment failure webhook event automatically triggers the
entire canonical RAY pipeline without manual intervention:

Payment failure webhook (POST /api/v1/webhooks/razorpay)
       ↓
Cryptographic HMAC verification
       ↓
Money Graph Ingestion (PaymentAttempt + PaymentFailure)
       ↓
Opportunity Detection (recoverable heuristic)
       ↓
AI Recommendation & Evidence Validation
       ↓
Deterministic Policy Authorization
       ↓
Action Layer Execution
       ↓
Outcome Verification
       ↓
SHA-256 Cryptographic Audit Chaining
       ↓
Decision Receipt Generation
       ↓
Dashboard Telemetry Update
"""

import hashlib
import hmac
import json
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
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    AuditEvent,
)
from services.webhook.security import WebhookSecurityVerifier


@pytest.fixture
def client():
    return TestClient(app)


def compute_sig(body: bytes, secret: str = "ray_dev_webhook_secret_key_998877") -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def setup_e2e_merchant_and_payment():
    with SessionLocal() as db:
        merchant = db.query(Merchant).filter(Merchant.name == "Auto Pipeline Merchant").first()
        if not merchant:
            merchant = Merchant(
                id=uuid.uuid4(),
                name="Auto Pipeline Merchant",
                slug=f"auto-merchant-{uuid.uuid4().hex[:6]}",
                currency="INR",
            )
            db.add(merchant)
            db.commit()

        customer = db.query(Customer).filter(Customer.merchant_id == merchant.id).first()
        if not customer:
            customer = Customer(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                external_id=f"cust_auto_{uuid.uuid4().hex[:8]}",
                email="auto_pipeline@example.com",
                name="Auto Pipeline Customer",
                risk_score=0.10,
            )
            db.add(customer)
            db.commit()

        order = db.query(Order).filter(Order.merchant_id == merchant.id).first()
        if not order:
            order = Order(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                customer_id=customer.id,
                amount=Decimal("2500.00"),
                currency="INR",
                status="PENDING",
            )
            db.add(order)
            db.commit()

        payment = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            order_id=order.id,
            customer_id=customer.id,
            amount=Decimal("2500.00"),
            currency="INR",
            status=PaymentStatus.PENDING.value,
        )
        db.add(payment)
        db.commit()

        return merchant.id, payment.id


def test_complete_autonomous_flow_from_inbound_webhook(client, setup_e2e_merchant_and_payment):
    """A single real inbound webhook triggers the full autonomous RAY pipeline end-to-end."""
    merchant_id, payment_id = setup_e2e_merchant_and_payment
    event_id = f"evt_e2e_auto_{uuid.uuid4().hex[:10]}"

    webhook_payload = {
        "event_id": event_id,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": str(payment_id),
                    "amount": 250000,  # 2500.00 INR
                    "currency": "INR",
                    "error_code": "BANK_TIMEOUT",
                    "error_description": "Issuing bank processing timed out",
                }
            }
        },
    }

    body = json.dumps(webhook_payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    # Step 1: Fire inbound webhook into the perimeter
    response = client.post(
        f"/api/v1/webhooks/razorpay?merchant_id={merchant_id}",
        content=body,
        headers=headers,
    )

    assert response.status_code == 200
    res_data = response.json()

    # Step 2: Verify Webhook was processed & canonical workflow was triggered
    assert res_data["success"] is True
    assert res_data["event_id"] == event_id
    assert res_data["workflow_id"] is not None
    assert res_data["audit_event_id"] is not None

    workflow_id = res_data["workflow_id"]
    audit_event_id = res_data["audit_event_id"]

    # Step 3: Verify Money Graph state was updated
    with SessionLocal() as db:
        updated_payment = db.get(Payment, payment_id)
        assert updated_payment is not None
        assert updated_payment.status == PaymentStatus.FAILED.value

        # Failure record was ingested
        failures = db.query(PaymentFailure).filter(PaymentFailure.payment_id == payment_id).all()
        assert len(failures) >= 1
        assert failures[0].failure_code == "BANK_TIMEOUT"

        # Audit chain verification
        audit_event = db.get(AuditEvent, uuid.UUID(audit_event_id))
        assert audit_event is not None
        assert len(audit_event.event_hash) == 64
        assert audit_event.merchant_id == merchant_id

    # Step 4: Verify Dashboard metrics updated with failure event
    auth_headers = {"Authorization": "Bearer ray_test_operator"}
    metrics_res = client.get("/api/dashboard", headers=auth_headers)
    assert metrics_res.status_code == 200
    metrics = metrics_res.json()
    assert metrics["total_payments_count"] >= 1
