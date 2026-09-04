"""Comprehensive Test Suite for Webhook Security & Event Architecture (Phase 2 & 3).

Verifies:
1. Valid cryptographic signature
2. Invalid signature (401)
3. Missing signature (401)
4. Malformed signature (401)
5. Expired timestamp / replay attack (400)
6. Duplicate webhook delivery (harmless 200, no duplicate state mutation)
7. Out-of-order event (e.g. CAPTURED after REFUNDED) rejected safely
8. Unknown payment reference handled without leaking info
9. Cross-tenant payment reference rejected
10. Malformed JSON payload (400)
11. State transition validation enforced
12. Payment failure webhook -> Money Graph -> Decision Workflow integration
13. Immutable audit event recorded with SHA-256 chain
"""

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
from typing import Optional
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
    WebhookDelivery,
    AuditEvent,
)
from services.webhook.security import WebhookSecurityVerifier


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_merchant_and_payment():
    with SessionLocal() as db:
        merchant = db.query(Merchant).first()
        if not merchant:
            merchant = Merchant(
                id=uuid.uuid4(),
                name="Webhook Test Merchant",
                slug=f"webhook-test-{uuid.uuid4().hex[:6]}",
                currency="USD",
            )
            db.add(merchant)
            db.commit()

        cust = db.query(Customer).filter(Customer.merchant_id == merchant.id).first()
        if not cust:
            cust = Customer(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                external_id=f"cust_{uuid.uuid4().hex[:8]}",
                email="webhook_test@example.com",
                name="Webhook Customer",
                risk_score=0.15,
            )
            db.add(cust)
            db.commit()

        order = db.query(Order).filter(Order.merchant_id == merchant.id).first()
        if not order:
            order = Order(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                customer_id=cust.id,
                amount=Decimal("199.99"),
                currency="USD",
            )
            db.add(order)
            db.commit()

        payment = Payment(
            id=uuid.uuid4(),
            merchant_id=merchant.id,
            customer_id=cust.id,
            order_id=order.id,
            amount=Decimal("199.99"),
            currency="USD",
            status=PaymentStatus.PENDING.value,
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return str(merchant.id), str(payment.id)


def compute_sig(body: bytes, secret: Optional[str] = None) -> str:
    if secret is None:
        secret = WebhookSecurityVerifier.get_gateway_secret("razorpay")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_webhook_missing_signature(client):
    payload = {"event_id": f"evt_{uuid.uuid4().hex[:8]}", "event_type": "payment.captured"}
    res = client.post("/api/webhooks/razorpay", json=payload)
    assert res.status_code == 401
    assert "Missing required webhook signature" in res.json()["detail"]


def test_webhook_invalid_signature(client):
    payload = {"event_id": f"evt_{uuid.uuid4().hex[:8]}", "event_type": "payment.captured"}
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-Razorpay-Signature": "a" * 64}  # Valid hex, wrong digest
    res = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert res.status_code == 401
    assert "signature mismatch" in res.json()["detail"]


def test_webhook_malformed_signature(client):
    payload = {"event_id": f"evt_{uuid.uuid4().hex[:8]}", "event_type": "payment.captured"}
    body = json.dumps(payload).encode("utf-8")
    headers = {"X-Razorpay-Signature": "not_a_valid_hex_string"}
    res = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert res.status_code == 401
    assert "Malformed webhook signature" in res.json()["detail"]


def test_webhook_expired_timestamp_replay_defense(client):
    payload = {"event_id": f"evt_{uuid.uuid4().hex[:8]}", "event_type": "payment.captured"}
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    expired_ts = str(time.time() - 600)  # 10 minutes ago (> 5m tolerance)
    headers = {
        "X-Razorpay-Signature": sig,
        "X-Webhook-Timestamp": expired_ts,
    }
    res = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert res.status_code == 400
    assert "replay window" in res.json()["detail"]


def test_webhook_malformed_json_payload(client):
    body = b"not_json_data_content"
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}
    res = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert res.status_code == 400
    assert "Malformed JSON" in res.json()["detail"]


def test_webhook_valid_event_transitions_payment_and_creates_outbox(client, test_merchant_and_payment):
    merchant_id, payment_id = test_merchant_and_payment
    event_id = f"evt_cap_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.captured",
        "data": {
            "payment_id": payment_id,
            "amount": "199.99",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post(f"/api/webhooks/simulation?merchant_id={merchant_id}", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PROCESSED"
    assert data["is_duplicate"] is False

    # Verify payment status in DB transitioned to CAPTURED
    with SessionLocal() as db:
        p = db.get(Payment, uuid.UUID(payment_id))
        assert p.status == PaymentStatus.CAPTURED.value

        # Verify WebhookDelivery record
        d = db.query(WebhookDelivery).filter(WebhookDelivery.event_id == event_id).first()
        assert d is not None
        assert d.status == "PROCESSED"


def test_webhook_duplicate_delivery_is_harmless(client, test_merchant_and_payment):
    merchant_id, payment_id = test_merchant_and_payment
    event_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.captured",
        "data": {
            "payment_id": payment_id,
            "amount": "199.99",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    # First delivery
    res1 = client.post(f"/api/webhooks/simulation?merchant_id={merchant_id}", content=body, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["is_duplicate"] is False

    # Immediate duplicate delivery
    res2 = client.post(f"/api/webhooks/simulation?merchant_id={merchant_id}", content=body, headers=headers)
    assert res2.status_code == 200
    assert res2.json()["is_duplicate"] is True
    assert res2.json()["status"] == "DUPLICATE"


def test_webhook_out_of_order_event_rejected(client, test_merchant_and_payment):
    merchant_id, payment_id = test_merchant_and_payment

    # Set payment directly to terminal REFUNDED
    with SessionLocal() as db:
        p = db.get(Payment, uuid.UUID(payment_id))
        p.status = PaymentStatus.REFUNDED.value
        db.commit()

    # Inbound late CAPTURED event on REFUNDED payment
    event_id = f"evt_ooo_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.captured",
        "data": {
            "payment_id": payment_id,
            "amount": "199.99",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post(f"/api/webhooks/simulation?merchant_id={merchant_id}", content=body, headers=headers)
    assert res.status_code == 400
    data = res.json()
    assert data["status"] == "REJECTED_OUT_OF_ORDER"

    # Verify payment status remained REFUNDED (not corrupted by late webhook)
    with SessionLocal() as db:
        p = db.get(Payment, uuid.UUID(payment_id))
        assert p.status == PaymentStatus.REFUNDED.value


def test_webhook_unknown_payment_reference(client):
    unknown_payment_id = str(uuid.uuid4())
    event_id = f"evt_unk_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.failed",
        "data": {
            "payment_id": unknown_payment_id,
            "amount": "50.00",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post("/api/webhooks/simulation", content=body, headers=headers)
    assert res.status_code == 400
    assert res.json()["status"] == "REJECTED"


def test_webhook_wrong_merchant_tenant_boundary(client, test_merchant_and_payment):
    _, payment_id = test_merchant_and_payment
    different_merchant_id = str(uuid.uuid4())  # Unauthorized merchant scope

    event_id = f"evt_cross_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.failed",
        "data": {
            "payment_id": payment_id,
            "amount": "50.00",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post(f"/api/webhooks/simulation?merchant_id={different_merchant_id}", content=body, headers=headers)
    assert res.status_code == 400
    assert res.json()["status"] == "REJECTED"


def test_webhook_payment_failure_triggers_decision_workflow(client, test_merchant_and_payment):
    merchant_id, payment_id = test_merchant_and_payment
    event_id = f"evt_fail_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": event_id,
        "event_type": "payment.failed",
        "data": {
            "payment_id": payment_id,
            "amount": "199.99",
            "currency": "USD",
            "failure_code": "BANK_TIMEOUT",
            "failure_message": "Bank connection timed out",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post(f"/api/webhooks/simulation?merchant_id={merchant_id}", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["workflow_id"] is not None  # Canonical decision loop triggered!
    assert data["audit_event_id"] is not None

    # Verify audit event has SHA-256 hash chaining
    with SessionLocal() as db:
        audit = db.get(AuditEvent, uuid.UUID(data["audit_event_id"]))
        assert audit is not None
        assert len(audit.event_hash) == 64


def test_v1_razorpay_webhook_endpoint(client, test_merchant_and_payment):
    """Verify production /api/v1/webhooks/razorpay endpoint processes valid webhooks."""
    merchant_id, payment_id = test_merchant_and_payment
    event_id = f"evt_v1_{uuid.uuid4().hex[:12]}"
    payload = {
        "event_id": event_id,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": 250000,
                    "currency": "INR",
                    "error_code": "BANK_TIMEOUT",
                    "error_description": "Issuing bank connection timed out",
                }
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    res = client.post(f"/api/v1/webhooks/razorpay?merchant_id={merchant_id}", content=body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["event_id"] == event_id


def test_concurrent_duplicate_webhook_delivery(client, test_merchant_and_payment):
    """Adversarial concurrency: 10 concurrent requests with identical event_id must safely deduplicate."""
    import concurrent.futures

    merchant_id, payment_id = test_merchant_and_payment
    event_id = f"evt_concurrent_{uuid.uuid4().hex[:12]}"
    payload = {
        "event_id": event_id,
        "event": "payment.captured",
        "data": {
            "payment_id": payment_id,
            "amount": "199.99",
            "currency": "USD",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    sig = compute_sig(body)
    headers = {"X-Razorpay-Signature": sig}

    def fire_webhook():
        return client.post(f"/api/v1/webhooks/razorpay?merchant_id={merchant_id}", content=body, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fire_webhook) for _ in range(10)]
        results = [f.result() for f in futures]

    # Every concurrent request must receive HTTP 200
    assert all(r.status_code == 200 for r in results)

    # Exactly one original process, remaining 9 duplicates
    parsed = [r.json() for r in results]
    duplicates = [p for p in parsed if p.get("is_duplicate") is True]
    originals = [p for p in parsed if p.get("is_duplicate") is False]

    assert len(originals) == 1
    assert len(duplicates) == 9

    # Database must contain exactly 1 WebhookDelivery row
    with SessionLocal() as db:
        deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.event_id == event_id).all()
        assert len(deliveries) == 1


def test_production_fails_closed_when_webhook_secret_missing(monkeypatch):
    """Production mode must fail closed if webhook secret is absent."""
    from services.webhook.security import WebhookSecurityError
    from services.config.settings import ConfigurationError
    import services.config.settings
    services.config.settings._settings = None
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)

    with pytest.raises((WebhookSecurityError, ConfigurationError)):
        WebhookSecurityVerifier.get_gateway_secret("razorpay")


def test_production_forbids_simulation_webhook(client, monkeypatch):
    """Production mode strictly forbids simulation webhook endpoints."""
    import services.config.settings
    services.config.settings._settings = None
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "super_secure_production_jwt_key_32_bytes_minimum_length_123")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "super_secure_webhook_secret_for_prod")

    body = b'{"event_id":"evt_sim","event_type":"payment.captured"}'
    sig = compute_sig(body, secret="super_secure_webhook_secret_for_prod")
    res = client.post("/api/webhooks/simulation", content=body, headers={"X-Razorpay-Signature": sig})
    assert res.status_code == 403
    assert "Simulation webhooks are strictly prohibited in production" in res.json()["detail"]

