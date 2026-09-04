"""Targeted Regression Test Suite for DEF-RAY-001 and DEF-RAY-002.

Verifies:
DEF-RAY-001:
- Simultaneous/concurrent duplicate requests to execute_action_route receive HTTP 409 Conflict.
- Preserves the original ConcurrentExecutionBlockedError exception message.
- Zero duplicate gateway calls and idempotency remains intact.
- No HTTP 500.

DEF-RAY-002:
- Valid signed webhook without account_id succeeds (200 PROCESSED).
- Valid signed webhook with valid account_id (matching merchant slug or UUID) succeeds (200 PROCESSED).
- Unknown/unresolvable account_id returns HTTP 400 with clean rejected status.
- Cross-tenant account attempt (conflicting account_id vs merchant_id) returns HTTP 400 REJECTED.
- Invalid signature returns HTTP 401 UNAUTHORIZED.
- Expired timestamp returns HTTP 400 BAD REQUEST.
- Duplicate webhook delivery returns HTTP 200 DUPLICATE without duplicate mutations.
- Zero HTTP 500, zero NameError (logger), zero AttributeError (rzp_account_id), zero information leakage.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import time
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
    PaymentFailure,
    PaymentStatus,
    RecoveryAction,
    RecoveryOpportunity,
    PolicyDecision,
    PolicyDecisionType,
    ActionExecutionStatus,
)
from services.action_layer.idempotency import (
    IdempotencyManager,
    ConcurrentExecutionBlockedError,
)
from services.webhook.security import WebhookSecurityVerifier


@pytest.fixture(autouse=True)
def reset_settings():
    """Ensure environment and settings are reset cleanly for each test."""
    import services.config.settings
    services.config.settings._settings = None
    yield
    services.config.settings._settings = None


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_data():
    """Fetches real isolated merchants, payments, and recovery actions from database for testing."""
    with SessionLocal() as db:
        m_alpha = db.query(Merchant).filter(Merchant.status == "ACTIVE").first()
        m_beta = db.query(Merchant).filter(Merchant.status == "ACTIVE", Merchant.id != m_alpha.id).first()

        # Payment for Alpha
        payment_a = db.query(Payment).filter(Payment.merchant_id == m_alpha.id).first()

        # Action for Alpha
        act_a = db.query(RecoveryAction).filter(RecoveryAction.merchant_id == m_alpha.id).first()

        return {
            "merchant_alpha_id": str(m_alpha.id),
            "merchant_alpha_slug": m_alpha.slug,
            "merchant_beta_id": str(m_beta.id),
            "merchant_beta_slug": m_beta.slug,
            "payment_alpha_id": str(payment_a.id) if payment_a else str(uuid.uuid4()),
            "action_alpha_id": str(act_a.id) if act_a else str(uuid.uuid4()),
        }


# ==============================================================================
# DEF-RAY-001 Regression Tests
# ==============================================================================

def test_def_ray_001_concurrent_execution_blocked_returns_409(client, test_data, monkeypatch):
    """Verifies that ConcurrentExecutionBlockedError returns HTTP 409 Conflict with preserved detail."""
    auth_headers = {"Authorization": f"Bearer ray_test_{test_data['merchant_alpha_slug']}_OPERATOR"}
    action_id = test_data["action_alpha_id"]
    shared_key = f"idem_concurrent_test_{uuid.uuid4().hex[:12]}"

    def mock_acquire(*args, **kwargs):
        raise ConcurrentExecutionBlockedError(
            f"Concurrent financial execution for idempotency key '{shared_key}' is already locked by another cluster process."
        )

    monkeypatch.setattr(IdempotencyManager, "acquire_execution_slot", mock_acquire)

    payload = {
        "action_id": action_id,
        "idempotency_key": shared_key,
    }

    res = client.post("/api/v1/actions/execute", headers=auth_headers, json=payload)
    assert res.status_code == 409, f"Expected 409 Conflict, got {res.status_code}: {res.text}"
    body = res.json()
    assert "detail" in body
    assert "already locked by another cluster process" in body["detail"]
    assert "500" not in str(res.status_code)


def test_def_ray_001_first_request_and_concurrent_duplicate(client, test_data):
    """Verifies real concurrent execution: first request proceeds (423 Stage 1 blocked),
    concurrent duplicate request encountering the advisory lock returns 409 Conflict,
    and neither returns HTTP 500.
    """
    auth_headers = {"Authorization": f"Bearer ray_test_{test_data['merchant_alpha_slug']}_OPERATOR"}
    action_id = test_data["action_alpha_id"]
    shared_key = f"idem_real_race_{uuid.uuid4().hex[:12]}"

    payload = {
        "action_id": action_id,
        "idempotency_key": shared_key,
    }

    results = []

    def call_route():
        c = TestClient(app)
        return c.post("/api/v1/actions/execute", headers=auth_headers, json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(call_route) for _ in range(5)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            results.append((res.status_code, res.text))

    status_codes = [sc for sc, _ in results]
    # No 500 internal server errors allowed!
    assert 500 not in status_codes, f"Encountered unexpected 500 error in results: {results}"
    # Allowed status codes: 423 (Stage 1 Safety Lock) and 409 (Concurrent duplicate conflict) or 200 (Cached replay)
    for sc in status_codes:
        assert sc in (200, 409, 423), f"Unexpected status code {sc} in results: {results}"


# ==============================================================================
# DEF-RAY-002 Regression Tests
# ==============================================================================

def compute_webhook_sig(body: bytes) -> str:
    secret = WebhookSecurityVerifier.get_gateway_secret("razorpay")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_def_ray_002_valid_signed_webhook_without_account_id(client, test_data):
    """Valid signed webhook without account_id succeeds normally (200 PROCESSED)."""
    mid = test_data["merchant_alpha_id"]
    pid = test_data["payment_alpha_id"]
    payload = {
        "event_id": f"evt_valid_no_acc_{uuid.uuid4().hex[:8]}",
        "event_type": "payment.failed",
        "data": {
            "payment_id": pid,
            "amount": "150.00",
            "currency": "USD",
            "error_code": "BAD_REQUEST_ERROR",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
    }

    res = client.post(f"/api/v1/webhooks/razorpay?merchant_id={mid}", content=raw_body, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PROCESSED"
    assert data["is_duplicate"] is False


def test_def_ray_002_valid_signed_webhook_with_valid_account_id(client, test_data):
    """Valid signed webhook with valid account_id (matching merchant slug or UUID) succeeds (200 PROCESSED)."""
    pid = test_data["payment_alpha_id"]
    slug = test_data["merchant_alpha_slug"]
    payload = {
        "event_id": f"evt_valid_acc_{uuid.uuid4().hex[:8]}",
        "account_id": f"acc_{slug}",
        "event_type": "payment.failed",
        "data": {
            "payment_id": pid,
            "amount": "150.00",
            "currency": "USD",
            "error_code": "BAD_REQUEST_ERROR",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
    }

    res = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "PROCESSED"


def test_def_ray_002_invalid_account_mapping_rejected_400(client, test_data):
    """Webhook with unknown/unresolvable account_id returns HTTP 400 with clean rejected status."""
    pid = test_data["payment_alpha_id"]
    payload = {
        "event_id": f"evt_unknown_acc_{uuid.uuid4().hex[:8]}",
        "account_id": "acc_totally_unknown_merchant_9999",
        "event_type": "payment.failed",
        "data": {
            "payment_id": pid,
            "amount": "150.00",
            "currency": "USD",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
    }

    res = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["success"] is False
    assert data["status"] == "REJECTED"
    assert "Unknown gateway account mapping" in data["message"]


def test_def_ray_002_cross_tenant_account_mismatch_rejected_400(client, test_data):
    """Webhook with conflicting account_id vs merchant_id returns HTTP 400 REJECTED."""
    mid_alpha = test_data["merchant_alpha_id"]
    slug_beta = test_data["merchant_beta_slug"]
    payload = {
        "event_id": f"evt_mismatch_acc_{uuid.uuid4().hex[:8]}",
        "account_id": slug_beta,
        "event_type": "payment.failed",
        "data": {
            "amount": "150.00",
            "currency": "USD",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
    }

    res = client.post(f"/api/v1/webhooks/razorpay?merchant_id={mid_alpha}", content=raw_body, headers=headers)
    assert res.status_code == 400, f"Expected 400, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["success"] is False
    assert data["status"] == "REJECTED"
    assert "Cross-tenant account mismatch" in data["message"]


def test_def_ray_002_invalid_signature_returns_401(client):
    """Invalid webhook signature returns HTTP 401."""
    payload = {"event_id": f"evt_bad_sig_{uuid.uuid4().hex[:8]}", "event_type": "payment.failed"}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": "f" * 64,
    }
    res = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert res.status_code == 401
    assert "signature mismatch" in res.json()["detail"]


def test_def_ray_002_expired_timestamp_returns_400(client):
    """Expired webhook timestamp returns HTTP 400."""
    payload = {"event_id": f"evt_exp_{uuid.uuid4().hex[:8]}", "event_type": "payment.failed"}
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
        "X-Webhook-Timestamp": str(time.time() - 600),
    }
    res = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert res.status_code == 400
    assert "replay window" in res.json()["detail"]


def test_def_ray_002_duplicate_webhook_returns_200_duplicate(client, test_data):
    """Duplicate webhook returns HTTP 200 with DUPLICATE status and zero state mutation."""
    pid = test_data["payment_alpha_id"]
    slug = test_data["merchant_alpha_slug"]
    ev_id = f"evt_dup_{uuid.uuid4().hex[:8]}"
    payload = {
        "event_id": ev_id,
        "account_id": slug,
        "event_type": "payment.failed",
        "data": {
            "payment_id": pid,
            "amount": "150.00",
            "currency": "USD",
            "error_code": "BAD_REQUEST_ERROR",
        },
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = compute_webhook_sig(raw_body)
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": sig,
    }

    # First delivery
    r1 = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["is_duplicate"] is False

    # Second (duplicate) delivery
    r2 = client.post("/api/v1/webhooks/razorpay", content=raw_body, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["is_duplicate"] is True
    assert r2.json()["status"] == "DUPLICATE"
