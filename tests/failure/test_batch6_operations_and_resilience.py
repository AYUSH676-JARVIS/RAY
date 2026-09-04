"""Batch 6 Operations, Readiness & Rate Limiting Test Suite."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from apps.api.main import app
from services.common.logging import JSONFormatter
from services.common.rate_limiter import RateLimiter


client = TestClient(app)


def test_readiness_endpoint_returns_ready():
    """GET /ready returns 200 and reports database and migration health."""
    res = client.get("/ready")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["database"] == "connected"


def test_rate_limiter_blocks_excessive_requests():
    """RateLimiter permits up to capacity and rejects subsequent requests."""
    limiter = RateLimiter(default_capacity=3, default_refill_per_sec=0.1)
    client_id = "test_rate_limited_client"

    # First 3 should pass
    for _ in range(3):
        allowed, remaining, _ = limiter.check_limit(client_id)
        assert allowed is True

    # 4th should be blocked
    allowed, remaining, retry_after = limiter.check_limit(client_id)
    assert allowed is False
    assert retry_after > 0


def test_structured_json_logging_redacts_secrets():
    """JSONFormatter automatically redacts sensitive financial and auth keys."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Transaction processed",
        args=(),
        exc_info=None,
    )
    record.request_id = "trace-12345"
    record.merchant_id = "merchant-abc"

    # Inject message with secret dict
    formatted = formatter.format(record)
    assert "trace-12345" in formatted
    assert "merchant-abc" in formatted

    # Test recursive redaction
    from services.common.logging import redact_sensitive_data
    sensitive_dict = {
        "user": "alice",
        "password": "super_secret_password",
        "api_key": "ray_live_secret",
        "details": {"card_number": "4111222233334444", "amount": 100},
    }
    cleaned = redact_sensitive_data(sensitive_dict)
    assert cleaned["password"] == "[REDACTED]"
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["details"]["card_number"] == "[REDACTED]"
    assert cleaned["details"]["amount"] == 100


def test_correlation_id_propagated_in_headers():
    """X-Request-ID is preserved and returned in HTTP response headers."""
    trace_id = "custom-trace-uuid-1234"
    res = client.get("/health", headers={"X-Request-ID": trace_id})
    assert res.status_code == 200
    assert res.headers.get("X-Request-ID") == trace_id
