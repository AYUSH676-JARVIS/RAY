"""Deterministic Policy Engine.

Core Invariant:
AI recommends. Deterministic policy authorizes.

This engine evaluates hard boundaries, velocity limits, and risk thresholds.
No AI agent can bypass the policy engine.
"""

from __future__ import annotations

import decimal
from typing import Dict, Tuple
from services.money_graph.models import FailureCategory, PolicyDecisionType


class DeterministicPolicyEngine:
    """Evaluates proposed recovery actions against strict mathematical & merchant guardrails."""

    def __init__(self, max_daily_retry_limit: int = 3, max_risk_score: float = 0.65):
        self.max_daily_retry_limit = max_daily_retry_limit
        self.max_risk_score = max_risk_score

    def authorize_recovery(
        self,
        failure_code: str,
        attempt_count: int,
        customer_risk_score: float,
        amount: decimal.Decimal,
        payment_status: Optional[str] = None,
    ) -> Tuple[PolicyDecisionType, str, str]:
        """Evaluate authorization boundary for a proposed recovery action.

        Returns:
            (DecisionType, RuleMatched, Reason)
        """
        # Validate canonical contracts
        if not (0.0 <= customer_risk_score <= 1.0):
            raise ValueError(
                f"Invalid customer_risk_score {customer_risk_score}. Canonical risk_score contract requires 0.0 <= risk_score <= 1.0."
            )
        if amount < decimal.Decimal("0.00"):
            raise ValueError(
                f"Invalid financial amount {amount}. Monetary amount cannot be negative."
            )

        # Rule 0A: Ambiguous UNKNOWN payments cannot be blindly retried
        if payment_status and payment_status.upper() == "UNKNOWN":
            return (
                PolicyDecisionType.REJECTED,
                "AMBIGUOUS_UNKNOWN_OUTCOME_RULE",
                "Payment outcome is in UNKNOWN state. Automated retry blocked until authoritative reconciliation.",
            )

        # Rule 0B: Already successful or settled payments cannot be retried
        if payment_status and payment_status.upper() in ["SUCCESS", "CAPTURED", "SETTLED"]:
            return (
                PolicyDecisionType.REJECTED,
                "ALREADY_SETTLED_RULE",
                "Payment has already succeeded or captured. Additional recovery actions are prohibited.",
            )

        # Rule 1: Fraud suspected transactions are permanently rejected for automated retry
        if failure_code == FailureCategory.FRAUD_SUSPECTED.value:
            return (
                PolicyDecisionType.REJECTED,
                "FRAUD_ZERO_TOLERANCE_RULE",
                "Transaction flagged with fraud indicators. Automated recovery strictly forbidden.",
            )

        # Rule 2: Expired cards cannot be retried without credential updates
        if failure_code == FailureCategory.CARD_EXPIRED.value:
            return (
                PolicyDecisionType.REJECTED,
                "CARD_EXPIRED_TERMINAL_RULE",
                "Card credential is expired. Retrying against network will cause issuer penalty.",
            )

        # Rule 3: Velocity limit on attempts
        if attempt_count >= self.max_daily_retry_limit:
            return (
                PolicyDecisionType.REJECTED,
                "VELOCITY_LIMIT_EXCEEDED_RULE",
                f"Maximum daily attempts ({self.max_daily_retry_limit}) reached for this payment.",
            )

        # Rule 4: High risk customer threshold
        if customer_risk_score > self.max_risk_score:
            return (
                PolicyDecisionType.FLAGGED,
                "HIGH_RISK_CUSTOMER_HEURISTIC",
                f"Customer risk score {customer_risk_score:.2f} exceeds auto-clearance threshold {self.max_risk_score:.2f}. Manual review required.",
            )

        # Default: Approved under deterministic guardrails
        return (
            PolicyDecisionType.APPROVED,
            "STANDARD_DETERMINISTIC_CLEARANCE",
            "Transaction passed all deterministic merchant policies and velocity boundaries.",
        )
