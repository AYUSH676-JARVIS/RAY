"""Schemas for the Canonical End-to-End Automatic Decision Loop."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from services.audit.explanation import DecisionExplanation
from services.audit.receipt import DecisionReceipt
from services.opportunities.providers import OpportunityResult


class StageStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"


class WorkflowStageRecord(BaseModel):
    """Execution telemetry for an individual pipeline stage."""
    stage_name: str
    status: StageStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    input_summary: Optional[Dict[str, Any]] = None
    detail: Optional[str] = None
    output_summary: Optional[Dict[str, Any]] = None
    decision: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None
    correlation_id: Optional[str] = None



class DecisionProposal(BaseModel):
    """Machine-validatable structured decision output from Decision Engine."""
    decision_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opportunity_id: Optional[uuid.UUID] = None
    recommended_action: str
    expected_value: Decimal
    probability_of_success: float
    model_confidence: float
    data_confidence: float
    risk: str
    urgency: str
    reasoning_summary: str
    evidence_ids: List[str] = Field(default_factory=list)
    policy_input: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Optional[Dict[str, Any]] = None


class DecisionWorkflowResult(BaseModel):
    """Complete immutable outcome of an end-to-end decision workflow execution."""
    workflow_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    status: str  # COMPLETED, STAGE1_BLOCKED, POLICY_BLOCKED, UNKNOWN, NO_OPPORTUNITY, FAILED
    current_stage: str
    stages: List[WorkflowStageRecord] = Field(default_factory=list)
    opportunity: Optional[OpportunityResult] = None
    decision: Optional[DecisionProposal] = None
    policy_decision: Optional[Dict[str, Any]] = None
    action_result: Optional[Dict[str, Any]] = None
    outcome_result: Optional[Dict[str, Any]] = None
    decision_receipt: Optional[DecisionReceipt] = None
    explanation: Optional[DecisionExplanation] = None
    audit_event_id: Optional[uuid.UUID] = None
    audit_hash: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
