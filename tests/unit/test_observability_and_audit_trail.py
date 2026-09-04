"""Observability, Incident Response & Audit Trail Verification Tests (Phase 11).

Verifies:
1. /metrics Prometheus endpoint returns valid metric formats with all critical counters and gauges.
2. /api/operations/diagnostics returns operational telemetry with database latency and webhook counts.
3. /api/audit/trail supports pagination (offset, limit) and entity_type filtering.
4. /api/audit/verify cryptographically validates the SHA-256 chain integrity.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest
from starlette.testclient import TestClient

from apps.api.main import app
from services.audit.logger import AuditLogger
from services.money_graph.database import SessionLocal
from services.money_graph.models import ActorType, AuditEvent, Merchant


@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    import services.config.settings
    services.config.settings._settings = None
    monkeypatch.setenv("ENVIRONMENT", "development")
    yield
    services.config.settings._settings = None


@pytest.fixture
def client():
    return TestClient(app)


def test_prometheus_metrics_endpoint(client: TestClient):
    """Verify Prometheus metrics endpoint outputs expected counters and gauges."""
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    text = res.text

    assert "ray_recovered_revenue_total" in text
    assert "ray_action_executions_total" in text
    assert "ray_outbox_backlog_total" in text
    assert "ray_webhook_events_total" in text
    assert "ray_kill_switch_active" in text
    assert "ray_stage_1_safety_lock" in text


def test_operations_diagnostics_endpoint(client: TestClient):
    """Verify JSON operations diagnostics endpoint returns system health telemetry."""
    res = client.get("/api/operations/diagnostics")
    assert res.status_code == 200
    data = res.json()

    assert "system_status" in data
    assert "database" in data
    assert data["database"]["status"] == "CONNECTED"
    assert "latency_ms" in data["database"]
    assert "webhooks" in data
    assert "total_received" in data["webhooks"]
    assert "worker" in data


def test_audit_trail_filtering_and_pagination(client: TestClient):
    """Verify /api/audit/trail supports filtering by entity_type and pagination."""
    with SessionLocal() as db:
        m = db.query(Merchant).first()
        assert m is not None

        # Insert 3 audit events
        for i in range(3):
            AuditLogger.record_event(
                session=db,
                merchant_id=m.id,
                entity_type="SYSTEM_SAFETY" if i == 0 else "PAYMENT",
                entity_id=uuid.uuid4(),
                event_type=f"TEST_EVENT_{i}",
                actor_type=ActorType.SYSTEM,
                actor_id="test_actor",
                payload_after={"step": i},
            )
        db.commit()

        auth_headers = {"Authorization": f"Bearer ray_test_{m.slug}_OPERATOR"}

        # Query all events
        res_all = client.get("/api/audit/trail", headers=auth_headers)
        assert res_all.status_code == 200
        events = res_all.json()
        assert len(events) >= 3

        # Query filtered by entity_type=SYSTEM_SAFETY
        res_filtered = client.get("/api/audit/trail?entity_type=SYSTEM_SAFETY", headers=auth_headers)
        assert res_filtered.status_code == 200
        filtered_events = res_filtered.json()
        assert all(e["entity_type"] == "SYSTEM_SAFETY" for e in filtered_events)
        assert len(filtered_events) >= 1

        # Query with pagination limit=1
        res_paginated = client.get("/api/audit/trail?limit=1&offset=0", headers=auth_headers)
        assert res_paginated.status_code == 200
        assert len(res_paginated.json()) == 1


def test_audit_chain_verification_endpoint(client: TestClient):
    """Verify /api/audit/verify endpoint verifies SHA-256 chain for tenant."""
    with SessionLocal() as db:
        m = db.query(Merchant).first()
        assert m is not None
        auth_headers = {"Authorization": f"Bearer ray_test_{m.slug}_OPERATOR"}

    res = client.get("/api/audit/verify", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "is_valid" in data
    assert "total_events_checked" in data
    assert data["is_valid"] is True
