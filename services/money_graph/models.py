"""RAY Merchant Money Intelligence Engine — Core Data Models

Principles:
- AI recommends.
- Deterministic policy authorizes.
- Deterministic action layer executes.
- Outcome verification confirms reality.
- Audit records everything.

Invariants:
- All primary keys use UUID identifiers.
- All timestamps are timezone-aware UTC.
- Every merchant-owned entity enforces multi-tenant merchant_id linkage.
- Financial actions support unique idempotency keys.
- Risk scores strictly adhere to risk_score in [0.0, 1.0].
- Audit records support cryptographic SHA-256 hash chaining.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    JSON,
    Float,
    types,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return current timestamp in UTC."""
    return datetime.now(timezone.utc)


class UTCDateTime(types.TypeDecorator):
    """DateTime type that guarantees timezone-aware UTC datetimes across SQLite and PostgreSQL."""
    impl = types.DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: Optional[datetime], dialect: Any) -> Optional[datetime]:
        if value is not None:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class Base(DeclarativeBase):
    """Base model class."""
    pass


class FailureCategory(str, enum.Enum):
    """Payment failure categories required by RAY specification."""
    BANK_TIMEOUT = "BANK_TIMEOUT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    ISSUER_DECLINED = "ISSUER_DECLINED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    NETWORK_ERROR = "NETWORK_ERROR"
    CARD_EXPIRED = "CARD_EXPIRED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"


class PaymentStatus(str, enum.Enum):
    """Comprehensive payment lifecycle state machine."""
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    SUCCESS = "SUCCESS"  # Settled successful payment
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"  # Ambiguous outcome awaiting gateway verification
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    CHARGEBACK = "CHARGEBACK"
    CANCELLED = "CANCELLED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OpportunityStatus(str, enum.Enum):
    OPEN = "OPEN"
    EVALUATING = "EVALUATING"
    ACTION_PLANNED = "ACTION_PLANNED"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"
    DISMISSED = "DISMISSED"


class ActionType(str, enum.Enum):
    SMART_RETRY = "SMART_RETRY"
    ROUTING_FALLBACK = "ROUTING_FALLBACK"
    CUSTOMER_OUTREACH = "CUSTOMER_OUTREACH"
    AUTH_REPAIR = "AUTH_REPAIR"


class ActionExecutionStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    BLOCKED_STAGE1_SAFETY = "BLOCKED_STAGE1_SAFETY"


class SettlementStatus(str, enum.Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class PolicyDecisionType(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FLAGGED = "FLAGGED"


class AgentRunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActorType(str, enum.Enum):
    AI_AGENT = "AI_AGENT"
    POLICY_ENGINE = "POLICY_ENGINE"
    ACTION_LAYER = "ACTION_LAYER"
    USER = "USER"
    SYSTEM = "SYSTEM"


# =====================================================================
# CORE ENTITIES
# =====================================================================

class Merchant(Base):
    """Merchant organization controlling payment accounts and policies."""
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    customers: Mapped[List[Customer]] = relationship("Customer", back_populates="merchant", cascade="all, delete-orphan")
    orders: Mapped[List[Order]] = relationship("Order", back_populates="merchant", cascade="all, delete-orphan")
    payments: Mapped[List[Payment]] = relationship("Payment", back_populates="merchant", cascade="all, delete-orphan")
    opportunities: Mapped[List[RecoveryOpportunity]] = relationship("RecoveryOpportunity", back_populates="merchant", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("slug", name="merchants_slug_key"),
        Index("ix_merchants_slug", "slug", unique=True),
    )


class Customer(Base):
    """Customer transacting with a merchant."""
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Unified canonical risk score contract: risk_score in [0.0, 1.0]
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    merchant: Mapped[Merchant] = relationship("Merchant", back_populates="customers")
    orders: Mapped[List[Order]] = relationship("Order", back_populates="customer", cascade="all, delete-orphan")
    payments: Mapped[List[Payment]] = relationship("Payment", back_populates="customer", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("risk_score >= 0.0 AND risk_score <= 1.0", name="chk_customer_risk_score"),
    )


class Order(Base):
    """Merchant order representing commercial purchase intent."""
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=OrderStatus.PENDING.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    merchant: Mapped[Merchant] = relationship("Merchant", back_populates="orders")
    customer: Mapped[Customer] = relationship("Customer", back_populates="orders")
    payments: Mapped[List[Payment]] = relationship("Payment", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("amount >= 0.0", name="chk_order_amount_non_negative"),
    )


class Payment(Base):
    """Payment transaction associated with an order."""
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=PaymentStatus.PENDING.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    merchant: Mapped[Merchant] = relationship("Merchant", back_populates="payments")
    order: Mapped[Order] = relationship("Order", back_populates="payments")
    customer: Mapped[Customer] = relationship("Customer", back_populates="payments")
    attempts: Mapped[List[PaymentAttempt]] = relationship("PaymentAttempt", back_populates="payment", cascade="all, delete-orphan", order_by="PaymentAttempt.attempt_number")
    failures: Mapped[List[PaymentFailure]] = relationship("PaymentFailure", back_populates="payment", cascade="all, delete-orphan")
    opportunities: Mapped[List[RecoveryOpportunity]] = relationship("RecoveryOpportunity", back_populates="payment", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("amount >= 0.0", name="chk_payment_amount_non_negative"),
    )


class PaymentAttempt(Base):
    """Specific gateway execution attempt for a payment."""
    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Financial actions must support idempotency keys
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    
    gateway_name: Mapped[str] = mapped_column(String(100), nullable=False)
    gateway_transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    # Relationships
    payment: Mapped[Payment] = relationship("Payment", back_populates="attempts")
    failures: Mapped[List[PaymentFailure]] = relationship("PaymentFailure", back_populates="attempt", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_payment_attempts_payment_attempt_num", "payment_id", "attempt_number"),
    )


class PaymentFailure(Base):
    """Diagnostic details of a payment failure attempt."""
    __tablename__ = "payment_failures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("payment_attempts.id", ondelete="CASCADE"), nullable=False)
    
    # Required failure categories
    failure_code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    is_retryable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    # Relationships
    payment: Mapped[Payment] = relationship("Payment", back_populates="failures")
    attempt: Mapped[PaymentAttempt] = relationship("PaymentAttempt", back_populates="failures")
    recovery_opportunities: Mapped[List[RecoveryOpportunity]] = relationship("RecoveryOpportunity", back_populates="failure", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_payment_failures_attempt_id", "payment_attempt_id"),
    )


class RecoveryOpportunity(Base):
    """AI-identified revenue recovery opportunity for a failed payment."""
    __tablename__ = "recovery_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)
    failure_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("payment_failures.id", ondelete="CASCADE"), nullable=False, index=True)
    
    strategy_name: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_recoverable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=OpportunityStatus.OPEN.value, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    merchant: Mapped[Merchant] = relationship("Merchant", back_populates="opportunities")
    payment: Mapped[Payment] = relationship("Payment", back_populates="opportunities")
    failure: Mapped[PaymentFailure] = relationship("PaymentFailure", back_populates="recovery_opportunities")
    actions: Mapped[List[RecoveryAction]] = relationship("RecoveryAction", back_populates="opportunity", cascade="all, delete-orphan")
    policy_decisions: Mapped[List[PolicyDecision]] = relationship("PolicyDecision", back_populates="opportunity", cascade="all, delete-orphan")


class RecoveryAction(Base):
    """Action plan or step proposed to recover revenue."""
    __tablename__ = "recovery_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Financial actions must support idempotency keys
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    
    # Safety invariant: defaults to BLOCKED_STAGE1_SAFETY
    execution_status: Mapped[str] = mapped_column(String(50), default=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value, nullable=False)
    parameters_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Relationships
    opportunity: Mapped[RecoveryOpportunity] = relationship("RecoveryOpportunity", back_populates="actions")
    policy_decisions: Mapped[List[PolicyDecision]] = relationship("PolicyDecision", back_populates="action", cascade="all, delete-orphan")


class PolicyDecision(Base):
    """Deterministic policy authorization boundary check."""
    __tablename__ = "policy_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), nullable=False, index=True)
    action_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("recovery_actions.id", ondelete="SET NULL"), nullable=True, index=True)
    
    decision: Mapped[str] = mapped_column(String(50), nullable=False)  # APPROVED, REJECTED, FLAGGED
    rule_matched: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Unified canonical risk score contract: risk_score in [0.0, 1.0]
    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    # Relationships
    opportunity: Mapped[RecoveryOpportunity] = relationship("RecoveryOpportunity", back_populates="policy_decisions")
    action: Mapped[Optional[RecoveryAction]] = relationship("RecoveryAction", back_populates="policy_decisions")

    __table_args__ = (
        CheckConstraint("risk_score >= 0.0 AND risk_score <= 1.0", name="chk_policy_decision_risk_score"),
    )


class AgentRun(Base):
    """Execution trace of an autonomous intelligence agent."""
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default=AgentRunStatus.RUNNING.value, nullable=False)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0.000000"), nullable=False)
    metadata_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    # Relationships
    tool_calls: Mapped[List[ToolCall]] = relationship("ToolCall", back_populates="agent_run", cascade="all, delete-orphan")
    audit_events: Mapped[List[AuditEvent]] = relationship("AuditEvent", back_populates="agent_run", cascade="all, delete-orphan")


class ToolCall(Base):
    """Specific tool invocation performed during an agent run."""
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_payload_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    output_payload_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    # Relationships
    agent_run: Mapped[AgentRun] = relationship("AgentRun", back_populates="tool_calls")


class AuditEvent(Base):
    """Cryptographically chained tamper-evident audit event."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_before_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    payload_after_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    
    # Cryptographic SHA-256 hash chaining
    previous_event_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="0" * 64)
    
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)

    # Relationships
    agent_run: Mapped[Optional[AgentRun]] = relationship("AgentRun", back_populates="audit_events")

    __table_args__ = (
        UniqueConstraint("merchant_id", "sequence_number", name="uq_audit_merchant_seq"),
        Index("ix_audit_events_merchant_timestamp", "merchant_id", "timestamp"),
    )


class WebhookDelivery(Base):
    """Authoritative ledger of inbound gateway webhook events for idempotency and replay defense."""
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    gateway_name: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    signature: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="RECEIVED", nullable=False)  # RECEIVED, PROCESSED, REJECTED, DUPLICATE
    error_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        UniqueConstraint("gateway_name", "event_id", name="uq_webhook_gateway_event"),
    )


class OutboxEvent(Base):
    """Transactional outbox for reliable, exactly-once event publication."""
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # PAYMENT, RECOVERY_ACTION, OPPORTUNITY
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False, index=True)  # PENDING, DISPATCHED, FAILED, DEAD_LETTER
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False, index=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)


class SystemSafetyControl(Base):
    """Distributed system safety, kill-switch, and Stage 2 activation state in PostgreSQL."""
    __tablename__ = "system_safety_controls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(50), default="GLOBAL", nullable=False)
    merchant_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True), ForeignKey("merchants.id", ondelete="CASCADE"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(50), default="STAGE1_SAFETY", nullable=False)
    is_live_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kill_switch_engaged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    kill_switch_engaged_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime(), nullable=True)
    kill_switch_engaged_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    kill_switch_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    single_transaction_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("5000.00"), nullable=False)
    daily_volume_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("25000.00"), nullable=False)
    current_daily_volume: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", name="uq_system_safety_scope"),
        Index("ix_system_safety_controls_scope", "scope"),
    )


# Register queue model in Base.metadata for migration tracking
from services.queue.task_queue import QueueTaskModel


