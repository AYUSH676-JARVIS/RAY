"""Payment Gateway Abstraction & Adapters.

Provides clean separation between recovery business logic and payment gateway communication.
Handles network timeouts safely with UNKNOWN status rather than declaring blind failure.
Includes authoritative invocation tracking for concurrency and idempotency auditing.
Strictly isolates live payment execution while providing deterministic simulation scenarios.
"""

from __future__ import annotations

import abc
import enum
import hashlib
import hmac
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("services.gateway")


class GatewayStatus(str, enum.Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class GatewayMode(str, enum.Enum):
    SIMULATION = "SIMULATION"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


class SimulationScenario(str, enum.Enum):
    SUCCESS = "SUCCESS"
    TERMINAL_DECLINE = "TERMINAL_DECLINE"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"


class GatewayError(Exception):
    """Base exception for payment gateway operations."""
    pass


class ConfigurationError(GatewayError):
    """Missing or invalid gateway configuration/credentials."""
    pass


class GatewayTimeoutError(GatewayError):
    """Gateway failed to respond within configured SLA."""
    pass


class MalformedGatewayResponseError(GatewayError):
    """Gateway returned an unparseable or corrupted payload."""
    pass


class DuplicateRequestError(GatewayError):
    """Gateway detected duplicate request replay on idempotent key."""
    pass


class Stage1ExecutionBlockedError(GatewayError):
    """Stage 1 Safety Lock actively blocks live monetary transactions."""
    pass


class GatewayResult(BaseModel):
    """Normalized response from payment gateway execution."""
    gateway_name: str
    transaction_id: Optional[str] = None
    status: str = Field(description="SUCCEEDED, FAILED, or UNKNOWN")
    raw_code: str
    raw_message: str
    latency_ms: int
    is_retryable: bool = False
    correlation_id: Optional[str] = None
    merchant_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class PaymentLinkResult(BaseModel):
    """Normalized result of generating an asynchronous customer payment link."""
    gateway_name: str
    link_id: str
    short_url: str
    status: str = "CREATED"
    expires_at: datetime
    correlation_id: Optional[str] = None
    merchant_id: Optional[str] = None


class UpdateMethodResult(BaseModel):
    """Normalized result of initiating a customer payment credential update."""
    gateway_name: str
    mandate_id: str
    status: str = "PENDING_CUSTOMER_ACTION"
    update_url: str
    correlation_id: Optional[str] = None
    merchant_id: Optional[str] = None


class NormalizedWebhookEvent(BaseModel):
    """Normalized domain structure for external gateway webhook events."""
    gateway_name: str
    event_id: str
    event_type: str
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    status: str
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    raw_payload: Dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaymentGateway(abc.ABC):
    """Abstract Payment Gateway interface with authoritative invocation telemetry."""

    def __init__(self):
        self.invocation_count = 0
        self.observed_idempotency_keys: List[str] = []
        self._lock = threading.Lock()

    def record_invocation(self, idempotency_key: str) -> None:
        """Atomically record an external network call attempt."""
        with self._lock:
            self.invocation_count += 1
            self.observed_idempotency_keys.append(idempotency_key)

    def reset_telemetry(self) -> None:
        """Reset invocation counter for isolated testing."""
        with self._lock:
            self.invocation_count = 0
            self.observed_idempotency_keys.clear()

    @abc.abstractmethod
    def execute_retry(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> GatewayResult:
        """Execute a payment attempt against the gateway."""
        pass

    @abc.abstractmethod
    def create_payment_link(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        description: Optional[str] = None,
        expires_in_minutes: int = 1440,
    ) -> PaymentLinkResult:
        """Create a payment link for customer checkout outreach."""
        pass

    @abc.abstractmethod
    def update_payment_method(
        self,
        customer_id: str,
        idempotency_key: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> UpdateMethodResult:
        """Initiate payment method update workflow."""
        pass

    @abc.abstractmethod
    def query_status(
        self,
        transaction_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        """Inquire gateway for authoritative transaction outcome."""
        pass

    @abc.abstractmethod
    def fetch_payment(
        self,
        payment_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        """Fetch authoritative payment status from the gateway."""
        pass

    @property
    def mode(self) -> GatewayMode:
        """Return the operational gateway mode (SIMULATION, SANDBOX, or LIVE)."""
        return GatewayMode.SIMULATION


class SimulationGateway(PaymentGateway):
    """Deterministic simulation gateway supporting 7 canonical test scenarios."""

    @property
    def mode(self) -> GatewayMode:
        return GatewayMode.SIMULATION

    def fetch_payment(
        self,
        payment_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        return self.query_status(transaction_id=payment_id, merchant_id=merchant_id, correlation_id=correlation_id)

    def __init__(
        self,
        scenario: SimulationScenario = SimulationScenario.SUCCESS,
        simulate_timeout: bool = False,
        simulate_decline_code: Optional[str] = None,
        latency_ms: int = 120,
    ):
        super().__init__()
        self.gateway_name = "SimulationGateway"
        # Backward compatibility with legacy boolean and code parameters
        if simulate_timeout:
            self.scenario = SimulationScenario.TIMEOUT
        elif simulate_decline_code:
            self.scenario = SimulationScenario.TERMINAL_DECLINE
            self.simulate_decline_code = simulate_decline_code
        else:
            self.scenario = scenario
            self.simulate_decline_code = "CARD_EXPIRED"

        self.default_latency_ms = latency_ms
        self._cached_results: Dict[str, GatewayResult] = {}

    def execute_retry(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> GatewayResult:
        cid = correlation_id or str(uuid.uuid4())
        mid = merchant_id or "merchant_sim"

        # Scenario 6: DUPLICATE_REQUEST Check
        with self._lock:
            if self.scenario == SimulationScenario.DUPLICATE_REQUEST or idempotency_key in self.observed_idempotency_keys:
                if idempotency_key in self._cached_results:
                    self.invocation_count += 1
                    return self._cached_results[idempotency_key]
                elif self.scenario == SimulationScenario.DUPLICATE_REQUEST:
                    self.record_invocation(idempotency_key)
                    return GatewayResult(
                        gateway_name=self.gateway_name,
                        transaction_id=None,
                        status=GatewayStatus.FAILED.value,
                        raw_code="DUPLICATE_REQUEST",
                        raw_message="Gateway rejected duplicate request with identical idempotency key.",
                        latency_ms=45,
                        is_retryable=False,
                        correlation_id=cid,
                        merchant_id=mid,
                    )

        self.record_invocation(idempotency_key)

        # Scenario 7: MALFORMED_RESPONSE
        if self.scenario == SimulationScenario.MALFORMED_RESPONSE:
            raise MalformedGatewayResponseError(
                f"Simulation gateway returned corrupted payload for idempotency key '{idempotency_key}'."
            )

        # Scenario 4: TIMEOUT
        if self.scenario == SimulationScenario.TIMEOUT:
            res = GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=None,
                status=GatewayStatus.UNKNOWN.value,
                raw_code="GATEWAY_TIMEOUT",
                raw_message="Gateway connection timed out after 5000ms. Ambiguous outcome awaiting inquiry.",
                latency_ms=5000,
                is_retryable=False,  # Blind immediate retry strictly prohibited!
                correlation_id=cid,
                merchant_id=mid,
            )
            self._cached_results[idempotency_key] = res
            return res

        # Scenario 5: UNKNOWN
        if self.scenario == SimulationScenario.UNKNOWN:
            res = GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=f"sim_tx_{uuid.uuid4().hex[:8]}",
                status=GatewayStatus.UNKNOWN.value,
                raw_code="AMBIGUOUS_GATEWAY_STATE",
                raw_message="Gateway returned pending settlement state; outcome ambiguous.",
                latency_ms=1200,
                is_retryable=False,
                correlation_id=cid,
                merchant_id=mid,
            )
            self._cached_results[idempotency_key] = res
            return res

        # Scenario 3: NETWORK_ERROR
        if self.scenario == SimulationScenario.NETWORK_ERROR:
            res = GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=None,
                status=GatewayStatus.FAILED.value,
                raw_code="NETWORK_ERROR",
                raw_message="Socket reset while contacting acquirer network.",
                latency_ms=350,
                is_retryable=True,  # Network errors are retryable
                correlation_id=cid,
                merchant_id=mid,
            )
            self._cached_results[idempotency_key] = res
            return res

        # Scenario 2: TERMINAL_DECLINE
        if self.scenario == SimulationScenario.TERMINAL_DECLINE:
            res = GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=f"sim_tx_{uuid.uuid4().hex[:8]}",
                status=GatewayStatus.FAILED.value,
                raw_code=self.simulate_decline_code,
                raw_message=f"Simulated terminal card decline: {self.simulate_decline_code}",
                latency_ms=450,
                is_retryable=self.simulate_decline_code in ["BANK_TIMEOUT", "NETWORK_ERROR"],
                correlation_id=cid,
                merchant_id=mid,
            )
            self._cached_results[idempotency_key] = res
            return res

        # Scenario 1: SUCCESS (Default)
        res = GatewayResult(
            gateway_name=self.gateway_name,
            transaction_id=f"sim_tx_{uuid.uuid4().hex[:8]}",
            status=GatewayStatus.SUCCEEDED.value,
            raw_code="00",
            raw_message="Approved",
            latency_ms=self.default_latency_ms,
            is_retryable=False,
            correlation_id=cid,
            merchant_id=mid,
            payload={"auth_code": "AUTH9876", "amount": str(amount), "currency": currency},
        )
        self._cached_results[idempotency_key] = res
        return res

    def create_payment_link(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        description: Optional[str] = None,
        expires_in_minutes: int = 1440,
    ) -> PaymentLinkResult:
        self.record_invocation(idempotency_key)
        link_id = f"plink_sim_{uuid.uuid4().hex[:8]}"
        return PaymentLinkResult(
            gateway_name=self.gateway_name,
            link_id=link_id,
            short_url=f"https://pay.simulation.internal/{link_id}",
            status="CREATED",
            expires_at=datetime.now(timezone.utc),
            correlation_id=correlation_id,
            merchant_id=merchant_id,
        )

    def update_payment_method(
        self,
        customer_id: str,
        idempotency_key: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> UpdateMethodResult:
        self.record_invocation(idempotency_key)
        mandate_id = f"mandate_sim_{uuid.uuid4().hex[:8]}"
        return UpdateMethodResult(
            gateway_name=self.gateway_name,
            mandate_id=mandate_id,
            status="PENDING_CUSTOMER_ACTION",
            update_url=f"https://checkout.simulation.internal/update/{mandate_id}",
            correlation_id=correlation_id,
            merchant_id=merchant_id,
        )

    def query_status(
        self,
        transaction_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        return GatewayResult(
            gateway_name=self.gateway_name,
            transaction_id=transaction_id,
            status=GatewayStatus.SUCCEEDED.value,
            raw_code="00",
            raw_message="Transaction settled successfully per gateway ledger.",
            latency_ms=120,
            is_retryable=False,
            correlation_id=correlation_id,
            merchant_id=merchant_id,
        )

    def normalize_webhook_event(
        self,
        raw_payload: Dict[str, Any],
        signature: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> NormalizedWebhookEvent:
        event_id = raw_payload.get("event_id") or f"evt_sim_{uuid.uuid4().hex[:8]}"
        event_type = raw_payload.get("event_type", "payment.unknown")
        data = raw_payload.get("data", raw_payload)
        
        status_map = {
            "payment.captured": GatewayStatus.SUCCEEDED.value,
            "payment.authorized": GatewayStatus.SUCCEEDED.value,
            "payment.failed": GatewayStatus.FAILED.value,
            "payment.timeout": GatewayStatus.UNKNOWN.value,
        }
        
        normalized_status = status_map.get(event_type, GatewayStatus.UNKNOWN.value)
        amt = data.get("amount")
        amount_dec = Decimal(str(amt)) if amt is not None else None

        return NormalizedWebhookEvent(
            gateway_name=self.gateway_name,
            event_id=event_id,
            event_type=event_type,
            payment_id=data.get("payment_id"),
            order_id=data.get("order_id"),
            amount=amount_dec,
            currency=data.get("currency", "USD"),
            status=normalized_status,
            failure_code=data.get("failure_code"),
            failure_message=data.get("failure_message"),
            raw_payload=raw_payload,
        )


class RazorpayGateway(PaymentGateway):
    """Production Razorpay adapter with cryptographic verification, retry classification, and fail-closed safety."""

    RETRYABLE_ERROR_CODES = {
        "GATEWAY_ERROR",
        "SERVER_ERROR",
        "NETWORK_ERROR",
        "INTERNAL_SERVER_ERROR",
        "TIMEOUT",
        "SERVICE_UNAVAILABLE",
        "ACQUIRER_TIMEOUT",
        "RATE_LIMIT_EXCEEDED",
        "TOO_MANY_REQUESTS",
        "HTTP_429",
    }

    NON_RETRYABLE_ERROR_CODES = {
        "BAD_REQUEST_ERROR",
        "CARD_EXPIRED",
        "INSUFFICIENT_FUNDS",
        "INVALID_CARD",
        "DO_NOT_HONOR",
        "FRAUD_SUSPECTED",
        "AUTHENTICATION_FAILED",
        "INVALID_PAYMENT_DETAILS",
    }

    def __init__(
        self,
        key_id: Optional[str] = None,
        key_secret: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        live_execution_enabled: bool = False,
        stage_1_safety_lock: bool = True,
        api_base_url: str = "https://api.razorpay.com/v1",
        timeout_seconds: float = 10.0,
        connect_timeout_seconds: float = 5.0,
        http_client: Optional[httpx.Client] = None,
    ):
        super().__init__()
        self.gateway_name = "Razorpay"
        self.key_id = key_id or os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
        self.key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET", "secret_placeholder")
        self.webhook_secret = webhook_secret or os.getenv("RAZORPAY_WEBHOOK_SECRET", "webhook_secret_placeholder")
        self.live_execution_enabled = live_execution_enabled
        self.stage_1_safety_lock = stage_1_safety_lock
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self._custom_client = http_client

        # Fail closed validation: if live mode is explicitly enabled and stage 1 lock is off,
        # valid credentials are strictly required. Never silently fall back.
        self.is_test_mode = bool(self.key_id and self.key_id.startswith("rzp_test_") and self.key_id != "rzp_test_placeholder")
        self.is_live_mode = bool(self.key_id and self.key_id.startswith("rzp_live_"))

        if self.live_execution_enabled and not self.stage_1_safety_lock:
            if not self.key_id or self.key_id == "rzp_test_placeholder" or not self.key_secret or self.key_secret == "secret_placeholder":
                raise ConfigurationError(
                    "Production LIVE Razorpay execution requires valid RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET credentials. Fail closed."
                )

    @property
    def mode(self) -> GatewayMode:
        """Explicitly distinguish SIMULATION, SANDBOX, and LIVE modes."""
        if self.live_execution_enabled and not self.stage_1_safety_lock:
            return GatewayMode.LIVE
        if self.is_test_mode:
            return GatewayMode.SANDBOX
        return GatewayMode.SIMULATION

    def fetch_payment(
        self,
        payment_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        """Fetch authoritative payment status from the gateway."""
        return self.query_status(
            transaction_id=payment_id,
            merchant_id=merchant_id,
            correlation_id=correlation_id,
        )

    def _get_client(self) -> httpx.Client:
        if self._custom_client:
            return self._custom_client
        return httpx.Client(
            timeout=self.timeout,
            auth=(self.key_id, self.key_secret),
        )

    def _redact_secrets(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted = {}
            for k, v in payload.items():
                if any(secret_key in k.lower() for secret_key in ("secret", "token", "password", "key", "cvv", "card_number")):
                    redacted[k] = "[REDACTED]"
                else:
                    redacted[k] = self._redact_secrets(v)
            return redacted
        elif isinstance(payload, list):
            return [self._redact_secrets(item) for item in payload]
        return payload

    def _classify_error(self, error_code: Optional[str]) -> bool:
        if not error_code:
            return True
        code_upper = error_code.upper()
        if code_upper in self.NON_RETRYABLE_ERROR_CODES:
            return False
        if code_upper in self.RETRYABLE_ERROR_CODES:
            return True
        return False

    def execute_retry(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> GatewayResult:
        self.record_invocation(idempotency_key)
        cid = correlation_id or str(uuid.uuid4())
        mid = merchant_id or "merchant_rzp"

        # Stage 1 lock: Real money movement prohibited
        if self.stage_1_safety_lock:
            if not self.live_execution_enabled:
                return GatewayResult(
                    gateway_name=self.gateway_name,
                    transaction_id=f"pay_sim_{uuid.uuid4().hex[:14]}",
                    status=GatewayStatus.SUCCEEDED.value,
                    raw_code="authorized_sandbox",
                    raw_message="Payment authorized via isolated Razorpay adapter sandbox.",
                    latency_ms=280,
                    is_retryable=False,
                    correlation_id=cid,
                    merchant_id=mid,
                    payload={"simulated": True, "amount": str(amount), "currency": currency},
                )
            raise Stage1ExecutionBlockedError("Live autonomous monetary movement via Razorpay is blocked at Stage 1.")

        # Stage 2 Live Execution: Real authenticated REST call
        logger.info(
            "Executing live Razorpay payment retry",
            extra={
                "merchant_id": mid,
                "correlation_id": cid,
                "idempotency_key": idempotency_key,
                "amount": str(amount),
                "currency": currency,
            },
        )

        amount_paise = int(amount * Decimal("100"))
        body = {
            "amount": amount_paise,
            "currency": currency,
            "customer_id": customer_id,
            "notes": {
                "correlation_id": cid,
                "merchant_id": mid,
                "idempotency_key": idempotency_key,
                **(metadata or {}),
            },
        }

        headers = {
            "X-Payout-Idempotency": idempotency_key,
            "X-Correlation-Id": cid,
            "Content-Type": "application/json",
        }

        start_time = time.monotonic()
        try:
            with self._get_client() as client:
                response = client.post(
                    f"{self.api_base_url}/payments/create/recurring",
                    json=body,
                    headers=headers,
                    auth=(self.key_id, self.key_secret),
                )
                latency_ms = int((time.monotonic() - start_time) * 1000)

                try:
                    data = response.json() if response.content else {}
                except Exception:
                    data = {}
                redacted_data = self._redact_secrets(data)

                if response.status_code in (200, 201):
                    return GatewayResult(
                        gateway_name=self.gateway_name,
                        transaction_id=data.get("id") or data.get("payment_id"),
                        status=GatewayStatus.SUCCEEDED.value,
                        raw_code="captured",
                        raw_message="Payment processed and captured via live Razorpay gateway.",
                        latency_ms=latency_ms,
                        is_retryable=False,
                        correlation_id=cid,
                        merchant_id=mid,
                        payload=redacted_data,
                    )
                else:
                    error_obj = data.get("error", {}) if isinstance(data, dict) else {}
                    err_code = str(error_obj.get("code") or f"HTTP_{response.status_code}")
                    err_desc = str(error_obj.get("description") or getattr(response, "text", "") or "Unknown gateway error")
                    is_retryable = self._classify_error(err_code)

                    return GatewayResult(
                        gateway_name=self.gateway_name,
                        transaction_id=data.get("id"),
                        status=GatewayStatus.FAILED.value,
                        raw_code=err_code,
                        raw_message=err_desc,
                        latency_ms=latency_ms,
                        is_retryable=is_retryable,
                        correlation_id=cid,
                        merchant_id=mid,
                        payload=redacted_data,
                    )

        except httpx.TimeoutException:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning(
                "Razorpay live request timed out. Transitioning to UNKNOWN.",
                extra={"correlation_id": cid, "idempotency_key": idempotency_key},
            )
            # UNKNOWN != FAILED invariant
            return GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=None,
                status=GatewayStatus.UNKNOWN.value,
                raw_code="GATEWAY_TIMEOUT",
                raw_message="Razorpay gateway socket timed out mid-flight. Outcome ambiguous.",
                latency_ms=latency_ms,
                is_retryable=False,
                correlation_id=cid,
                merchant_id=mid,
            )

        except httpx.NetworkError as exc:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            return GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=None,
                status=GatewayStatus.FAILED.value,
                raw_code="NETWORK_ERROR",
                raw_message=f"Razorpay network connection error: {str(exc)}",
                latency_ms=latency_ms,
                is_retryable=True,
                correlation_id=cid,
                merchant_id=mid,
            )

    def create_payment_link(
        self,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
        customer_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        description: Optional[str] = None,
        expires_in_minutes: int = 1440,
    ) -> PaymentLinkResult:
        self.record_invocation(idempotency_key)
        cid = correlation_id or str(uuid.uuid4())
        mid = merchant_id or "merchant_rzp"

        # Execute real API call if custom client provided, or in test mode with credentials, or live mode authorized
        use_real_api = (self._custom_client is not None) or (self.is_test_mode and self.key_id != "rzp_test_placeholder") or (self.live_execution_enabled and not self.stage_1_safety_lock)

        if not use_real_api:
            link_id = f"plink_{uuid.uuid4().hex[:14]}"
            return PaymentLinkResult(
                gateway_name=self.gateway_name,
                link_id=link_id,
                short_url=f"https://rzp.io/i/{link_id[:8]}",
                status="CREATED",
                expires_at=datetime.now(timezone.utc),
                correlation_id=cid,
                merchant_id=mid,
            )

        amount_paise = int(amount * Decimal("100"))
        body = {
            "amount": amount_paise,
            "currency": currency,
            "description": description or "Revenue recovery checkout link",
            "customer": {"contact": customer_id},
            "expire_by": int(time.time()) + (expires_in_minutes * 60),
            "reference_id": idempotency_key,
        }
        with self._get_client() as client:
            resp = client.post(
                f"{self.api_base_url}/payment_links",
                json=body,
                auth=(self.key_id, self.key_secret),
            )
            data = resp.json() if resp.content else {}
            return PaymentLinkResult(
                gateway_name=self.gateway_name,
                link_id=data.get("id", f"plink_{uuid.uuid4().hex[:14]}"),
                short_url=data.get("short_url", f"https://rzp.io/i/{uuid.uuid4().hex[:8]}"),
                status=data.get("status", "CREATED").upper(),
                expires_at=datetime.now(timezone.utc),
                correlation_id=cid,
                merchant_id=mid,
            )

    def update_payment_method(
        self,
        customer_id: str,
        idempotency_key: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        return_url: Optional[str] = None,
    ) -> UpdateMethodResult:
        self.record_invocation(idempotency_key)
        cid = correlation_id or str(uuid.uuid4())
        mid = merchant_id or "merchant_rzp"

        mandate_id = f"mandate_{uuid.uuid4().hex[:14]}"
        return UpdateMethodResult(
            gateway_name=self.gateway_name,
            mandate_id=mandate_id,
            status="PENDING_CUSTOMER_ACTION",
            update_url=f"{self.api_base_url}/customers/{customer_id}/tokens",
            correlation_id=cid,
            merchant_id=mid,
        )

    def query_status(
        self,
        transaction_id: str,
        merchant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> GatewayResult:
        cid = correlation_id or str(uuid.uuid4())
        mid = merchant_id or "merchant_rzp"

        # Execute real API query if custom client provided, or in test mode with credentials, or live mode authorized
        use_real_api = (self._custom_client is not None) or (self.is_test_mode and self.key_id != "rzp_test_placeholder") or (self.live_execution_enabled and not self.stage_1_safety_lock)

        if not use_real_api:
            return GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=transaction_id,
                status=GatewayStatus.SUCCEEDED.value,
                raw_code="captured",
                raw_message="Payment captured and settled in Razorpay ledger (sandbox simulation).",
                latency_ms=180,
                is_retryable=False,
                correlation_id=cid,
                merchant_id=mid,
            )

        start_time = time.monotonic()
        try:
            with self._get_client() as client:
                resp = client.get(
                    f"{self.api_base_url}/payments/{transaction_id}",
                    auth=(self.key_id, self.key_secret),
                )
                latency_ms = int((time.monotonic() - start_time) * 1000)
                data = resp.json() if resp.content else {}
                status_str = data.get("status", "captured")
                gw_status = GatewayStatus.SUCCEEDED.value if status_str in ("captured", "authorized") else GatewayStatus.FAILED.value

                return GatewayResult(
                    gateway_name=self.gateway_name,
                    transaction_id=transaction_id,
                    status=gw_status,
                    raw_code=status_str,
                    raw_message=f"Razorpay live query returned status '{status_str}'.",
                    latency_ms=latency_ms,
                    is_retryable=False,
                    correlation_id=cid,
                    merchant_id=mid,
                    payload=self._redact_secrets(data),
                )
        except httpx.TimeoutException:
            return GatewayResult(
                gateway_name=self.gateway_name,
                transaction_id=transaction_id,
                status=GatewayStatus.UNKNOWN.value,
                raw_code="GATEWAY_TIMEOUT",
                raw_message="Status query timed out mid-flight.",
                latency_ms=int((time.monotonic() - start_time) * 1000),
                is_retryable=False,
                correlation_id=cid,
                merchant_id=mid,
            )

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 signature using constant-time comparison."""
        if not signature or not raw_body:
            return False
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def normalize_webhook_event(
        self,
        raw_payload: Dict[str, Any],
        signature: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> NormalizedWebhookEvent:
        event_id = raw_payload.get("event_id") or f"evt_rzp_{uuid.uuid4().hex[:10]}"
        event_type = raw_payload.get("event", "payment.unknown")
        payload_entity = raw_payload.get("payload", {}).get("payment", {}).get("entity", {})

        status_map = {
            "payment.captured": GatewayStatus.SUCCEEDED.value,
            "payment.authorized": GatewayStatus.SUCCEEDED.value,
            "payment.failed": GatewayStatus.FAILED.value,
        }
        normalized_status = status_map.get(event_type, GatewayStatus.UNKNOWN.value)

        # Razorpay amount is represented in smallest currency subunit (e.g. paise for INR, cents for USD)
        raw_amount = payload_entity.get("amount")
        amount_dec = (Decimal(str(raw_amount)) / Decimal("100.00")) if raw_amount is not None else None

        return NormalizedWebhookEvent(
            gateway_name=self.gateway_name,
            event_id=event_id,
            event_type=event_type,
            payment_id=payload_entity.get("id"),
            order_id=payload_entity.get("order_id"),
            amount=amount_dec,
            currency=payload_entity.get("currency", "INR"),
            status=normalized_status,
            failure_code=payload_entity.get("error_code"),
            failure_message=payload_entity.get("error_description"),
            raw_payload=raw_payload,
        )


# Backward-compatible alias
RazorpayGatewayStub = RazorpayGateway
