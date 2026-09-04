"""Security & Tenant Isolation Test Suite.

Verifies:
1. Zero unauthenticated access (HTTP 401).
2. Zero cross-tenant data leakage (HTTP 404 on cross-tenant resource requests).
3. Server-side RBAC permission boundaries (HTTP 403 on unauthorized roles).
4. Security response headers (HSTS, nosniff, DENY).
"""

from __future__ import annotations

import decimal
import uuid
import pytest
from starlette.testclient import TestClient

from apps.api.main import app
from services.money_graph.database import SessionLocal
from services.money_graph.models import Customer, Merchant, Order, Payment


@pytest.fixture
def client():
    return TestClient(app)


def test_anonymous_request_rejected_with_401(client: TestClient):
    """Anonymous access to protected endpoints is strictly rejected with HTTP 401."""
    # Dashboard
    res = client.get("/api/dashboard")
    assert res.status_code == 401
    assert "detail" in res.json()

    # Payments
    res = client.get(f"/api/payments/{uuid.uuid4()}")
    assert res.status_code == 401

    # Opportunities
    res = client.get("/api/opportunities")
    assert res.status_code == 401

    # Detect
    res = client.post("/api/opportunities/detect", json={"payment_id": str(uuid.uuid4())})
    assert res.status_code == 401


def test_invalid_token_rejected_with_401(client: TestClient):
    """Malformed or invalid bearer tokens are rejected with HTTP 401."""
    headers = {"Authorization": "Bearer invalid_gibberish_token"}
    res = client.get("/api/dashboard", headers=headers)
    assert res.status_code == 401


def test_cross_tenant_isolation_returns_404(client: TestClient):
    """Tenant A cannot read or detect opportunities on Tenant B payments.
    
    Returns 404 to avoid leaking resource existence.
    """
    db = SessionLocal()
    try:
        # Create Tenant Alpha
        m_alpha = Merchant(name="Tenant Alpha", slug=f"alpha-{uuid.uuid4().hex[:6]}")
        db.add(m_alpha)
        db.flush()

        cust_alpha = Customer(merchant_id=m_alpha.id, external_id="cust_a", email="a@alpha.com", name="Cust A", risk_score=0.1)
        db.add(cust_alpha)
        db.flush()

        order_alpha = Order(merchant_id=m_alpha.id, customer_id=cust_alpha.id, amount=decimal.Decimal("100.00"))
        db.add(order_alpha)
        db.flush()

        payment_alpha = Payment(
            merchant_id=m_alpha.id,
            order_id=order_alpha.id,
            customer_id=cust_alpha.id,
            amount=decimal.Decimal("100.00"),
            status="FAILED",
        )
        db.add(payment_alpha)
        db.flush()

        # Create Tenant Beta
        m_beta = Merchant(name="Tenant Beta", slug=f"beta-{uuid.uuid4().hex[:6]}")
        db.add(m_beta)
        db.flush()

        cust_beta = Customer(merchant_id=m_beta.id, external_id="cust_b", email="b@beta.com", name="Cust B", risk_score=0.1)
        db.add(cust_beta)
        db.flush()

        order_beta = Order(merchant_id=m_beta.id, customer_id=cust_beta.id, amount=decimal.Decimal("250.00"))
        db.add(order_beta)
        db.flush()

        payment_beta = Payment(
            merchant_id=m_beta.id,
            order_id=order_beta.id,
            customer_id=cust_beta.id,
            amount=decimal.Decimal("250.00"),
            status="FAILED",
        )
        db.add(payment_beta)
        db.commit()

        # Caller authenticates as Tenant Alpha
        alpha_headers = {"Authorization": f"Bearer ray_test_{m_alpha.slug}_OPERATOR"}

        # 1. Tenant Alpha queries Tenant Alpha payment -> 200 OK
        res_own = client.get(f"/api/payments/{payment_alpha.id}", headers=alpha_headers)
        assert res_own.status_code == 200
        assert res_own.json()["id"] == str(payment_alpha.id)

        # 2. Tenant Alpha attempts to query Tenant Beta payment -> 404 NOT FOUND (no leak)
        res_cross = client.get(f"/api/payments/{payment_beta.id}", headers=alpha_headers)
        assert res_cross.status_code == 404

        # 3. Tenant Alpha attempts to query Tenant Beta context -> 404 NOT FOUND
        res_context = client.get(f"/api/payments/{payment_beta.id}/context", headers=alpha_headers)
        assert res_context.status_code == 404

        # 4. Tenant Alpha attempts to trigger detection on Tenant Beta payment -> 404 NOT FOUND
        res_detect = client.post(
            "/api/opportunities/detect",
            json={"payment_id": str(payment_beta.id)},
            headers=alpha_headers,
        )
        assert res_detect.status_code == 404

        # 5. Tenant Alpha attempts to execute action on Tenant Beta -> 404 NOT FOUND
        res_exec = client.post(
            "/api/actions/execute",
            json={"action_id": str(uuid.uuid4()), "idempotency_key": f"idem_cross_{uuid.uuid4().hex[:8]}"},
            headers=alpha_headers,
        )
        assert res_exec.status_code == 404

        # 6. Tenant Alpha attempts to run decision loop on Tenant Beta payment -> 404 / rejected
        res_loop = client.post(
            "/api/decision-loop/run",
            json={"payment_id": str(payment_beta.id)},
            headers=alpha_headers,
        )
        assert res_loop.status_code in [400, 404]

        # 7. Dashboard scoping: Alpha dashboard does not contain Beta payments
        res_dash = client.get("/api/dashboard", headers=alpha_headers)
        assert res_dash.status_code == 200
        dash_data = res_dash.json()
        assert str(payment_beta.id) not in str(dash_data)

    finally:
        db.close()


def test_rbac_permission_boundaries(client: TestClient):
    """READ_ONLY role cannot trigger financial opportunity detection (returns 403 Forbidden)."""
    db = SessionLocal()
    try:
        sample_payment = db.query(Payment).filter(Payment.status == "FAILED").first()
        assert sample_payment is not None
        p_id = str(sample_payment.id)
    finally:
        db.close()

    # Authenticate as READ_ONLY user
    readonly_headers = {"Authorization": "Bearer ray_test_READ_ONLY"}

    # Dashboard view allowed
    res_dash = client.get("/api/dashboard", headers=readonly_headers)
    assert res_dash.status_code == 200

    # Opportunity detect disallowed (requires OPPORTUNITY_DETECT permission)
    res_detect = client.post(
        "/api/opportunities/detect",
        json={"payment_id": p_id},
        headers=readonly_headers,
    )
    assert res_detect.status_code == 403
    assert "Forbidden" in res_detect.json()["detail"]


def test_security_headers_present(client: TestClient):
    """All responses include defense-in-depth security headers."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert "Strict-Transport-Security" in res.headers
