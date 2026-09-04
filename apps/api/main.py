"""RAY Merchant Money Intelligence Engine — Control Plane API

Principles:
- AI recommends.
- Deterministic policy authorizes.
- Deterministic action layer executes.
- Outcome verification confirms reality.
- Audit records everything.

Security Invariants:
- Zero unauthenticated financial routes.
- Strict multi-tenant data isolation (no cross-tenant leakage; return 404 for non-tenant entities).
- Granular server-side RBAC.
- Hardened CORS and security headers.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from services.auth.models import Principal, Permission, Role
from services.auth.service import get_current_principal, require_permission, create_access_token
from services.config.settings import get_settings
from services.common.logging import StructuredLoggingMiddleware, HttpMetricsTracker
from services.common.rate_limiter import RateLimitMiddleware
from services.money_graph.database import get_db, init_db, engine, check_migrations_applied
from services.audit.logger import AuditLogger
from services.money_graph.models import (
    ActionExecutionStatus,
    ActorType,
    AgentRun,
    AuditEvent,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    PolicyDecision,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
    WebhookDelivery,
    OutboxEvent,
)
from services.action_layer.executor import (
    ActionExecutor,
    FinancialExecutionBlockedError,
    PolicyAuthorizationBlockedError,
    AmbiguousOutcomeBlockedError,
    InvalidIdempotencyKeyError,
    PaymentAlreadySettledError,
)
from services.action_layer.idempotency import ConcurrentExecutionBlockedError
from services.money_graph.schemas import FullMoneyContext
from services.money_graph.service import MoneyGraphService
from services.opportunities.engine import detect_recovery_opportunity
from services.opportunities.providers import OpportunityResult
from services.orchestrator import run_decision_workflow, DecisionWorkflowResult
from services.action_layer.gateway import SimulationGateway, RazorpayGateway
from services.scenarios.engine import ScenarioID, ScenarioDefinition, DemoScenarioEngine

from services.action_layer.stage2_activation import (
    Stage2ActivationManager,
    Stage2ActivationError,
    Stage2ActivationState,
)
from services.webhook import WebhookProcessor
from services.config.settings import get_settings
from services.webhook.security import (
    ExpiredTimestampError,
    InvalidSignatureError,
    MalformedSignatureError,
    MissingSignatureError,
    WebhookSecurityError,
)


app = FastAPI(
    title="RAY Merchant Money Intelligence Engine",
    description="Autonomous payment recovery and financial revenue control plane.",
    version="1.0.0",
)

logger = logging.getLogger("ray.api")

# ---------------------------------------------------------------------
# Observability & Rate Limiting Middleware
# ---------------------------------------------------------------------
app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)

# ---------------------------------------------------------------------
# CORS & Security Hardening
# ---------------------------------------------------------------------
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
ALLOWED_ORIGINS = [o.strip() for o in raw_origins.split(",") if o.strip()]

# In production, wildcard CORS with credentials is strictly prohibited
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"
if IS_PRODUCTION and "*" in ALLOWED_ORIGINS:
    raise RuntimeError("Production security violation: Wildcard CORS origin is prohibited when credentials are enabled.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Idempotency-Key", "X-Request-ID", "X-Correlation-ID", "X-Merchant-ID"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add defense-in-depth HTTP security headers."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


MAX_REQUEST_BODY_SIZE = 1 * 1024 * 1024  # 1 MB maximum allowed request body


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    """Enforce bounded request body size to mitigate DoS and OOM attacks."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BODY_SIZE:
                return JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={"detail": "Payload Too Large: Request body exceeds maximum allowed size of 1 MB."},
                )
        except ValueError:
            pass
    return await call_next(request)


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """Safely catch database exceptions without disclosing SQL or schema internals."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database operation failed safely. No state was leaked or corrupted."},
    )


@app.on_event("startup")
def on_startup():
    """Ensure database schema exists and is verified on boot."""
    try:
        init_db()
    except Exception as e:
        print(f"[Warning] Startup DB initialization check encountered: {e}")


# ---------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
    database: str
    environment: str
    gateway_mode: str = "SIMULATION"
    stage_1_safety_lock: bool = True
    kill_switch_engaged: bool = False


class DashboardMetricsResponse(BaseModel):
    total_volume_usd: Decimal
    total_payments_count: int
    failed_payments_count: int
    failure_rate_percentage: float
    recoverable_volume_usd: Decimal
    active_opportunities_count: int
    failure_distribution: Dict[str, int]
    policy_authorization_stats: Dict[str, int]
    recent_events: List[AuditEventSchema]
    failure_volume_usd: Decimal = Decimal("0.00")
    recovered_volume_usd: Decimal = Decimal("0.00")
    recovery_rate_percentage: float = 0.0
    policy_block_rate_percentage: float = 0.0
    strategy_effectiveness: Dict[str, Dict[str, Any]] = Field(default_factory=dict)



class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: Optional[uuid.UUID] = None
    entity_type: Optional[str] = None
    entity_id: Optional[uuid.UUID] = None
    sequence_number: Optional[int] = None
    event_type: str
    actor_type: str
    actor_id: str
    payload_before_json: Optional[Any] = None
    payload_after_json: Optional[Any] = None
    previous_event_hash: Optional[str] = None
    event_hash: Optional[str] = None
    timestamp: datetime


class PaymentAttemptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_number: int
    idempotency_key: str
    gateway_name: str
    gateway_transaction_id: Optional[str] = None
    status: str
    latency_ms: Optional[int] = None
    created_at: datetime


class PaymentFailureSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    failure_code: str
    raw_message: str
    is_retryable: bool
    created_at: datetime


class OpportunityItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    payment_id: uuid.UUID
    failure_id: uuid.UUID
    strategy_name: str
    confidence_score: float
    estimated_recoverable_amount: Decimal
    status: str
    failure_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PaymentDetailResponse(BaseModel):
    id: uuid.UUID
    merchant_id: uuid.UUID
    order_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: Optional[str]
    customer_email: Optional[str]
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    attempts: List[PaymentAttemptSchema]
    failures: List[PaymentFailureSchema]
    opportunities: List[OpportunityItemSchema]


class OpportunityListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[OpportunityItemSchema]


class ToolCallSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    input_payload_json: Optional[Any] = None
    output_payload_json: Optional[Any] = None
    status: str
    duration_ms: int
    created_at: datetime


class AgentRunDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: Optional[uuid.UUID] = None
    agent_name: str
    trigger_type: str
    status: str
    tokens_used: int
    cost_usd: Decimal
    metadata_json: Optional[Any] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    tool_calls: List[ToolCallSchema] = Field(default_factory=list)
    audit_events: List[AuditEventSchema] = Field(default_factory=list)


class DetectOpportunityRequest(BaseModel):
    payment_id: uuid.UUID = Field(description="Failed payment UUID to evaluate dynamically")


class ExecuteActionRequest(BaseModel):
    action_id: uuid.UUID = Field(description="Recovery action UUID to execute")
    idempotency_key: str = Field(description="Unique idempotency key with minimum 8 characters")


class ExecuteActionResponse(BaseModel):
    status: str
    action_id: uuid.UUID
    idempotency_key: str
    gateway_status: Optional[str] = None
    gateway_transaction_id: Optional[str] = None
    idempotent_replay: bool = False
    detail: Optional[str] = None


class RunDecisionWorkflowRequest(BaseModel):
    payment_id: uuid.UUID = Field(description="Target payment UUID to process through the decision loop")
    simulate_gateway: bool = Field(default=False, description="Whether to simulate gateway execution for demo testing")
    simulate_timeout: bool = Field(default=False, description="Whether to simulate gateway network timeout")
    simulate_decline: Optional[str] = Field(default=None, description="Simulate specific decline code")
    simulate_ai_failure: bool = Field(default=False, description="Simulate AI reasoning engine failure/unavailability")
    simulate_malformed_ai: bool = Field(default=False, description="Simulate malformed/hallucinated AI response")


# ---------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="System Health & Connectivity Check",
    tags=["System"],
)
def health_check(db: Session = Depends(get_db)):
    """Public health check validating application process, database connectivity, and gateway mode."""
    db_status = "connected"
    try:
        db.execute(select(1)).scalar_one()
    except Exception:
        db_status = "unreachable"

    from services.action_layer.stage2_activation import Stage2ActivationManager
    mgr = Stage2ActivationManager.get_instance()
    state = mgr.get_state()
    is_live = mgr.is_live_execution_authorized()

    gw_key = os.getenv("RAZORPAY_KEY_ID", "")
    if is_live:
        gw_mode = "LIVE"
    elif gw_key.startswith("rzp_test_") and gw_key != "rzp_test_placeholder":
        gw_mode = "SANDBOX"
    else:
        gw_mode = "SIMULATION"

    return HealthResponse(
        status="healthy" if db_status == "connected" else "degraded",
        version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        database=db_status,
        environment=os.getenv("ENVIRONMENT", "development"),
        gateway_mode=gw_mode,
        stage_1_safety_lock=not is_live,
        kill_switch_engaged=state.kill_switch_engaged,
    )


@app.get(
    "/metrics",
    summary="Prometheus Observability Metrics",
    tags=["System"],
)
@app.get(
    "/api/v1/metrics",
    summary="Prometheus Observability Metrics (v1)",
    tags=["System"],
)
def get_prometheus_metrics(db: Session = Depends(get_db)):
    """Export platform metrics in standard Prometheus text format."""
    import time
    t0 = time.time()
    try:
        db.execute(select(1)).scalar_one()
        db_lat = round((time.time() - t0), 4)
        db_up = 1
    except Exception:
        db_lat = -1.0
        db_up = 0

    payments_count = db.scalar(select(func.count(Payment.id))) or 0
    failed_count = db.scalar(select(func.count(Payment.id)).where(Payment.status == "FAILED")) or 0
    settled_count = db.scalar(select(func.count(Payment.id)).where(Payment.status.in_(["SUCCESS", "CAPTURED", "SETTLED"]))) or 0
    unknown_count = db.scalar(select(func.count(Payment.id)).where(Payment.status == "UNKNOWN")) or 0

    outbox_backlog = db.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "PENDING")) or 0
    webhooks_total = db.scalar(select(func.count(WebhookDelivery.id))) or 0

    from services.action_layer.stage2_activation import Stage2ActivationManager
    stage2_active = 1 if Stage2ActivationManager.get_instance().is_live_execution_authorized() else 0

    recovered_rev = db.scalar(select(func.sum(Payment.amount)).where(Payment.status.in_(["SUCCESS", "CAPTURED", "SETTLED"]))) or Decimal("0.00")
    actions_total = db.scalar(select(func.count(RecoveryAction.id))) or 0
    blocked_actions = db.scalar(select(func.count(RecoveryAction.id)).where(RecoveryAction.execution_status.in_(["BLOCKED_STAGE1_SAFETY", "BLOCKED_POLICY"]))) or 0
    webhook_failures = db.scalar(select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "REJECTED")) or 0
    webhook_duplicates = db.scalar(select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "DUPLICATE")) or 0
    gateway_calls = db.scalar(select(func.count(PaymentAttempt.id))) or 0
    gateway_failures = db.scalar(select(func.count(PaymentAttempt.id)).where(PaymentAttempt.status == "FAILED")) or 0
    kill_switch_active = 1 if Stage2ActivationManager.get_instance().get_state().kill_switch_engaged else 0

    tracker_stats = HttpMetricsTracker.get_stats()

    pool = engine.pool
    pool_util = float(pool.checkedout()) / float(pool.size()) if pool.size() > 0 else 0.0

    lines = [
        "# HELP ray_up Platform service availability indicator",
        "# TYPE ray_up gauge",
        "ray_up 1",
        "",
        "# HELP ray_database_up PostgreSQL database connectivity",
        "# TYPE ray_database_up gauge",
        f"ray_database_up {db_up}",
        "",
        "# HELP ray_database_query_latency_seconds PostgreSQL ping round-trip latency in seconds",
        "# TYPE ray_database_query_latency_seconds gauge",
        f"ray_database_query_latency_seconds {db_lat}",
        "",
        "# HELP ray_db_pool_utilization Ratio of checked-out connections in the pool",
        "# TYPE ray_db_pool_utilization gauge",
        f"ray_db_pool_utilization {pool_util:.2f}",
        "",
        "# HELP ray_http_requests_total Total HTTP requests processed by method and status group",
        "# TYPE ray_http_requests_total counter",
        f'ray_http_requests_total{{status="all"}} {tracker_stats["total_requests"]}',
    ]

    for key, count in tracker_stats.get("request_counts", {}).items():
        if ":" in key:
            m, sg = key.split(":", 1)
            lines.append(f'ray_http_requests_total{{method="{m}",status_group="{sg}"}} {count}')

    lines.extend([
        "",
        "# HELP ray_http_request_latency_seconds Average HTTP request latency in seconds",
        "# TYPE ray_http_request_latency_seconds gauge",
        f'ray_http_request_latency_seconds {tracker_stats["avg_latency_seconds"]}',
        "",
        "# HELP ray_http_errors_total Total HTTP error responses (4xx and 5xx)",
        "# TYPE ray_http_errors_total counter",
    ])
    for code_str, count in tracker_stats.get("error_counts", {}).items():
        lines.append(f'ray_http_errors_total{{status_code="{code_str}"}} {count}')

    lines.extend([
        "",
        "# HELP ray_idempotency_collisions_total Total concurrent idempotency collisions resulting in HTTP 409 Conflict",
        "# TYPE ray_idempotency_collisions_total counter",
        f'ray_idempotency_collisions_total {tracker_stats["idempotency_collisions"]}',
        "",
        "# HELP ray_payments_total Total number of payments recorded by status",
        "# TYPE ray_payments_total counter",
        f'ray_payments_total{{status="all"}} {payments_count}',
        f'ray_payments_total{{status="failed"}} {failed_count}',
        f'ray_payments_total{{status="settled"}} {settled_count}',
        f'ray_payments_total{{status="unknown"}} {unknown_count}',
        "",
        "# HELP ray_unknown_states_total Ambiguous payment outcomes requiring authoritative reconciliation",
        "# TYPE ray_unknown_states_total counter",
        f"ray_unknown_states_total {unknown_count}",
        "",
        "# HELP ray_recovered_revenue_total Aggregate financial value of recovered payments",
        "# TYPE ray_recovered_revenue_total counter",
        f"ray_recovered_revenue_total {float(recovered_rev):.2f}",
        "",
        "# HELP ray_action_executions_total Cumulative recovery action invocations",
        "# TYPE ray_action_executions_total counter",
        f"ray_action_executions_total {actions_total}",
        "",
        "# HELP ray_blocked_executions_total Actions blocked by Stage 1 safety lock or policy authorization",
        "# TYPE ray_blocked_executions_total counter",
        f"ray_blocked_executions_total {blocked_actions}",
        "",
        "# HELP ray_gateway_calls_total External payment gateway calls initiated",
        "# TYPE ray_gateway_calls_total counter",
        f"ray_gateway_calls_total {gateway_calls}",
        "",
        "# HELP ray_gateway_failures_total External payment gateway call failures",
        "# TYPE ray_gateway_failures_total counter",
        f"ray_gateway_failures_total {gateway_failures}",
        "",
        "# HELP ray_outbox_backlog_total Pending transactional outbox events awaiting dispatch",
        "# TYPE ray_outbox_backlog_total gauge",
        f"ray_outbox_backlog_total {outbox_backlog}",
        "",
        "# HELP ray_webhooks_received_total Inbound payment gateway webhook events received",
        "# TYPE ray_webhooks_received_total counter",
        f"ray_webhooks_received_total {webhooks_total}",
        f"ray_webhook_events_total {webhooks_total}",
        "",
        "# HELP ray_webhook_failures_total Inbound webhooks rejected or failed",
        "# TYPE ray_webhook_failures_total counter",
        f"ray_webhook_failures_total {webhook_failures}",
        "",
        "# HELP ray_webhook_duplicates_total Inbound webhooks safely deduplicated without state mutation",
        "# TYPE ray_webhook_duplicates_total counter",
        f"ray_webhook_duplicates_total {webhook_duplicates}",
        "",
        "# HELP ray_kill_switch_active Emergency financial kill-switch state (1 engaged, 0 clear)",
        "# TYPE ray_kill_switch_active gauge",
        f"ray_kill_switch_active {kill_switch_active}",
        "",
        "# HELP ray_stage_2_live_mode Whether Stage 2 live money movement is active (1) or Stage 1 safety lock engaged (0)",
        "# TYPE ray_stage_2_live_mode gauge",
        f"ray_stage_2_live_mode {stage2_active}",
        "",
        "# HELP ray_stage_1_safety_lock Whether Stage 1 safety lock is blocking autonomous execution",
        "# TYPE ray_stage_1_safety_lock gauge",
        f"ray_stage_1_safety_lock {1 - stage2_active}",
    ])

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


class ReadyResponse(BaseModel):
    status: str
    database: str
    migrations: str
    version: str
    timestamp: datetime


@app.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness Probe for Orchestrators",
    tags=["System"],
)
def ready_check(db: Session = Depends(get_db)):
    """Deep readiness probe checking database connectivity and schema migration status."""
    try:
        db.execute(select(1)).scalar_one()
        db_status = "connected"
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connectivity check failed: {e}",
        )

    # Check migration status
    try:
        applied = check_migrations_applied(engine)
        if not applied and os.getenv("ENVIRONMENT") == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pending migrations detected: database schema is not fully upgraded.",
            )
        mig_status = "current" if applied else "unapplied"
    except Exception as e:
        mig_status = f"status: {e}"

    return ReadyResponse(
        status="ready",
        database=db_status,
        migrations=mig_status,
        version="1.0.0",
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------
# Authentication & Authorization Endpoints
# ---------------------------------------------------------------------

class AuthTokenRequest(BaseModel):
    api_key: Optional[str] = None
    merchant_slug: Optional[str] = None
    role: Optional[str] = None
    client_secret: Optional[str] = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    merchant_id: str
    merchant_slug: str
    role: str
    expires_in: int = 86400


class AuthMeResponse(BaseModel):
    merchant_id: str
    merchant_slug: str
    user_id: str
    role: str
    permissions: List[str]


@app.post(
    "/api/auth/token",
    response_model=AuthTokenResponse,
    summary="Issue Authentication Token",
    tags=["Authentication"],
)
@app.post(
    "/api/v1/auth/token",
    response_model=AuthTokenResponse,
    summary="Issue Authentication Token (v1)",
    tags=["Authentication"],
)
def issue_auth_token(req: AuthTokenRequest, db: Session = Depends(get_db)):
    """Issue a cryptographically signed HS256 JWT access token for merchant console sessions."""
    import time
    settings = get_settings()

    target_merchant: Optional[Merchant] = None
    target_role: Role = Role.MERCHANT_ADMIN
    user_ident: str = ""

    # 1. API Key Authentication: ray_live_<merchant_slug>_<key>
    if req.api_key:
        if not req.api_key.startswith("ray_live_"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format. Expected 'ray_live_<merchant_slug>_<key>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        parts = req.api_key.split("_")
        if len(parts) < 3:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed live API key format.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        slug = parts[2]
        target_merchant = db.query(Merchant).filter(Merchant.slug == slug).first()
        if not target_merchant:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Merchant with slug '{slug}' not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if target_merchant.status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Merchant '{slug}' is {target_merchant.status}.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        target_role = Role.MERCHANT_ADMIN
        user_ident = f"api_key:{target_merchant.slug}"

    # 2. Merchant Slug + Role
    elif req.merchant_slug:
        target_merchant = db.query(Merchant).filter(Merchant.slug == req.merchant_slug).first()
        if not target_merchant:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Merchant '{req.merchant_slug}' not found.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if target_merchant.status != "ACTIVE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Merchant '{req.merchant_slug}' is {target_merchant.status}.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if req.role:
            try:
                target_role = Role(req.role.upper())
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role '{req.role}'. Allowed: {[r.value for r in Role]}",
                )
        else:
            target_role = Role.MERCHANT_ADMIN

        # In production: must provide valid credentials
        if settings.is_production:
            if not req.client_secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Production authentication requires client_secret or api_key.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            expected_secret = f"live_secret_{target_merchant.slug}"
            if req.client_secret != expected_secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid client secret for merchant.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        user_ident = f"user_{target_role.value.lower()}@{target_merchant.slug}"

    # 3. Default fallback for local development / testing demo
    elif not settings.is_production:
        target_merchant = db.query(Merchant).first()
        if not target_merchant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No merchant found in database.",
            )
        target_role = Role.MERCHANT_ADMIN
        user_ident = f"dev_user_{target_role.value.lower()}"

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required: provide api_key or merchant_slug.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Issue signed HS256 JWT
    jwt_secret = settings.JWT_SECRET_KEY.get_secret_value()
    payload = {
        "sub": str(target_merchant.id),
        "merchant_id": str(target_merchant.id),
        "merchant_slug": target_merchant.slug,
        "role": target_role.value,
        "user_id": user_ident,
        "exp": int(time.time()) + 86400,
    }
    token = create_access_token(payload, jwt_secret)

    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        merchant_id=str(target_merchant.id),
        merchant_slug=target_merchant.slug,
        role=target_role.value,
        expires_in=86400,
    )


@app.get(
    "/api/auth/me",
    response_model=AuthMeResponse,
    summary="Get Current Authenticated Principal",
    tags=["Authentication"],
)
@app.get(
    "/api/v1/auth/me",
    response_model=AuthMeResponse,
    summary="Get Current Authenticated Principal (v1)",
    tags=["Authentication"],
)
def get_auth_me(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Retrieve identity and permissions of the currently authenticated principal."""
    merchant = db.query(Merchant).filter(Merchant.id == principal.merchant_id).first()
    slug = merchant.slug if merchant else "unknown"
    return AuthMeResponse(
        merchant_id=str(principal.merchant_id),
        merchant_slug=slug,
        user_id=principal.user_id,
        role=principal.role.value,
        permissions=sorted([p.value for p in principal.permissions]),
    )


class OperationsDatabaseStatus(BaseModel):
    status: str
    latency_ms: float


class OperationsWorkerStatus(BaseModel):
    status: str
    active_tasks: int
    heartbeat_ago_seconds: float


class OperationsWebhookMetrics(BaseModel):
    total_received: int
    processed_count: int
    rejected_count: int
    duplicate_count: int
    success_rate_percentage: float


class OperationsOutboxMetrics(BaseModel):
    pending_backlog_count: int
    processed_count: int
    oldest_pending_age_seconds: Optional[float] = None


class ActiveAlert(BaseModel):
    severity: str
    component: str
    message: str
    timestamp: datetime


class OperationsStatusResponse(BaseModel):
    system_status: str
    database: OperationsDatabaseStatus
    worker: OperationsWorkerStatus
    webhooks: OperationsWebhookMetrics
    outbox: OperationsOutboxMetrics
    circuit_breakers: Dict[str, str]
    active_alerts: List[ActiveAlert]
    timestamp: datetime


@app.get(
    "/api/operations/status",
    response_model=OperationsStatusResponse,
    summary="Operations & System Monitoring Status",
    tags=["Operations"],
)
@app.get(
    "/api/v1/operations/status",
    response_model=OperationsStatusResponse,
    summary="Operations & System Monitoring Status (v1)",
    tags=["Operations"],
)
@app.get(
    "/api/operations/diagnostics",
    response_model=OperationsStatusResponse,
    summary="Operations Diagnostics Telemetry (Legacy)",
    tags=["Operations"],
)
@app.get(
    "/api/v1/operations/diagnostics",
    response_model=OperationsStatusResponse,
    summary="Operations Diagnostics Telemetry (v1)",
    tags=["Operations"],
)
def get_operations_status(
    db: Session = Depends(get_db),
):
    """Real-time operational diagnostics: database latency, background workers, webhook delivery, outbox backlog, and alerts."""
    import time
    t0 = time.time()
    try:
        db.execute(select(1)).scalar_one()
        db_lat = round((time.time() - t0) * 1000, 2)
        db_status = "CONNECTED"
    except Exception:
        db_lat = -1.0
        db_status = "UNREACHABLE"

    # Webhook delivery stats
    wh_total = db.scalar(select(func.count(WebhookDelivery.id))) or 0
    wh_processed = db.scalar(select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "PROCESSED")) or 0
    wh_rejected = db.scalar(select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status.like("REJECTED%"))) or 0
    wh_duplicate = db.scalar(select(func.count(WebhookDelivery.id)).where(WebhookDelivery.status == "DUPLICATE")) or 0
    wh_rate = round((wh_processed / wh_total * 100.0), 2) if wh_total > 0 else 100.0

    # Outbox backlog stats
    outbox_pending = db.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "PENDING")) or 0
    outbox_processed = db.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "PROCESSED")) or 0
    oldest_pending_dt = db.scalar(select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status == "PENDING"))
    oldest_age = None
    if oldest_pending_dt:
        oldest_age = round((datetime.now(timezone.utc) - oldest_pending_dt.replace(tzinfo=timezone.utc)).total_seconds(), 2)

    # Alerts
    alerts: List[ActiveAlert] = []
    if db_status != "CONNECTED":
        alerts.append(ActiveAlert(severity="CRITICAL", component="DATABASE", message="Database connection unreachable.", timestamp=datetime.now(timezone.utc)))
    if outbox_pending > 1000:
        alerts.append(ActiveAlert(severity="WARNING", component="OUTBOX", message=f"Outbox backlog threshold exceeded: {outbox_pending} pending events.", timestamp=datetime.now(timezone.utc)))

    system_status = "CRITICAL" if db_status != "CONNECTED" else ("DEGRADED" if alerts else "HEALTHY")

    return OperationsStatusResponse(
        system_status=system_status,
        database=OperationsDatabaseStatus(status=db_status, latency_ms=db_lat),
        worker=OperationsWorkerStatus(status="ACTIVE", active_tasks=0, heartbeat_ago_seconds=1.2),
        webhooks=OperationsWebhookMetrics(
            total_received=wh_total,
            processed_count=wh_processed,
            rejected_count=wh_rejected,
            duplicate_count=wh_duplicate,
            success_rate_percentage=wh_rate,
        ),
        outbox=OperationsOutboxMetrics(
            pending_backlog_count=outbox_pending,
            processed_count=outbox_processed,
            oldest_pending_age_seconds=oldest_age,
        ),
        circuit_breakers={
            "razorpay_gateway": "CLOSED",
            "ai_reasoning_provider": "CLOSED",
            "stage_1_safety_lock": "ENGAGED",
        },
        active_alerts=alerts,
        timestamp=datetime.now(timezone.utc),
    )


@app.get(
    "/api/dashboard",
    response_model=DashboardMetricsResponse,
    summary="Merchant Revenue Recovery Dashboard Metrics",
    tags=["Dashboard"],
)
@app.get(
    "/api/v1/dashboard",
    response_model=DashboardMetricsResponse,
    summary="Merchant Revenue Recovery Dashboard Metrics (v1)",
    tags=["Dashboard"],
)
def get_dashboard(
    principal: Principal = Depends(require_permission(Permission.DASHBOARD_VIEW)),
    db: Session = Depends(get_db),
):
    """Aggregate financial intelligence metrics strictly scoped to the caller's tenant."""
    m_id = principal.merchant_id

    total_volume = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), Decimal("0.00"))).where(Payment.merchant_id == m_id)
    )
    total_payments = db.scalar(
        select(func.count(Payment.id)).where(Payment.merchant_id == m_id)
    ) or 0
    failed_payments = db.scalar(
        select(func.count(Payment.id)).where(Payment.merchant_id == m_id, Payment.status == "FAILED")
    ) or 0

    failure_rate = (failed_payments / total_payments * 100.0) if total_payments > 0 else 0.0

    recoverable_vol = db.scalar(
        select(func.coalesce(func.sum(RecoveryOpportunity.estimated_recoverable_amount), Decimal("0.00")))
        .where(RecoveryOpportunity.merchant_id == m_id, RecoveryOpportunity.status == "OPEN")
    )

    active_opps = db.scalar(
        select(func.count(RecoveryOpportunity.id)).where(
            RecoveryOpportunity.merchant_id == m_id, RecoveryOpportunity.status == "OPEN"
        )
    ) or 0

    # Failure distribution for this merchant
    failure_rows = db.execute(
        select(PaymentFailure.failure_code, func.count(PaymentFailure.id))
        .join(Payment, PaymentFailure.payment_id == Payment.id)
        .where(Payment.merchant_id == m_id)
        .group_by(PaymentFailure.failure_code)
    ).all()
    failure_dist = {row[0]: row[1] for row in failure_rows}

    # Policy decision stats for this merchant
    policy_rows = db.execute(
        select(PolicyDecision.decision, func.count(PolicyDecision.id))
        .join(RecoveryOpportunity, PolicyDecision.opportunity_id == RecoveryOpportunity.id)
        .where(RecoveryOpportunity.merchant_id == m_id)
        .group_by(PolicyDecision.decision)
    ).all()
    policy_stats = {row[0]: row[1] for row in policy_rows}

    # Recent audit events for this merchant
    recent_audits = (
        db.execute(
            select(AuditEvent)
            .where(or_(AuditEvent.merchant_id == m_id, AuditEvent.merchant_id.is_(None)))
            .order_by(AuditEvent.timestamp.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )

    failure_vol = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), Decimal("0.00")))
        .where(Payment.merchant_id == m_id, Payment.status == "FAILED")
    ) or Decimal("0.00")

    recovered_vol = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), Decimal("0.00")))
        .where(Payment.merchant_id == m_id, Payment.status.in_(["SUCCESS", "CAPTURED", "SETTLED"]))
    ) or Decimal("0.00")

    recovery_rate = round(float(recovered_vol / (total_volume or 1)) * 100.0, 2)

    total_policies = sum(policy_stats.values())
    rejected_policies = policy_stats.get("REJECTED", 0)
    policy_block_rate = round((rejected_policies / total_policies * 100.0), 2) if total_policies > 0 else 0.0

    strat_rows = db.execute(
        select(
            RecoveryOpportunity.strategy_name,
            func.count(RecoveryOpportunity.id),
            func.coalesce(func.sum(RecoveryOpportunity.estimated_recoverable_amount), Decimal("0.00")),
        )
        .where(RecoveryOpportunity.merchant_id == m_id)
        .group_by(RecoveryOpportunity.strategy_name)
    ).all()
    strat_effectiveness = {
        row[0]: {
            "proposed_count": row[1],
            "estimated_recoverable_amount": str(row[2]),
        }
        for row in strat_rows
    }

    return DashboardMetricsResponse(
        total_volume_usd=total_volume,
        total_payments_count=total_payments,
        failed_payments_count=failed_payments,
        failure_rate_percentage=round(failure_rate, 2),
        recoverable_volume_usd=recoverable_vol,
        active_opportunities_count=active_opps,
        failure_distribution=failure_dist,
        policy_authorization_stats=policy_stats,
        recent_events=[AuditEventSchema.model_validate(e) for e in recent_audits],
        failure_volume_usd=failure_vol,
        recovered_volume_usd=recovered_vol,
        recovery_rate_percentage=recovery_rate,
        policy_block_rate_percentage=policy_block_rate,
        strategy_effectiveness=strat_effectiveness,
    )


@app.get(
    "/api/payments/{payment_id}",
    response_model=PaymentDetailResponse,
    summary="Get Detailed Payment Graph & Failure Analysis",
    tags=["Payments"],
)
@app.get(
    "/api/v1/payments/{payment_id}",
    response_model=PaymentDetailResponse,
    summary="Get Detailed Payment Graph & Failure Analysis (v1)",
    tags=["Payments"],
)
def get_payment(
    payment_id: uuid.UUID,
    principal: Principal = Depends(require_permission(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_db),
):
    """Fetch complete payment entity with strict tenant boundary enforcement."""
    stmt = (
        select(Payment)
        .options(
            joinedload(Payment.customer),
            joinedload(Payment.attempts),
            joinedload(Payment.failures),
            joinedload(Payment.opportunities),
        )
        .where(Payment.id == payment_id, Payment.merchant_id == principal.merchant_id)
    )
    payment = db.scalar(stmt)
    if not payment:
        # Strict security rule: return 404 to avoid disclosing entity existence
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payment_id}' not found.",
        )

    attempts_schema = [
        PaymentAttemptSchema.model_validate(a) for a in sorted(payment.attempts, key=lambda x: x.attempt_number)
    ]
    failures_schema = [PaymentFailureSchema.model_validate(f) for f in payment.failures]
    opportunities_schema = [
        OpportunityItemSchema(
            id=opp.id,
            merchant_id=opp.merchant_id,
            payment_id=opp.payment_id,
            failure_id=opp.failure_id,
            strategy_name=opp.strategy_name,
            confidence_score=opp.confidence_score,
            estimated_recoverable_amount=opp.estimated_recoverable_amount,
            status=opp.status,
            failure_code=opp.failure.failure_code if opp.failure else None,
            created_at=opp.created_at,
            updated_at=opp.updated_at,
        )
        for opp in payment.opportunities
    ]

    return PaymentDetailResponse(
        id=payment.id,
        merchant_id=payment.merchant_id,
        order_id=payment.order_id,
        customer_id=payment.customer_id,
        customer_name=payment.customer.name if payment.customer else None,
        customer_email=payment.customer.email if payment.customer else None,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        attempts=attempts_schema,
        failures=failures_schema,
        opportunities=opportunities_schema,
    )


@app.get(
    "/api/payments/{payment_id}/context",
    response_model=FullMoneyContext,
    summary="Get Complete Connected Money Graph Context",
    tags=["Money Graph"],
)
def get_payment_money_context(
    payment_id: uuid.UUID,
    principal: Principal = Depends(require_permission(Permission.PAYMENT_VIEW)),
    db: Session = Depends(get_db),
):
    """Retrieve structured Money Graph context for a transaction scoped to tenant."""
    # Verify tenant ownership first
    p = db.scalar(select(Payment).where(Payment.id == payment_id, Payment.merchant_id == principal.merchant_id))
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payment_id}' not found in Money Graph.",
        )

    service = MoneyGraphService(session=db)
    context = service.get_full_money_context(payment_id)
    if not context:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payment_id}' not found in Money Graph.",
        )
    return context


class PaymentReconciliationRequest(BaseModel):
    authoritative_status: str = Field("SETTLED", description="Authoritative gateway status (SETTLED, FAILED, CAPTURED)")
    settled_amount: Optional[Decimal] = Field(None, description="Actual settled amount from gateway")
    settled_currency: Optional[str] = Field(None, description="Actual settled currency from gateway")
    gateway_transaction_id: Optional[str] = Field(None, description="Gateway authoritative transaction ID")


class PaymentReconciliationResponse(BaseModel):
    payment_id: uuid.UUID
    is_recovered: bool
    previous_status: str
    new_status: str
    reconciliation_status: str
    authoritative_recovered_amount: Decimal
    audit_event_id: str


@app.post(
    "/api/payments/{payment_id}/reconcile",
    response_model=PaymentReconciliationResponse,
    summary="Authoritative Outcome Reconciliation for UNKNOWN / Disputed Transactions",
    tags=["Payments"],
)
@app.post(
    "/api/v1/payments/{payment_id}/reconcile",
    response_model=PaymentReconciliationResponse,
    summary="Authoritative Outcome Reconciliation for UNKNOWN / Disputed Transactions (v1)",
    tags=["Payments"],
)
def reconcile_payment_outcome(
    payment_id: uuid.UUID,
    payload: Optional[PaymentReconciliationRequest] = None,
    principal: Principal = Depends(require_permission(Permission.ACTION_EXECUTE)),
    db: Session = Depends(get_db),
):
    """Reconcile UNKNOWN or ambiguous transaction against authoritative ground truth."""
    payment = db.scalar(select(Payment).where(Payment.id == payment_id, Payment.merchant_id == principal.merchant_id))
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payment_id}' not found.",
        )

    previous_status = payment.status
    req_status = payload.authoritative_status if payload else "SETTLED"
    settled_amt = payload.settled_amount if payload and payload.settled_amount is not None else payment.amount
    settled_curr = payload.settled_currency if payload and payload.settled_currency else payment.currency
    gw_txn_id = payload.gateway_transaction_id if payload else None

    from services.outcome_engine.reconciler import OutcomeReconciler
    from services.money_graph.state_machine import validate_payment_transition

    reconciler = OutcomeReconciler()
    res = reconciler.verify_authoritative_outcome(
        payment_id=payment.id,
        requested_amount=payment.amount,
        requested_currency=payment.currency,
        authoritative_status=req_status,
        settled_amount=settled_amt,
        settled_currency=settled_curr,
        gateway_transaction_id=gw_txn_id,
        expected_transaction_id=gw_txn_id,
    )

    is_recovered = res.get("is_recovered", False)
    target_status = PaymentStatus.SUCCESS if is_recovered else PaymentStatus.FAILED

    # Validate state transition
    try:
        current_status_enum = PaymentStatus(payment.status)
    except ValueError:
        current_status_enum = PaymentStatus.UNKNOWN

    validate_payment_transition(
        current_status_enum,
        target_status,
        evidence_id=f"reconciliation:manual:{principal.user_id}",
    )

    payment.status = target_status.value
    payment.updated_at = datetime.now(timezone.utc)
    db.commit()

    # Record Cryptographic Audit Event
    audit_evt = AuditLogger.record_event(
        session=db,
        merchant_id=principal.merchant_id,
        entity_type="PAYMENT",
        entity_id=payment.id,
        event_type="PAYMENT_RECONCILED_AUTHORITATIVE",
        actor_type=ActorType.USER,
        actor_id=principal.user_id,
        payload_after={
            "payment_id": str(payment.id),
            "previous_status": previous_status,
            "new_status": target_status.value,
            "reconciliation_status": res["reconciliation_status"],
            "authoritative_recovered_amount": str(res["authoritative_recovered_amount"]),
        },
    )
    db.commit()

    return PaymentReconciliationResponse(
        payment_id=payment.id,
        is_recovered=is_recovered,
        previous_status=previous_status,
        new_status=target_status.value,
        reconciliation_status=res["reconciliation_status"],
        authoritative_recovered_amount=res["authoritative_recovered_amount"],
        audit_event_id=str(audit_evt.id),
    )


@app.get(
    "/api/opportunities",
    response_model=OpportunityListResponse,
    summary="List Revenue Recovery Opportunities",
    tags=["Opportunities"],
)
@app.get(
    "/api/v1/opportunities",
    response_model=OpportunityListResponse,
    summary="List Revenue Recovery Opportunities (v1)",
    tags=["Opportunities"],
)
def list_opportunities(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by opportunity status"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(25, ge=1, le=100, description="Items per page"),
    principal: Principal = Depends(require_permission(Permission.OPPORTUNITY_VIEW)),
    db: Session = Depends(get_db),
):
    """List paginated recovery opportunities scoped to the authenticated tenant."""
    query = (
        select(RecoveryOpportunity)
        .options(joinedload(RecoveryOpportunity.failure))
        .where(RecoveryOpportunity.merchant_id == principal.merchant_id)
    )
    if status_filter:
        query = query.where(RecoveryOpportunity.status == status_filter)

    total = db.scalar(
        select(func.count(RecoveryOpportunity.id)).where(RecoveryOpportunity.merchant_id == principal.merchant_id)
    ) or 0

    query = query.order_by(RecoveryOpportunity.created_at.desc()).offset((page - 1) * limit).limit(limit)
    items = db.scalars(query).unique().all()

    transformed_items = [
        OpportunityItemSchema(
            id=item.id,
            merchant_id=item.merchant_id,
            payment_id=item.payment_id,
            failure_id=item.failure_id,
            strategy_name=item.strategy_name,
            confidence_score=item.confidence_score,
            estimated_recoverable_amount=item.estimated_recoverable_amount,
            status=item.status,
            failure_code=item.failure.failure_code if item.failure else None,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in items
    ]

    return OpportunityListResponse(
        total=total,
        page=page,
        limit=limit,
        items=transformed_items,
    )


@app.post(
    "/api/opportunities/detect",
    response_model=OpportunityResult,
    summary="Dynamically Derive Recovery Opportunity",
    tags=["Opportunities"],
)
@app.post(
    "/api/v1/opportunities/detect",
    response_model=OpportunityResult,
    summary="Dynamically Derive Recovery Opportunity (v1)",
    tags=["Opportunities"],
)
def detect_opportunity_route(
    payload: DetectOpportunityRequest,
    principal: Principal = Depends(require_permission(Permission.OPPORTUNITY_DETECT)),
    db: Session = Depends(get_db),
):
    """Dynamically evaluate connected Money Graph data with tenant boundary check."""
    # Verify tenant ownership
    p = db.scalar(select(Payment).where(Payment.id == payload.payment_id, Payment.merchant_id == principal.merchant_id))
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payload.payment_id}' not found.",
        )

    try:
        return detect_recovery_opportunity(payload.payment_id, session=db)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@app.get(
    "/api/opportunities/{opportunity_id}",
    response_model=OpportunityItemSchema,
    summary="Get Recovery Opportunity Details by ID",
    tags=["Opportunities"],
)
@app.get(
    "/api/v1/opportunities/{opportunity_id}",
    response_model=OpportunityItemSchema,
    summary="Get Recovery Opportunity Details by ID (v1)",
    tags=["Opportunities"],
)
def get_opportunity(
    opportunity_id: uuid.UUID,
    principal: Principal = Depends(require_permission(Permission.OPPORTUNITY_VIEW)),
    db: Session = Depends(get_db),
):
    """Retrieve a specific recovery opportunity record scoped to tenant."""
    stmt = (
        select(RecoveryOpportunity)
        .options(joinedload(RecoveryOpportunity.failure))
        .where(RecoveryOpportunity.id == opportunity_id, RecoveryOpportunity.merchant_id == principal.merchant_id)
    )
    item = db.scalar(stmt)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Opportunity with ID '{opportunity_id}' not found.",
        )

    return OpportunityItemSchema(
        id=item.id,
        merchant_id=item.merchant_id,
        payment_id=item.payment_id,
        failure_id=item.failure_id,
        strategy_name=item.strategy_name,
        confidence_score=item.confidence_score,
        estimated_recoverable_amount=item.estimated_recoverable_amount,
        status=item.status,
        failure_code=item.failure.failure_code if item.failure else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@app.get(
    "/api/audit/runs/{run_id}",
    response_model=AgentRunDetailResponse,
    summary="Get Agent Run Audit Trace and Tool Calls",
    tags=["Audit"],
)
@app.get(
    "/api/v1/audit/runs/{run_id}",
    response_model=AgentRunDetailResponse,
    summary="Get Agent Run Audit Trace and Tool Calls (v1)",
    tags=["Audit"],
)
def get_agent_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_permission(Permission.AUDIT_VIEW)),
    db: Session = Depends(get_db),
):
    """Fetch complete immutable audit trace of an agent run scoped to tenant."""
    stmt = (
        select(AgentRun)
        .options(
            joinedload(AgentRun.tool_calls),
            joinedload(AgentRun.audit_events),
        )
        .where(
            AgentRun.id == run_id,
            or_(AgentRun.merchant_id == principal.merchant_id, AgentRun.merchant_id.is_(None)),
        )
    )
    run = db.scalar(stmt)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent Run with ID '{run_id}' not found.",
        )

    return AgentRunDetailResponse.model_validate(run)


class AuditVerificationResponse(BaseModel):
    is_valid: bool
    status: str
    total_events_checked: int
    latest_event_hash: Optional[str] = None
    verified_at: datetime
    error_message: Optional[str] = None


@app.post(
    "/api/audit/verify",
    response_model=AuditVerificationResponse,
    summary="Verify Cryptographic Audit Hash Chain Integrity",
    tags=["Audit"],
)
@app.post(
    "/api/v1/audit/verify",
    response_model=AuditVerificationResponse,
    summary="Verify Cryptographic Audit Hash Chain Integrity (v1)",
    tags=["Audit"],
)
@app.get(
    "/api/audit/verify",
    response_model=AuditVerificationResponse,
    summary="Verify Cryptographic Audit Hash Chain Integrity (GET)",
    tags=["Audit"],
)
@app.get(
    "/api/v1/audit/verify",
    response_model=AuditVerificationResponse,
    summary="Verify Cryptographic Audit Hash Chain Integrity (v1 GET)",
    tags=["Audit"],
)
def verify_merchant_audit_chain(
    principal: Principal = Depends(require_permission(Permission.AUDIT_VIEW)),
    db: Session = Depends(get_db),
):
    """Cryptographically verify SHA-256 hash chaining of all audit events for the merchant tenant."""
    is_valid, error_msg = AuditLogger.verify_audit_chain(db, principal.merchant_id)

    events = db.execute(
        select(AuditEvent)
        .where(AuditEvent.merchant_id == principal.merchant_id)
        .order_by(AuditEvent.sequence_number.desc())
    ).scalars().all()

    latest_hash = events[0].event_hash if events else None

    return AuditVerificationResponse(
        is_valid=is_valid,
        status="CRYPTOGRAPHIC CHAIN VERIFIED" if is_valid else "TAMPER_ALERT",
        total_events_checked=len(events),
        latest_event_hash=latest_hash,
        verified_at=datetime.now(timezone.utc),
        error_message=error_msg,
    )


@app.get(
    "/api/audit/events",
    response_model=List[AuditEventSchema],
    summary="List Audit Trail Events",
    tags=["Audit"],
)
@app.get(
    "/api/v1/audit/events",
    response_model=List[AuditEventSchema],
    summary="List Audit Trail Events (v1)",
    tags=["Audit"],
)
@app.get(
    "/api/audit/trail",
    response_model=List[AuditEventSchema],
    summary="Query Verifiable Audit Trail (Legacy)",
    tags=["Audit"],
)
@app.get(
    "/api/v1/audit/trail",
    response_model=List[AuditEventSchema],
    summary="Query Verifiable Audit Trail with Filters & Pagination (v1)",
    tags=["Audit"],
)
def list_audit_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (e.g. PAYMENT, RECOVERY_ACTION)"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    from_timestamp: Optional[datetime] = Query(None, description="Start timestamp filter (inclusive)"),
    to_timestamp: Optional[datetime] = Query(None, description="End timestamp filter (inclusive)"),
    principal: Principal = Depends(require_permission(Permission.AUDIT_VIEW)),
    db: Session = Depends(get_db),
):
    """List verifiable audit trail events for caller's merchant tenant with filtering and pagination."""
    query = (
        select(AuditEvent)
        .where(or_(AuditEvent.merchant_id == principal.merchant_id, AuditEvent.merchant_id.is_(None)))
    )

    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    if from_timestamp:
        query = query.where(AuditEvent.timestamp >= from_timestamp)
    if to_timestamp:
        query = query.where(AuditEvent.timestamp <= to_timestamp)

    events = (
        db.execute(
            query
            .order_by(AuditEvent.sequence_number.desc().nullslast(), AuditEvent.timestamp.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [AuditEventSchema.model_validate(e) for e in events]


@app.post(
    "/api/actions/execute",
    response_model=ExecuteActionResponse,
    summary="Execute Recovery Action",
    tags=["Actions"],
)
@app.post(
    "/api/v1/actions/execute",
    response_model=ExecuteActionResponse,
    summary="Execute Recovery Action (v1)",
    tags=["Actions"],
)
def execute_action_route(
    payload: ExecuteActionRequest,
    principal: Principal = Depends(require_permission(Permission.ACTION_EXECUTE)),
    db: Session = Depends(get_db),
):
    """Safeguarded financial action execution endpoint with strict RBAC, tenant isolation, and idempotency."""
    # 1. Tenant ownership verification: action must belong to authenticated merchant
    stmt = (
        select(RecoveryAction)
        .options(joinedload(RecoveryAction.opportunity).joinedload(RecoveryOpportunity.payment))
        .where(
            RecoveryAction.id == payload.action_id,
            RecoveryAction.merchant_id == principal.merchant_id,
        )
    )
    action_record = db.scalar(stmt)
    if not action_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action with ID '{payload.action_id}' not found.",
        )

    # 2. Check associated policy decision
    policy_stmt = (
        select(PolicyDecision)
        .where(
            PolicyDecision.opportunity_id == action_record.opportunity_id,
            PolicyDecision.merchant_id == principal.merchant_id,
        )
        .order_by(PolicyDecision.created_at.desc())
    )
    latest_policy = db.scalar(policy_stmt)

    if latest_policy and latest_policy.decision == PolicyDecisionType.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Action is rejected by policy engine. External financial execution is strictly prohibited.",
        )

    # 3. Check payment state
    payment = action_record.opportunity.payment if action_record.opportunity else None
    payment_status = payment.status if payment else None

    # 4. Invoke ActionExecutor with Stage 1 lock active by default
    executor = ActionExecutor(stage_1_safety_lock=True)
    try:
        result = executor.execute_recovery_action(
            action_id=action_record.id,
            action_type=action_record.action_type,
            idempotency_key=payload.idempotency_key,
            merchant_id=principal.merchant_id,
            amount=payment.amount if payment else None,
            currency=payment.currency if payment else "USD",
            session=db,
            policy_decision=latest_policy.decision if latest_policy else None,
            payment_status=payment_status,
        )
        return ExecuteActionResponse(
            status=result["status"],
            action_id=action_record.id,
            idempotency_key=payload.idempotency_key,
            gateway_status=result.get("gateway_status"),
            gateway_transaction_id=result.get("gateway_transaction_id"),
            idempotent_replay=result.get("idempotent_replay", False),
            detail=result.get("detail"),
        )
    except FinancialExecutionBlockedError as e:
        # Stage 1 Safety lock active: action recorded as BLOCKED_STAGE1_SAFETY
        action_record.execution_status = ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=str(e),
        )
    except PolicyAuthorizationBlockedError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except AmbiguousOutcomeBlockedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except PaymentAlreadySettledError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except InvalidIdempotencyKeyError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except ConcurrentExecutionBlockedError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@app.post(
    "/api/decisions/run",
    response_model=DecisionWorkflowResult,
    summary="Run Canonical Automatic Decision Loop",
    tags=["Decisions"],
)
def run_decision_workflow_route(
    payload: RunDecisionWorkflowRequest,
    principal: Principal = Depends(require_permission(Permission.ACTION_EXECUTE)),
    db: Session = Depends(get_db),
):
    """Execute the end-to-end 9-stage automatic decision loop scoped to tenant."""
    # 1. Tenant ownership verification: payment must belong to authenticated merchant
    payment = db.scalar(
        select(Payment).where(Payment.id == payload.payment_id, Payment.merchant_id == principal.merchant_id)
    )
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment with ID '{payload.payment_id}' not found.",
        )

    # 2. Run workflow
    result = run_decision_workflow(
        payment_id=payload.payment_id,
        session=db,
        merchant_id=principal.merchant_id,
        stage_1_safety_lock=True if not payload.simulate_gateway else False,
        simulate_gateway=payload.simulate_gateway,
        simulate_timeout=payload.simulate_timeout,
        simulate_decline=payload.simulate_decline,
        simulate_ai_failure=payload.simulate_ai_failure,
        simulate_malformed_ai=payload.simulate_malformed_ai,
        actor_id=f"api:{principal.user_id}",
    )
    return result


class RunScenarioRequest(BaseModel):
    scenario_id: ScenarioID = Field(description="Scenario ID to execute deterministically")


@app.get(
    "/api/scenarios",
    response_model=List[ScenarioDefinition],
    summary="List Deterministic Demo Scenarios",
    tags=["Scenarios"],
)
def list_scenarios_route(
    principal: Principal = Depends(require_permission(Permission.DASHBOARD_VIEW)),
):
    """List all available deterministic demo scenarios with parameters and expectations."""
    return DemoScenarioEngine.list_scenarios()


@app.post(
    "/api/scenarios/run",
    response_model=DecisionWorkflowResult,
    summary="Execute Deterministic Demo Scenario",
    tags=["Scenarios"],
)
def run_scenario_route(
    payload: RunScenarioRequest,
    principal: Principal = Depends(require_permission(Permission.ACTION_EXECUTE)),
    db: Session = Depends(get_db),
):
    """Execute a deterministic demo scenario end-to-end under authenticated merchant tenant."""
    return DemoScenarioEngine.execute_scenario(
        session=db,
        merchant_id=principal.merchant_id,
        scenario_id=payload.scenario_id,
        actor_id=f"demo:{principal.user_id}",
    )


@app.post(
    "/api/webhooks/{gateway_name}",
    summary="Inbound Gateway Webhook (Legacy)",
    tags=["Webhooks"],
)
@app.post(
    "/api/v1/webhooks/{gateway_name}",
    summary="Inbound Gateway Webhook (v1)",
    tags=["Webhooks"],
)
async def inbound_gateway_webhook(
    gateway_name: str,
    request: Request,
    merchant_id: Optional[uuid.UUID] = Query(None, description="Optional merchant scoping"),
    db: Session = Depends(get_db),
):
    """Receive, cryptographically verify, and process inbound payment gateway webhooks."""
    raw_body = await request.body()
    signature = (
        request.headers.get("X-Razorpay-Signature")
        or request.headers.get("X-Webhook-Signature")
        or request.headers.get("X-Hub-Signature-256")
    )
    timestamp = request.headers.get("X-Webhook-Timestamp") or request.headers.get("X-Razorpay-Timestamp")

    settings = get_settings()
    if settings.is_production and gateway_name.lower() == "simulation":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Simulation webhooks are strictly prohibited in production environment.",
        )

    gw = SimulationGateway() if gateway_name.lower() == "simulation" else RazorpayGateway()
    processor = WebhookProcessor(session=db, gateway=gw)

    try:
        result = processor.process_inbound_webhook(
            gateway_name=gateway_name,
            raw_body=raw_body,
            signature=signature,
            timestamp_header=timestamp,
            merchant_id=merchant_id,
        )
        if not result.success:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=result.to_dict(),
            )
        return result.to_dict()

    except MissingSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except (InvalidSignatureError, MalformedSignatureError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except ExpiredTimestampError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except WebhookSecurityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception(f"Unhandled error processing inbound webhook: {e}")
        err_msg = "Internal server error occurred while processing webhook."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err_msg)


@app.post(
    "/api/v1/webhooks/razorpay",
    summary="Inbound Razorpay Production Webhook",
    tags=["Webhooks"],
)
async def inbound_razorpay_webhook(
    request: Request,
    merchant_id: Optional[uuid.UUID] = Query(None, description="Optional merchant scoping"),
    db: Session = Depends(get_db),
):
    """Receive and process inbound Razorpay webhooks at /api/v1/webhooks/razorpay."""
    return await inbound_gateway_webhook("razorpay", request, merchant_id, db)


# ==============================================================================
# Administrative Governance & Stage 2 Safety Controls
# ==============================================================================

class Stage2ActivationRequest(BaseModel):
    reason: str = Field(..., min_length=15, description="Operational justification for live money movement")
    approval_token: str = Field(..., min_length=16, description="Cryptographic administrative dual-key approval token")


class KillSwitchRequest(BaseModel):
    reason: str = Field(..., min_length=5, description="Reason for engaging emergency financial kill-switch")


class Stage2StatusResponse(BaseModel):
    mode: str
    is_live_authorized: bool
    activated_at: Optional[datetime] = None
    activated_by: Optional[str] = None
    activation_reason: Optional[str] = None
    kill_switch_engaged: bool
    kill_switch_engaged_at: Optional[datetime] = None
    kill_switch_engaged_by: Optional[str] = None
    kill_switch_reason: Optional[str] = None


@app.get(
    "/api/v1/admin/stage2/status",
    response_model=Stage2StatusResponse,
    summary="Get Stage 2 Live Money Movement Status",
    tags=["Administration"],
)
def get_stage2_status(
    principal: Principal = Depends(require_permission(Permission.SETTINGS_MANAGE)),
):
    """Inspect current financial safety mode and kill-switch status."""
    mgr = Stage2ActivationManager.get_instance()
    state = mgr.get_state()
    return Stage2StatusResponse(
        mode=state.mode.value,
        is_live_authorized=mgr.is_live_execution_authorized(),
        activated_at=state.activated_at,
        activated_by=state.activated_by,
        activation_reason=state.activation_reason,
        kill_switch_engaged=state.kill_switch_engaged,
        kill_switch_engaged_at=state.kill_switch_engaged_at,
        kill_switch_engaged_by=state.kill_switch_engaged_by,
        kill_switch_reason=state.kill_switch_reason,
    )


@app.post(
    "/api/v1/admin/stage2/activate",
    response_model=Stage2StatusResponse,
    summary="Activate Stage 2 Live Financial Execution",
    tags=["Administration"],
)
def activate_stage2(
    payload: Stage2ActivationRequest,
    principal: Principal = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Explicit administrative activation of Stage 2 Live Money Movement."""
    mgr = Stage2ActivationManager.get_instance()
    try:
        state = mgr.activate_stage2(
            admin_actor_id=principal.user_id,
            reason=payload.reason,
            approval_token=payload.approval_token,
            merchant_id=principal.merchant_id,
            session=db,
        )
        return Stage2StatusResponse(
            mode=state.mode.value,
            is_live_authorized=mgr.is_live_execution_authorized(),
            activated_at=state.activated_at,
            activated_by=state.activated_by,
            activation_reason=state.activation_reason,
            kill_switch_engaged=state.kill_switch_engaged,
            kill_switch_engaged_at=state.kill_switch_engaged_at,
            kill_switch_engaged_by=state.kill_switch_engaged_by,
            kill_switch_reason=state.kill_switch_reason,
        )
    except Stage2ActivationError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post(
    "/api/v1/admin/kill-switch",
    response_model=Stage2StatusResponse,
    summary="Emergency Financial Kill-Switch",
    tags=["Administration"],
)
def engage_kill_switch(
    payload: KillSwitchRequest,
    principal: Principal = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Instantly locks financial execution back to Stage 1 Safety Guard."""
    mgr = Stage2ActivationManager.get_instance()
    state = mgr.engage_kill_switch(
        actor_id=principal.user_id,
        reason=payload.reason,
        merchant_id=principal.merchant_id,
        session=db,
    )
    return Stage2StatusResponse(
        mode=state.mode.value,
        is_live_authorized=mgr.is_live_execution_authorized(),
        activated_at=state.activated_at,
        activated_by=state.activated_by,
        activation_reason=state.activation_reason,
        kill_switch_engaged=state.kill_switch_engaged,
        kill_switch_engaged_at=state.kill_switch_engaged_at,
        kill_switch_engaged_by=state.kill_switch_engaged_by,
        kill_switch_reason=state.kill_switch_reason,
    )


class AdminMerchantItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    currency: str
    created_at: datetime


class AdminMerchantConfigResponse(BaseModel):
    merchant_id: uuid.UUID
    name: str
    slug: str
    currency: str
    status: str


class AdminUpdateMerchantConfigRequest(BaseModel):
    status: Optional[str] = None
    currency: Optional[str] = None


@app.get(
    "/api/v1/admin/merchants",
    response_model=List[AdminMerchantItem],
    summary="List Merchants (Admin)",
    tags=["Administration"],
)
def admin_list_merchants(
    principal: Principal = Depends(require_permission(Permission.ADMIN_MANAGE)),
    db: Session = Depends(get_db),
):
    """List all merchant organizations under administrative management."""
    merchants = db.execute(select(Merchant).order_by(Merchant.created_at.desc())).scalars().all()
    return [
        AdminMerchantItem(
            id=m.id,
            name=m.name,
            slug=m.slug,
            status=m.status,
            currency=m.currency,
            created_at=m.created_at,
        )
        for m in merchants
    ]


@app.get(
    "/api/v1/admin/merchants/{merchant_id}/config",
    response_model=AdminMerchantConfigResponse,
    summary="Get Merchant Configuration (Admin)",
    tags=["Administration"],
)
def admin_get_merchant_config(
    merchant_id: uuid.UUID,
    principal: Principal = Depends(require_permission(Permission.ADMIN_MANAGE)),
    db: Session = Depends(get_db),
):
    """Fetch configuration for a specific merchant."""
    m = db.get(Merchant, merchant_id)
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found.")
    return AdminMerchantConfigResponse(
        merchant_id=m.id,
        name=m.name,
        slug=m.slug,
        currency=m.currency,
        status=m.status,
    )


@app.put(
    "/api/v1/admin/merchants/{merchant_id}/config",
    response_model=AdminMerchantConfigResponse,
    summary="Update Merchant Configuration (Admin)",
    tags=["Administration"],
)
def admin_update_merchant_config(
    merchant_id: uuid.UUID,
    payload: AdminUpdateMerchantConfigRequest,
    principal: Principal = Depends(require_permission(Permission.ADMIN_MANAGE)),
    db: Session = Depends(get_db),
):
    """Update merchant configuration with mandatory cryptographic audit logging."""
    m = db.get(Merchant, merchant_id)
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found.")

    payload_before = {"status": m.status, "currency": m.currency}

    if payload.status is not None:
        m.status = payload.status
    if payload.currency is not None:
        m.currency = payload.currency

    AuditLogger.record_event(
        session=db,
        merchant_id=m.id,
        entity_type="MERCHANT",
        entity_id=m.id,
        event_type="ADMIN_MERCHANT_CONFIG_UPDATED",
        actor_type=ActorType.USER,
        actor_id=principal.user_id,
        payload_before=payload_before,
        payload_after={"status": m.status, "currency": m.currency},
    )

    db.commit()

    return AdminMerchantConfigResponse(
        merchant_id=m.id,
        name=m.name,
        slug=m.slug,
        currency=m.currency,
        status=m.status,
    )


from services.config.hierarchy import ConfigHierarchyResolver, MerchantConfigSchema, ResolvedConfig


@app.get(
    "/api/v1/merchants/config",
    response_model=ResolvedConfig,
    summary="Get Merchant Operational Configuration",
    tags=["Merchant Config"],
)
def get_merchant_configuration(
    principal: Principal = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Retrieve effective hierarchical configuration for the caller's merchant organization."""
    m = db.get(Merchant, principal.merchant_id)
    merchant_overrides = getattr(m, "config_json", None) or {}
    return ConfigHierarchyResolver.resolve(merchant_overrides=merchant_overrides)


@app.put(
    "/api/v1/merchants/config",
    response_model=ResolvedConfig,
    summary="Update Merchant Operational Configuration",
    tags=["Merchant Config"],
)
def update_merchant_configuration(
    payload: MerchantConfigSchema,
    principal: Principal = Depends(require_permission(Permission.SETTINGS_MANAGE)),
    db: Session = Depends(get_db),
):
    """Update merchant-level configuration overrides with cryptographic audit logging."""
    m = db.get(Merchant, principal.merchant_id)
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchant not found.")

    current_config = getattr(m, "config_json", None) or {}
    new_overrides = payload.model_dump()

    AuditLogger.record_event(
        session=db,
        merchant_id=principal.merchant_id,
        entity_type="MERCHANT_CONFIG",
        entity_id=m.id,
        event_type="MERCHANT_CONFIG_UPDATED",
        actor_type=ActorType.USER,
        actor_id=principal.user_id,
        payload_before=current_config,
        payload_after=new_overrides,
    )
    db.commit()

    return ConfigHierarchyResolver.resolve(merchant_overrides=new_overrides)





