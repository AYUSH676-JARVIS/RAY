"""Deterministic Failure Intelligence.

Classifies payment failure codes into deterministic recoverability profiles,
recommended strategy candidates, and blocked strategies.

DISCLAIMER & SCOPE:
Base recovery probabilities defined herein are calibrated baseline heuristics
for the RAY prototype and synthetic data testing. They do not represent real-world
empirical banking probabilities. In production, these calibrate against empirical
merchant telemetry.
"""

from __future__ import annotations

import enum
from typing import Dict, List
from pydantic import BaseModel, Field

from services.money_graph.models import FailureCategory


class CandidateStrategy(str, enum.Enum):
    """Supported candidate remediation strategies."""
    RETRY_NOW = "RETRY_NOW"
    WAIT_AND_RETRY = "WAIT_AND_RETRY"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    UPDATE_PAYMENT_METHOD = "UPDATE_PAYMENT_METHOD"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    NO_ACTION = "NO_ACTION"


class UrgencyLevel(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FailureProfile(BaseModel):
    """Deterministic intelligence profile for a failure category."""
    category: str
    is_recoverable: bool = Field(description="Whether the transaction can be recovered through safe automated means")
    base_recovery_probability: float = Field(
        description="Calibrated prototype recovery probability [0.0 - 1.0]. Not claimed to be empirical banking truth."
    )
    recommended_strategies: List[CandidateStrategy] = Field(description="Valid candidate recovery strategies")
    blocked_strategies: List[CandidateStrategy] = Field(description="Strategies strictly blocked by policy or logic")
    default_urgency: UrgencyLevel
    rationale: str


# Deterministic taxonomy mapping
FAILURE_PROFILES: Dict[str, FailureProfile] = {
    FailureCategory.BANK_TIMEOUT.value: FailureProfile(
        category=FailureCategory.BANK_TIMEOUT.value,
        is_recoverable=True,
        base_recovery_probability=0.85,
        recommended_strategies=[
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.RETRY_NOW,
            CandidateStrategy.SEND_PAYMENT_LINK,
        ],
        blocked_strategies=[
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
        ],
        default_urgency=UrgencyLevel.HIGH,
        rationale="Transient network/acquirer latency spike. Card credential remains healthy; scheduled retry yields high recovery probability.",
    ),
    FailureCategory.INSUFFICIENT_FUNDS.value: FailureProfile(
        category=FailureCategory.INSUFFICIENT_FUNDS.value,
        is_recoverable=True,
        base_recovery_probability=0.72,
        recommended_strategies=[
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.SEND_PAYMENT_LINK,
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,  # Retrying immediately when balance is empty guarantees decline and burns attempt
        ],
        default_urgency=UrgencyLevel.MEDIUM,
        rationale="Cardholder balance depleted. Immediate retries burn attempts; scheduled delay (payday/morning window) or alternate payment link recommended.",
    ),
    FailureCategory.ISSUER_DECLINED.value: FailureProfile(
        category=FailureCategory.ISSUER_DECLINED.value,
        is_recoverable=True,
        base_recovery_probability=0.60,
        recommended_strategies=[
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
            CandidateStrategy.SEND_PAYMENT_LINK,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,
        ],
        default_urgency=UrgencyLevel.MEDIUM,
        rationale="Generic 'Do Not Honor' decline. Immediate retry burns attempts. Secondary routing or customer account update recommended.",
    ),
    FailureCategory.AUTHENTICATION_FAILED.value: FailureProfile(
        category=FailureCategory.AUTHENTICATION_FAILED.value,
        is_recoverable=True,
        base_recovery_probability=0.75,
        recommended_strategies=[
            CandidateStrategy.SEND_PAYMENT_LINK,
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,  # Customer must be present for 3D-Secure challenge
        ],
        default_urgency=UrgencyLevel.HIGH,
        rationale="3D-Secure authentication failed or timed out. Customer presence required; payment link or authenticated outreach required.",
    ),
    FailureCategory.NETWORK_ERROR.value: FailureProfile(
        category=FailureCategory.NETWORK_ERROR.value,
        is_recoverable=True,
        base_recovery_probability=0.90,
        recommended_strategies=[
            CandidateStrategy.RETRY_NOW,
            CandidateStrategy.WAIT_AND_RETRY,
        ],
        blocked_strategies=[
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
        ],
        default_urgency=UrgencyLevel.HIGH,
        rationale="Transport-level packet loss or gateway socket reset. Transient error; immediate or rapid exponential backoff retry recommended.",
    ),
    FailureCategory.CARD_EXPIRED.value: FailureProfile(
        category=FailureCategory.CARD_EXPIRED.value,
        is_recoverable=False,  # Direct retry is NOT recoverable
        base_recovery_probability=0.65,  # Recoverable via credential update only
        recommended_strategies=[
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
            CandidateStrategy.SEND_PAYMENT_LINK,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,
            CandidateStrategy.WAIT_AND_RETRY,
        ],
        default_urgency=UrgencyLevel.LOW,
        rationale="Card validity date expired. Direct retries against card networks are strictly forbidden to avoid non-compliance fines. Customer outreach required.",
    ),
    FailureCategory.LIMIT_EXCEEDED.value: FailureProfile(
        category=FailureCategory.LIMIT_EXCEEDED.value,
        is_recoverable=True,
        base_recovery_probability=0.70,
        recommended_strategies=[
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.SEND_PAYMENT_LINK,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,
        ],
        default_urgency=UrgencyLevel.MEDIUM,
        rationale="Cardholder daily spend limit exceeded. Reset occurs post-midnight; retry after window reset or offer alternative payment method.",
    ),
    FailureCategory.FRAUD_SUSPECTED.value: FailureProfile(
        category=FailureCategory.FRAUD_SUSPECTED.value,
        is_recoverable=False,  # Autonomous recovery is strictly forbidden
        base_recovery_probability=0.05,
        recommended_strategies=[
            CandidateStrategy.HUMAN_REVIEW,
            CandidateStrategy.NO_ACTION,
        ],
        blocked_strategies=[
            CandidateStrategy.RETRY_NOW,
            CandidateStrategy.WAIT_AND_RETRY,
            CandidateStrategy.SEND_PAYMENT_LINK,
            CandidateStrategy.UPDATE_PAYMENT_METHOD,
        ],
        default_urgency=UrgencyLevel.HIGH,
        rationale="Transaction triggered issuer or gateway fraud rules. Automated recovery is prohibited to prevent chargebacks and network fines. Requires human review.",
    ),
}


def get_failure_profile(failure_code: str) -> FailureProfile:
    """Retrieve failure profile for a category, or a safe default if unknown."""
    if failure_code in FAILURE_PROFILES:
        return FAILURE_PROFILES[failure_code]

    # Safe fallback for unrecognized failure codes
    return FailureProfile(
        category=failure_code,
        is_recoverable=False,
        base_recovery_probability=0.10,
        recommended_strategies=[CandidateStrategy.HUMAN_REVIEW, CandidateStrategy.NO_ACTION],
        blocked_strategies=[CandidateStrategy.RETRY_NOW],
        default_urgency=UrgencyLevel.LOW,
        rationale=f"Unrecognized failure code '{failure_code}'. Manual investigation required before any automated action.",
    )
