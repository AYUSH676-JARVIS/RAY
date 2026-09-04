"""Integration Test Suite for Admin Control Plane & Governance.

Verifies:
1. GET /api/v1/admin/merchants lists merchants with RBAC enforcement.
2. GET and PUT /api/v1/admin/merchants/{id}/config updates configuration with audit trail.
3. Non-admin principals cannot access admin endpoints (403 Forbidden).
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from services.money_graph.database import SessionLocal
from services.money_graph.models import AuditEvent, Merchant


@pytest.fixture
def client():
    return TestClient(app)


def test_admin_list_merchants(client):
    """Admin principal lists all merchant organizations."""
    headers = {"Authorization": "Bearer ray_test_merchant_admin"}
    res = client.get("/api/v1/admin/merchants", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "slug" in data[0]
    assert "status" in data[0]


def test_admin_merchant_config_lifecycle(client):
    """Admin reads and updates merchant configuration with audit record."""
    with SessionLocal() as session:
        m = session.execute(Merchant.__table__.select()).first()
        assert m is not None
        merchant_id = str(m.id)

    headers = {"Authorization": "Bearer ray_test_merchant_admin"}

    # 1. Get config
    get_res = client.get(f"/api/v1/admin/merchants/{merchant_id}/config", headers=headers)
    assert get_res.status_code == 200
    assert get_res.json()["merchant_id"] == merchant_id

    # 2. Update config
    put_res = client.put(
        f"/api/v1/admin/merchants/{merchant_id}/config",
        headers=headers,
        json={"currency": "USD", "status": "ACTIVE"},
    )
    assert put_res.status_code == 200
    assert put_res.json()["currency"] == "USD"

    # 3. Verify audit log entry
    with SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter_by(event_type="ADMIN_MERCHANT_CONFIG_UPDATED")
            .order_by(AuditEvent.timestamp.desc())
            .first()
        )
        assert audit is not None
        assert audit.entity_type == "MERCHANT"


def test_admin_endpoints_rbac_forbidden(client):
    """Non-admin roles are rejected with 403 Forbidden on admin endpoints."""
    # Analyst role does not possess ADMIN_MANAGE permission
    headers = {"Authorization": "Bearer ray_test_analyst"}
    res = client.get("/api/v1/admin/merchants", headers=headers)
    assert res.status_code == 403
