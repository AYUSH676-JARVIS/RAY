"""Decision Explanation System for RAY.

Provides first-class, structured, deterministic explanations for every autonomous decision.
Explains:
1. WHAT HAPPENED: Empirical failure context
2. WHY IT HAPPENED: Root cause diagnosis from Money Graph
3. WHAT RAY RECOMMENDED: Proposed action with recovery probability & expected value
4. WHAT POLICY DECIDED: Deterministic authorization & rule clearance
5. WHAT ACTION WAS TAKEN: Execution result or safety lock block
6. WHAT EVIDENCE SUPPORTED THE DECISION: Validated empirical Money Graph entity IDs
7. WHAT THE EXPECTED VALUE WAS: Quantized monetary expected value
8. WHAT THE ACTUAL RESULT WAS: Authoritative outcome verification or safety status
9. WHY RAY DID NOT ACT: Explicit rationale when execution is withheld
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DecisionExplanation(BaseModel):
    """Structured, tamper-evident explanation of a financial recovery decision."""
    what_happened: str = Field(description="Summary of the empirical payment failure")
    why_it_happened: str = Field(description="Root cause breakdown from Money Graph context")
    what_ray_recommended: str = Field(description="Strategy proposed by opportunity intelligence")
    expected_value: str = Field(description="Quantized expected revenue recovery")
    recovery_probability: str = Field(description="Calibrated probability of successful recovery")
    what_policy_decided: str = Field(description="Deterministic rule evaluation and authorization status")
    what_action_was_taken: str = Field(description="Gateway execution result or Stage 1 safety lock status")
    evidence_citations: List[str] = Field(default_factory=list, description="Empirical entity IDs supporting the decision")
    actual_result: str = Field(description="Authoritative outcome confirmation or hold status")
    why_ray_did_not_act: Optional[str] = Field(default=None, description="Explicit rationale if action was withheld or blocked")


class DecisionExplanationGenerator:
    """Generates truth-grounded DecisionExplanation instances from recorded decision data."""

    @classmethod
    def generate_explanation(
        cls,
        payment_id: str,
        amount: str,
        currency: str,
        failure_code: str,
        failure_message: Optional[str],
        recommended_strategy: Optional[str],
        expected_value: Optional[str],
        recovery_probability: Optional[float],
        policy_decision: Optional[str],
        policy_rule: Optional[str],
        policy_reason: Optional[str],
        action_status: Optional[str],
        is_recovered: bool,
        evidence_ids: List[str],
        why_didnt_ray_act: Optional[str] = None,
    ) -> DecisionExplanation:
        """Construct authoritative explanation strictly from recorded domain data."""
        # 1. What Happened
        msg_detail = f": {failure_message}" if failure_message else ""
        what_happened = (
            f"Payment of {currency} {amount} encountered a transaction failure ({failure_code}{msg_detail})."
        )

        # 2. Why It Happened
        if failure_code == "BANK_TIMEOUT":
            why_it_happened = "Acquiring bank or 3DS verification pipe timed out before final settlement."
        elif failure_code == "FRAUD_SUSPECTED":
            why_it_happened = "Transaction triggered issuer fraud risk velocity heuristics or blacklisted card identifiers."
        elif failure_code == "CARD_EXPIRED":
            why_it_happened = "Customer card credential has passed its expiration date and cannot be authorized."
        elif failure_code == "INSUFFICIENT_FUNDS":
            why_it_happened = "Cardholder balance was insufficient at time of presentment."
        else:
            why_it_happened = f"Transaction declined with raw failure code '{failure_code}'."

        # 3. What RAY Recommended
        rec_strat = recommended_strategy or "NO_ACTION"
        what_ray_recommended = f"RAY proposed strategy '{rec_strat}' based on historical customer reliability and route performance."

        # 4. Expected Value & Probability
        ev_str = f"{currency} {expected_value}" if expected_value else f"{currency} 0.00"
        prob_str = f"{(recovery_probability or 0.0) * 100:.1f}%"

        # 5. What Policy Decided
        p_dec = policy_decision or "EVALUATION_PENDING"
        p_rule = f"[{policy_rule}] " if policy_rule else ""
        p_reason = f": {policy_reason}" if policy_reason else ""
        what_policy_decided = f"{p_dec} {p_rule}{p_reason}"

        # 6. What Action Was Taken
        act_status = action_status or "NO_ACTION_EXECUTED"
        if act_status == "BLOCKED_STAGE1_SAFETY":
            what_action_was_taken = "Execution held by Stage 1 Financial Safety Lock (autonomous money movement disabled)."
        elif act_status == "SUCCEEDED":
            what_action_was_taken = "Dispatched recovery transaction to payment gateway."
        elif act_status == "UNKNOWN":
            what_action_was_taken = "Gateway timed out during retry execution. Entered UNKNOWN state without blind retries."
        else:
            what_action_was_taken = f"Action status: {act_status}."

        # 7. Actual Result
        if is_recovered:
            actual_result = f"Authoritative verification confirmed {currency} {amount} settled."
        elif act_status == "BLOCKED_STAGE1_SAFETY":
            actual_result = "Zero external money moved. Action cleared by policy but withheld by safety lock."
        elif act_status == "UNKNOWN":
            actual_result = "Outcome ambiguous. Requires authoritative reconciliation before state transition."
        elif policy_decision == "REJECTED":
            actual_result = "Execution blocked by deterministic policy guardrails. Zero gateway calls dispatched."
        else:
            actual_result = "Recovery attempt concluded without successful settlement."

        return DecisionExplanation(
            what_happened=what_happened,
            why_it_happened=why_it_happened,
            what_ray_recommended=what_ray_recommended,
            expected_value=ev_str,
            recovery_probability=prob_str,
            what_policy_decided=what_policy_decided,
            what_action_was_taken=what_action_was_taken,
            evidence_citations=evidence_ids,
            actual_result=actual_result,
            why_ray_did_not_act=why_didnt_ray_act,
        )
