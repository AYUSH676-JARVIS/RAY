"""Pydantic schemas for RAY API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = Field(default="healthy")
    version: str = Field(default="1.0.0")
    timestamp: datetime
    database: str = Field(default="connected")
    environment: str = Field(default="development")


class PaymentAttemptSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payment_id: uuid.UUID
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
    payment_id: uuid.UUID
    payment_attempt_id: uuid.UUID
    failure_code: str
    raw_message: str
    is_retryable: bool
    created_at: datetime


class PolicyDecisionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision: str
    rule_matched: str
    reason: str
    risk_score: float
    created_at: datetime


class RecoveryActionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type: str
    idempotency_key: str
    execution_status: str
    parameters_json: Optional[Any] = None
    created_at: datetime
    executed_at: Optional[datetime] = None


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


class OpportunityListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: List[OpportunityItemSchema]


class PaymentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    order_id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime
    attempts: List[PaymentAttemptSchema] = []
    failures: List[PaymentFailureSchema] = []
    opportunities: List[OpportunityItemSchema] = []


class ToolCallSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    input_payload_json: Optional[Any] = None
    output_payload_json: Optional[Any] = None
    status: str
    duration_ms: int
    created_at: datetime


class AuditEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: Optional[uuid.UUID] = None
    entity_type: str
    entity_id: Optional[uuid.UUID] = None
    event_type: str
    actor_type: str
    actor_id: str
    payload_before_json: Optional[Any] = None
    payload_after_json: Optional[Any] = None
    timestamp: datetime


class AgentRunDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_name: str
    trigger_type: str
    status: str
    tokens_used: int
    cost_usd: Decimal
    metadata_json: Optional[Any] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    tool_calls: List[ToolCallSchema] = []
    audit_events: List[AuditEventSchema] = []


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


class DetectOpportunityRequest(BaseModel):
    """Payload for dynamically detecting a recovery opportunity."""
    payment_id: uuid.UUID

