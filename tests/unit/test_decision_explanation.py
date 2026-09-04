"""TASK 20 — Decision Explanation System Unit Tests.

Verifies:
1. Structured schema completeness:
   - WHAT HAPPENED
   - WHY IT HAPPENED
   - WHAT RAY RECOMMENDED
   - WHAT POLICY DECIDED
   - WHAT ACTION WAS TAKEN
   - WHAT EVIDENCE SUPPORTED THE DECISION
   - WHAT THE EXPECTED VALUE WAS
   - WHAT THE ACTUAL RESULT WAS
   - WHY RAY DID NOT ACT (when blocked)
2. Truth-grounding: Explanations correspond strictly to recorded domain data.
3. No hallucination or fabrication.
"""

import uuid
from decimal import Decimal
import pytest

from services.audit.explanation import DecisionExplanation, DecisionExplanationGenerator
from services.audit.receipt import DecisionReceiptGenerator
from services.money_graph.models import PolicyDecisionType


def test_decision_explanation_approved_stage1_blocked():
    """Verify explanation generated when action is cleared by policy but held by Stage 1 lock."""
    explanation = DecisionExplanationGenerator.generate_explanation(
        payment_id=str(uuid.uuid4()),
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        failure_message="Acquiring bank connection timed out during 3DS",
        recommended_strategy="SMART_RETRY",
        expected_value="2125.00",
        recovery_probability=0.85,
        policy_decision=PolicyDecisionType.APPROVED.value,
        policy_rule="STANDARD_DETERMINISTIC_CLEARANCE",
        policy_reason="Transaction passed all deterministic merchant policies and velocity boundaries.",
        action_status="BLOCKED_STAGE1_SAFETY",
        is_recovered=False,
        evidence_ids=["pay_001", "fail_timeout"],
        why_didnt_ray_act="Action was cleared by deterministic policy, but financial execution was safely withheld by Stage 1 lock.",
    )

    assert isinstance(explanation, DecisionExplanation)
    assert "2500.00" in explanation.what_happened
    assert "BANK_TIMEOUT" in explanation.what_happened
    assert "SMART_RETRY" in explanation.what_ray_recommended
    assert "2125.00" in explanation.expected_value
    assert "85.0%" in explanation.recovery_probability
    assert "APPROVED" in explanation.what_policy_decided
    assert "Stage 1" in explanation.what_action_was_taken
    assert explanation.why_ray_did_not_act is not None
    assert "pay_001" in explanation.evidence_citations


def test_decision_explanation_policy_rejected_fraud():
    """Verify explanation generated when action is rejected due to fraud detection."""
    explanation = DecisionExplanationGenerator.generate_explanation(
        payment_id=str(uuid.uuid4()),
        amount="15000.00",
        currency="INR",
        failure_code="FRAUD_SUSPECTED",
        failure_message="High risk velocity detected on card BIN",
        recommended_strategy="WAIT_AND_RETRY",
        expected_value="0.00",
        recovery_probability=0.05,
        policy_decision=PolicyDecisionType.REJECTED.value,
        policy_rule="FRAUD_ZERO_TOLERANCE_RULE",
        policy_reason="Terminal fraud indicator detected. Automated retry prohibited.",
        action_status=None,
        is_recovered=False,
        evidence_ids=["pay_fraud_99", "cust_risk_0.95"],
        why_didnt_ray_act="RAY refused to act because transaction exhibits terminal fraud indicators.",
    )

    assert "15000.00" in explanation.what_happened
    assert "FRAUD_SUSPECTED" in explanation.what_happened
    assert "fraud" in explanation.why_it_happened.lower()
    assert "REJECTED" in explanation.what_policy_decided
    assert "Zero gateway calls" in explanation.actual_result
    assert "terminal fraud" in explanation.why_ray_did_not_act.lower()


def test_decision_explanation_unknown_gateway_timeout():
    """Verify explanation generated when gateway times out into UNKNOWN state."""
    explanation = DecisionExplanationGenerator.generate_explanation(
        payment_id=str(uuid.uuid4()),
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        failure_message="Issuer pipe timeout",
        recommended_strategy="SMART_RETRY",
        expected_value="2125.00",
        recovery_probability=0.85,
        policy_decision="APPROVED",
        policy_rule="STANDARD_DETERMINISTIC_CLEARANCE",
        policy_reason="Cleared",
        action_status="UNKNOWN",
        is_recovered=False,
        evidence_ids=["pay_001"],
        why_didnt_ray_act=None,
    )

    assert "UNKNOWN" in explanation.what_action_was_taken
    assert "Requires authoritative reconciliation" in explanation.actual_result
    assert explanation.why_ray_did_not_act is None


def test_decision_explanation_successful_recovery():
    """Verify explanation generated when action executes and settles successfully."""
    explanation = DecisionExplanationGenerator.generate_explanation(
        payment_id=str(uuid.uuid4()),
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        failure_message=None,
        recommended_strategy="SMART_RETRY",
        expected_value="2125.00",
        recovery_probability=0.85,
        policy_decision="APPROVED",
        policy_rule="STANDARD_DETERMINISTIC_CLEARANCE",
        policy_reason="Cleared",
        action_status="SUCCEEDED",
        is_recovered=True,
        evidence_ids=["pay_001"],
        why_didnt_ray_act=None,
    )

    assert "settled" in explanation.actual_result.lower()
    assert "INR 2500.00" in explanation.actual_result
    assert explanation.why_ray_did_not_act is None
