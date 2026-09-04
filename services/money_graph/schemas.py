"""Pydantic schemas for the Money Graph service.

Invariants:
- All identifiers are UUIDs.
- All timestamps are timezone-aware UTC.
- Never fabricate fields: unavailable or missing fields return None or an explicit "UNKNOWN".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PaymentContext(BaseModel):
    """Contextual representation of a payment transaction."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    order_id: uuid.UUID
    customer_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime
    updated_at: datetime


class CustomerContext(BaseModel):
    """Contextual profile and value metrics of a customer."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    external_id: str
    name: str
    email: str
    risk_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Customer credit and fraud risk score in canonical [0.0, 1.0] range",
    )
    lifetime_value: Decimal = Field(description="Sum of all successful payments in merchant history")
    successful_payments: int = Field(description="Count of settled successful transactions")
    failed_payments: int = Field(description="Count of historical failed transactions")
    total_orders: int = Field(description="Total orders placed by customer")
    created_at: datetime


class OrderContext(BaseModel):
    """Contextual commercial order intent."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID
    customer_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime


class PaymentAttemptContext(BaseModel):
    """Contextual representation of a discrete gateway execution attempt."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    attempt_number: int
    idempotency_key: str
    gateway_name: str
    gateway_transaction_id: Optional[str] = None
    status: str
    latency_ms: Optional[int] = None
    created_at: datetime


class FailureContext(BaseModel):
    """Diagnostic breakdown of a payment failure."""
    model_config = ConfigDict(from_attributes=True)

    id: Optional[uuid.UUID] = None
    failure_code: str
    raw_message: str
    is_retryable: bool
    attempt_number: Optional[int] = None
    gateway_name: Optional[str] = None
    created_at: Optional[datetime] = None


class CustomerPaymentHistoryContext(BaseModel):
    """Aggregated historical payment behavior and retry performance."""
    customer_id: uuid.UUID
    total_payments: int
    total_successful: int
    total_failed: int
    retry_success_rate: float = Field(
        description="Historical success rate on transactions retried after initial failure [0.0 - 1.0]"
    )
    average_ticket_size: Decimal
    last_payment_date: Optional[datetime] = None


class TransactionSummary(BaseModel):
    """Lightweight historical transaction summary."""
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID
    order_id: uuid.UUID
    amount: Decimal
    currency: str
    status: str
    created_at: datetime


class FullMoneyContext(BaseModel):
    """Reconstructed graph of money relationships for a single payment.

    All evidence originates from connected merchant records.
    Never fabricates fields: if a failure or customer is missing, fields remain None or UNKNOWN.
    """
    payment: PaymentContext
    customer: Optional[CustomerContext] = None
    order: Optional[OrderContext] = None
    failure: Optional[FailureContext] = None
    attempts: List[PaymentAttemptContext] = Field(default_factory=list)
    history: Optional[CustomerPaymentHistoryContext] = None
    related_transactions: List[TransactionSummary] = Field(default_factory=list)
