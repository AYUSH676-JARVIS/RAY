"""Integration Test Suite for Production Observability & Prometheus Metrics.

Verifies:
1. GET /metrics returns valid Prometheus text format with essential metrics.
2. GET /api/v1/metrics returns identical format.
3. X-Correlation-ID header propagation on all requests.
4. Custom X-Correlation-ID is preserved and echoed on response.
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_prometheus_metrics_endpoint(client):
    """GET /metrics returns standard Prometheus text format."""
    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    text = res.text

    assert "ray_up 1" in text
    assert "ray_database_up" in text
    assert "ray_payments_total" in text
    assert "ray_outbox_backlog_total" in text
    assert "ray_webhooks_received_total" in text
    assert "ray_stage_1_safety_lock" in text


def test_v1_metrics_endpoint(client):
    """GET /api/v1/metrics returns standard Prometheus text format."""
    res = client.get("/api/v1/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    assert "ray_up 1" in res.text


def test_correlation_id_auto_generation(client):
    """Requests without correlation ID receive an automatically generated X-Correlation-ID."""
    res = client.get("/health")
    assert res.status_code == 200
    assert "x-correlation-id" in res.headers
    cid = res.headers["x-correlation-id"]
    assert len(cid) > 10


def test_correlation_id_propagation(client):
    """Custom X-Correlation-ID is propagated through response headers."""
    custom_cid = "corr-test-trace-998877"
    res = client.get("/health", headers={"X-Correlation-ID": custom_cid})
    assert res.status_code == 200
    assert res.headers.get("x-correlation-id") == custom_cid
