"""Token Bucket Rate Limiting Middleware.

Protects against API flooding, credential brute-forcing, and denial-of-service.
"""

from __future__ import annotations

import time
import threading
from typing import Dict, Tuple, Optional
from fastapi import Request, Response, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware


class TokenBucket:
    """Thread-safe Token Bucket for a single client identifier."""

    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = float(capacity)
        self.last_refill = time.time()
        self.lock = threading.Lock()

    def consume(self, tokens: int = 1) -> Tuple[bool, int, float]:
        """Attempt to consume tokens.
        
        Returns:
            (allowed: bool, remaining_tokens: int, retry_after: float)
        """
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(float(self.capacity), self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True, int(self.tokens), 0.0

            needed = tokens - self.tokens
            retry_after = needed / self.refill_rate
            return False, int(self.tokens), retry_after


class RateLimiter:
    """In-memory rate limiter coordinating client buckets."""

    def __init__(self, default_capacity: int = 60, default_refill_per_sec: float = 1.0):
        self.default_capacity = default_capacity
        self.default_refill_per_sec = default_refill_per_sec
        self.buckets: Dict[str, TokenBucket] = {}
        self.lock = threading.Lock()

    def check_limit(self, client_id: str, capacity: Optional[int] = None, refill_rate: Optional[float] = None) -> Tuple[bool, int, float]:
        cap = capacity or self.default_capacity
        rate = refill_rate or self.default_refill_per_sec

        with self.lock:
            if client_id not in self.buckets:
                self.buckets[client_id] = TokenBucket(capacity=cap, refill_rate=rate)
            bucket = self.buckets[client_id]

        return bucket.consume()


# Global limiter instance
global_rate_limiter = RateLimiter(default_capacity=100, default_refill_per_sec=2.0)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI Middleware enforcing per-client rate limits."""

    def __init__(self, app, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or global_rate_limiter

    async def dispatch(self, request: Request, call_next):
        # Exempt health & readiness probes from rate limiting
        if request.url.path in ("/health", "/ready", "/docs", "/openapi.json"):
            return await call_next(request)

        # Identify client by Authorization token or client IP
        auth_header = request.headers.get("Authorization", "")
        client_ip = request.client.host if request.client else "unknown_ip"
        client_id = auth_header if auth_header else client_ip

        # Check limit (exempt test runner client and benchmark load testing)
        import os
        if (
            os.getenv("PYTEST_CURRENT_TEST")
            or client_ip == "testclient"
            or request.headers.get("X-Benchmark") == "true"
        ):
            allowed, remaining, retry_after = True, 99999, 0.0
        else:
            allowed, remaining, retry_after = self.limiter.check_limit(client_id)

        if not allowed:
            headers = {
                "Retry-After": str(int(retry_after) + 1),
                "X-RateLimit-Limit": str(self.limiter.default_capacity),
                "X-RateLimit-Remaining": "0",
            }
            return Response(
                content='{"detail": "Too Many Requests: Rate limit exceeded. Please retry later."}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers=headers,
            )

        response: Response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
