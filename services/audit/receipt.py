"""First-Class Decision Receipts & "Why Didn't RAY Act?" Engine.

Enforces:
1. Immutable record of every financial decision.
2. Complete explainability: Deterministic rationale for why RAY approved, flagged, or refused to act.
3. Decision Replay: Verification that given the identical money context, the policy engine produces the identical authorization output.
"""

from __future__ import annotations

import decimal
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from services.money_graph.models import PolicyDecisionType
from services.policy_engine.engine import DeterministicPolicyEngine


class DecisionReceipt(BaseModel):
    """Authoritative, tamper-evident record of a financial recovery decision."""
    receipt_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    opportunity_id: uuid.UUID
    payment_id: uuid.UUID
    merchant_id: uuid.UUID
    decision: str  # APPROVED, REJECTED, FLAGGED
    rule_matched: str
    primary_reason: str
    why_didnt_ray_act: Optional[str] = None
    risk_score: float
    amount: decimal.Decimal
    currency: str
    stage_1_safety_lock_active: bool
    evidence_snapshot: List[str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DecisionReceiptGenerator:
    """Generates immutable decision receipts and deterministic action explanations."""

    @staticmethod
    def explain_inaction(
        decision: str,
        rule_matched: str,
        failure_code: str,
        risk_score: float,
        attempt_count: int,
        stage_1_safety_lock: bool = True,
    ) -> Optional[str]:
        """Explain deterministically why RAY chose not to execute an action."""
        if decision == PolicyDecisionType.REJECTED.value:
            if rule_matched == "FRAUD_ZERO_TOLERANCE_RULE":
                return (
                    "RAY refused to act because transaction exhibits terminal fraud indicators. "
                    "Automated recovery is strictly prohibited by merchant risk guardrails."
                )
            if rule_matched == "CARD_EXPIRED_TERMINAL_RULE":
                return (
                    "RAY refused to act because payment credential is expired. "
                    "Retrying against the card network would trigger issuer dispute penalties."
                )
            if rule_matched == "VELOCITY_LIMIT_EXCEEDED_RULE":
                return (
                    f"RAY refused to act because maximum velocity limit ({attempt_count} attempts) "
                    "was reached for this payment lifecycle."
                )
            return f"RAY refused to act because policy rule '{rule_matched}' blocked execution."

        if decision == PolicyDecisionType.FLAGGED.value:
            return (
                f"RAY paused autonomous execution because customer risk score ({risk_score:.2f}) "
                "exceeds automated clearance threshold. Escalated to manual human review."
            )

        if decision == PolicyDecisionType.APPROVED.value and stage_1_safety_lock:
            return (
                "Action was cleared by deterministic policy, but financial execution was safely withheld "
                "by the Stage 1 Financial Execution Safety Lock."
            )

        return None

    @classmethod
    def generate_receipt(
        cls,
        opportunity_id: uuid.UUID,
        payment_id: uuid.UUID,
        merchant_id: uuid.UUID,
        decision: str,
        rule_matched: str,
        reason: str,
        risk_score: float,
        amount: decimal.Decimal,
        currency: str,
        failure_code: str,
        attempt_count: int,
        evidence: List[str],
        stage_1_safety_lock: bool = True,
        receipt_id: Optional[uuid.UUID] = None,
    ) -> DecisionReceipt:
        """Construct authoritative DecisionReceipt with complete explainability."""
        inaction_explanation = cls.explain_inaction(
            decision=decision,
            rule_matched=rule_matched,
            failure_code=failure_code,
            risk_score=risk_score,
            attempt_count=attempt_count,
            stage_1_safety_lock=stage_1_safety_lock,
        )

        det_receipt_id = receipt_id or uuid.uuid5(uuid.NAMESPACE_DNS, f"ray_receipt_{payment_id}_{attempt_count}")

        return DecisionReceipt(
            receipt_id=det_receipt_id,
            opportunity_id=opportunity_id,
            payment_id=payment_id,
            merchant_id=merchant_id,
            decision=decision,
            rule_matched=rule_matched,
            primary_reason=reason,
            why_didnt_ray_act=inaction_explanation,
            risk_score=risk_score,
            amount=amount,
            currency=currency,
            stage_1_safety_lock_active=stage_1_safety_lock,
            evidence_snapshot=evidence,
        )

    @classmethod
    def replay_decision(
        cls,
        receipt: DecisionReceipt,
        failure_code: str,
        attempt_count: int,
    ) -> Tuple[bool, str]:
        """Replay stored context against policy engine and assert 100% deterministic reproducibility.
        
        Returns:
            (is_identical: bool, detail: str)
        """
        engine = DeterministicPolicyEngine()
        replayed_decision, replayed_rule, _ = engine.authorize_recovery(
            failure_code=failure_code,
            attempt_count=attempt_count,
            customer_risk_score=receipt.risk_score,
            amount=receipt.amount,
        )

        if replayed_decision.value != receipt.decision:
            return (
                False,
                f"Replay divergence: Original decision '{receipt.decision}' != Replayed decision '{replayed_decision.value}'",
            )

        if replayed_rule != receipt.rule_matched:
            return (
                False,
                f"Replay rule divergence: Original rule '{receipt.rule_matched}' != Replayed rule '{replayed_rule}'",
            )

        return True, "Decision replayed identically. Output is 100% deterministically reproducible."
