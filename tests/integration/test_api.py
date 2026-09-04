"""Integration tests verifying API startup and synthetic data retrieval."""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from apps.api.main import app
from services.money_graph.database import SessionLocal
from services.money_graph.models import Payment, AgentRun


@pytest.fixture(scope="module")
def client():
    """Synchronous test client for FastAPI with authenticated test principal."""
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer ray_test_merchant_admin"
        yield c


def test_health_endpoint(client: TestClient):
    """Verify /health returns 200 and healthy status."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "timestamp" in data
    assert "version" in data


def test_dashboard_metrics(client: TestClient):
    """Verify /api/dashboard returns financial intelligence aggregation."""
    res = client.get("/api/dashboard")
    assert res.status_code == 200
    data = res.json()
    assert "total_volume_usd" in data
    assert "total_payments_count" in data
    assert data["total_payments_count"] > 0
    assert "failed_payments_count" in data
    assert "failure_distribution" in data
    assert len(data["failure_distribution"]) > 0


def test_retrieve_payment_graph(client: TestClient):
    """Integration test proving that the API starts and can retrieve a real synthetic payment."""
    # Find a real payment ID from the database
    db = SessionLocal()
    try:
        sample_payment = db.query(Payment).first()
        assert sample_payment is not None, "Database must have seeded payments"
        payment_id = str(sample_payment.id)
    finally:
        db.close()

    # Query the payment via API
    res = client.get(f"/api/payments/{payment_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == payment_id
    assert "amount" in data
    assert "status" in data
    assert "attempts" in data
    assert isinstance(data["attempts"], list)
    if data["attempts"]:
        assert "idempotency_key" in data["attempts"][0]


def test_payment_not_found(client: TestClient):
    """Verify querying non-existent payment returns 404."""
    random_id = str(uuid.uuid4())
    res = client.get(f"/api/payments/{random_id}")
    assert res.status_code == 404
    assert f"Payment with ID '{random_id}' not found." in res.json()["detail"]


def test_opportunities_pagination(client: TestClient):
    """Verify /api/opportunities returns paginated records."""
    res = client.get("/api/opportunities?page=1&limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["page"] == 1
    assert data["limit"] == 10
    assert len(data["items"]) <= 10
    if data["items"]:
        item = data["items"][0]
        assert "strategy_name" in item
        assert "confidence_score" in item
        assert "estimated_recoverable_amount" in item


def test_audit_run_retrieval(client: TestClient):
    """Verify /api/audit/runs/{run_id} returns agent execution trace and tool calls."""
    db = SessionLocal()
    try:
        run = db.query(AgentRun).first()
        assert run is not None
        run_id = str(run.id)
    finally:
        db.close()

    res = client.get(f"/api/audit/runs/{run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == run_id
    assert "agent_name" in data
    assert "tool_calls" in data
    assert isinstance(data["tool_calls"], list)
