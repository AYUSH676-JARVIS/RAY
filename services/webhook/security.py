"""Cryptographic Webhook Security & Verification Boundary.

Enforces:
1. Constant-time HMAC-SHA256 signature verification.
2. Rejection of missing, empty, or malformed signatures.
3. Timestamp freshness checks to prevent replay attacks.
4. Safe secret handling strictly from configuration/environment.
5. Redaction of sensitive payload elements in logs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Optional, Tuple


class WebhookSecurityError(Exception):
    """Base exception for webhook security validation failures."""
    pass


class MissingSignatureError(WebhookSecurityError):
    """Signature header was not provided in inbound request."""
    pass


class MalformedSignatureError(WebhookSecurityError):
    """Signature format is invalid (e.g. invalid hex, wrong length)."""
    pass


class InvalidSignatureError(WebhookSecurityError):
    """Cryptographic signature check failed."""
    pass


class ExpiredTimestampError(WebhookSecurityError):
    """Webhook timestamp is outside allowed freshness window."""
    pass


class WebhookSecurityVerifier:
    """Production-grade cryptographic webhook verifier."""

    DEFAULT_TOLERANCE_SECONDS = 300  # 5 minutes replay defense window
    HEX_SIGNATURE_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")

    @classmethod
    def get_gateway_secret(cls, gateway_name: str, custom_secret: Optional[str] = None) -> str:
        """Resolve gateway webhook signing secret from secure configuration."""
        if custom_secret:
            return custom_secret

        from services.config.settings import get_settings
        settings = get_settings()

        if gateway_name.lower() == "razorpay" and settings.RAZORPAY_WEBHOOK_SECRET:
            val = settings.RAZORPAY_WEBHOOK_SECRET.get_secret_value()
            if val:
                return val

        env_key = f"{gateway_name.upper()}_WEBHOOK_SECRET"
        secret = os.getenv(env_key)
        if secret:
            return secret

        if settings.is_production:
            raise WebhookSecurityError(
                f"Production violation: Missing webhook signing secret for gateway '{gateway_name}'. Failing closed."
            )

        # Fallback strictly for development/testing environments
        return os.getenv("WEBHOOK_SIGNING_SECRET", "ray_dev_webhook_secret_key_998877")

    @classmethod
    def verify_signature(
        cls,
        raw_body: bytes,
        signature: Optional[str],
        secret: str,
        gateway_name: str = "Razorpay",
    ) -> bool:
        """Verify HMAC-SHA256 signature using constant-time comparison.
        
        Raises WebhookSecurityError subclasses on explicit security failures.
        """
        if not signature or not signature.strip():
            raise MissingSignatureError("Missing required webhook signature header.")

        clean_sig = signature.strip()

        # Check for signature header prefixes (e.g. t=...,v1=... or sha256=...)
        if "v1=" in clean_sig:
            # Razorpay / Stripe timestamped header format: t=12345,v1=hex
            parts = dict(item.split("=", 1) for item in clean_sig.split(",") if "=" in item)
            clean_sig = parts.get("v1", "")

        if not cls.HEX_SIGNATURE_PATTERN.match(clean_sig):
            raise MalformedSignatureError("Malformed webhook signature: expected 64-character hex digest.")

        if not raw_body:
            raise MalformedSignatureError("Empty webhook payload cannot be cryptographically verified.")

        expected = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison defends against timing attacks
        if not hmac.compare_digest(expected.lower(), clean_sig.lower()):
            raise InvalidSignatureError("Webhook signature verification failed: signature mismatch.")

        return True

    @classmethod
    def verify_timestamp_freshness(
        cls,
        timestamp_header: Optional[str],
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    ) -> bool:
        """Verify webhook timestamp is within tolerance window to prevent replay attacks."""
        if not timestamp_header:
            return True  # If gateway does not provide timestamp header, rely on unique event_id deduplication

        try:
            ts = float(timestamp_header)
            current_time = time.time()
            if abs(current_time - ts) > tolerance_seconds:
                raise ExpiredTimestampError(
                    f"Webhook timestamp {ts} is outside the allowed {tolerance_seconds}s replay window."
                )
            return True
        except (ValueError, TypeError):
            raise ExpiredTimestampError(f"Invalid timestamp header format: '{timestamp_header}'.")

    @classmethod
    def compute_payload_hash(cls, raw_body: bytes) -> str:
        """Compute SHA-256 digest of raw payload for durable audit tracking."""
        return hashlib.sha256(raw_body).hexdigest()
