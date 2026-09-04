"""Test suite for Operations & Monitoring API.

Verifies:
1. GET /api/v1/operations/status returns database status, latency, worker, webhooks, outbox, and circuit breakers.
2. System health status calculation.
"""

from fastapi.testclient import TestClient
from apps.api.main import app


def test_operations_status_endpoint():
    """GET /api/v1/operations/status returns complete operational telemetry."""
    client = TestClient(app)
    res = client.get("/api/v1/operations/status")
    assert res.status_code == 200
    data = res.json()

    assert "system_status" in data
    assert data["system_status"] in ["HEALTHY", "DEGRADED", "CRITICAL"]

    assert "database" in data
    assert data["database"]["status"] == "CONNECTED"
    assert data["database"]["latency_ms"] >= 0

    assert "worker" in data
    assert data["worker"]["status"] == "ACTIVE"

    assert "webhooks" in data
    assert "total_received" in data["webhooks"]
    assert "processed_count" in data["webhooks"]

    assert "outbox" in data
    assert "pending_backlog_count" in data["outbox"]

    assert "circuit_breakers" in data
    assert data["circuit_breakers"]["razorpay_gateway"] in ["CLOSED", "OPEN", "HALF_OPEN"]
