"""Structured JSON Logging & Observability.

Features:
- Structured JSON log formatting.
- Request correlation IDs (X-Request-ID).
- Automatic redaction of sensitive financial credentials and authentication tokens.
- Request latency and status code tracking.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


import threading

REDACTED_KEYS = {
    "authorization",
    "password",
    "token",
    "secret",
    "api_key",
    "card_number",
    "cvv",
    "pan",
    "key_secret",
    "database_url",
    "jwt_secret_key",
    "webhook_secret",
    "cookie",
}


class HttpMetricsTracker:
    """Thread-safe runtime accumulator for HTTP and platform metrics."""
    _lock = threading.Lock()
    request_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}
    total_latency_ms: float = 0.0
    total_requests: int = 0
    idempotency_collisions: int = 0

    @classmethod
    def record_request(cls, method: str, status_code: int, duration_ms: float):
        with cls._lock:
            cls.total_requests += 1
            cls.total_latency_ms += duration_ms
            status_group = f"{status_code // 100}xx"
            key = f"{method.upper()}:{status_group}"
            cls.request_counts[key] = cls.request_counts.get(key, 0) + 1
            if status_code >= 400:
                cls.error_counts[str(status_code)] = cls.error_counts.get(str(status_code), 0) + 1
            if status_code == 409:
                cls.idempotency_collisions += 1

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        with cls._lock:
            avg_latency = (cls.total_latency_ms / cls.total_requests) if cls.total_requests > 0 else 0.0
            return {
                "total_requests": cls.total_requests,
                "avg_latency_ms": round(avg_latency, 2),
                "avg_latency_seconds": round(avg_latency / 1000.0, 4),
                "request_counts": dict(cls.request_counts),
                "error_counts": dict(cls.error_counts),
                "idempotency_collisions": cls.idempotency_collisions,
            }


def redact_sensitive_data(data: Any) -> Any:
    """Recursively redact sensitive key values in dictionaries and lists."""
    if isinstance(data, dict):
        clean = {}
        for k, v in data.items():
            if str(k).lower() in REDACTED_KEYS:
                clean[k] = "[REDACTED]"
            else:
                clean[k] = redact_sensitive_data(v)
        return clean
    if isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    return data


class JSONFormatter(logging.Formatter):
    """Formats log records as structured single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "request_id"):
            log_entry["request_id"] = getattr(record, "request_id")
        if hasattr(record, "merchant_id"):
            log_entry["merchant_id"] = getattr(record, "merchant_id")
        if hasattr(record, "latency_ms"):
            log_entry["latency_ms"] = getattr(record, "latency_ms")
        if hasattr(record, "status_code"):
            log_entry["status_code"] = getattr(record, "status_code")

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(redact_sensitive_data(log_entry))


logger = logging.getLogger("ray")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware attaching correlation IDs and logging structured HTTP metrics."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = (
            request.headers.get("X-Correlation-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        start_time = time.time()

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.time() - start_time) * 1000, 2)
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = correlation_id

            logger.info(
                f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms}ms) [cid={correlation_id}]",
                extra={
                    "correlation_id": correlation_id,
                    "request_id": correlation_id,
                    "status_code": response.status_code,
                    "latency_ms": duration_ms,
                },
            )
            HttpMetricsTracker.record_request(request.method, response.status_code, duration_ms)
            return response
        except Exception as e:
            duration_ms = round((time.time() - start_time) * 1000, 2)
            HttpMetricsTracker.record_request(request.method, 500, duration_ms)
            logger.error(
                f"{request.method} {request.url.path} -> 500 Unhandled Exception ({duration_ms}ms) [cid={correlation_id}]: {e}",
                extra={
                    "correlation_id": correlation_id,
                    "request_id": correlation_id,
                    "status_code": 500,
                    "latency_ms": duration_ms,
                },
                exc_info=True,
            )
            raise


def get_logger(name: str = "ray") -> logging.Logger:
    """Return a configured logger instance."""
    return logging.getLogger(name)
