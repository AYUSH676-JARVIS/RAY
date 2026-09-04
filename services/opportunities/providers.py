"""Pluggable Decision Provider Architecture for RAY.

Strict Architectural Invariant:
- Deterministic logic performs all money calculations, eligibility checks,
  retry limits, and policy boundaries.
- The DecisionProvider interface allows future AI providers to propose
  strategy rankings or natural-language explanations without ever bypassing
  deterministic safety gates.
"""

from __future__ import annotations

import abc
import decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

from services.money_graph.schemas import FullMoneyContext
from services.opportunities.failure_intelligence import (
    CandidateStrategy,
    FailureProfile,
    UrgencyLevel,
    get_failure_profile,
)


class ScoreBreakdown(BaseModel):
    """Transparent and inspectable breakdown of the opportunity score.
    
    Formula:
      Expected Recovery = Amount * recovery_probability
      Expected Net Value = Expected Recovery - action_cost - risk_cost - friction_cost
      Raw Score = Expected Net Value * model_confidence * data_confidence * urgency
      Normalized Score = (Raw Score / Amount) * 100 [clamped to 0.00 - 100.00]
    """
    expected_recovery: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Gross expected recovery")
    recovery_probability: float = Field(default=0.0, description="Estimated probability of successful recovery [0.0 - 1.0]")
    model_confidence: float = Field(default=1.0, description="Confidence of recovery model [0.0 - 1.0]")
    data_confidence: float = Field(default=1.0, description="Confidence based on customer history [0.0 - 1.0]")
    urgency_multiplier: float = Field(default=1.0, description="Urgency multiplier")
    action_cost: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Operational action cost")
    risk_cost: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Fraud and credit risk cost")
    friction_cost: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Customer outreach friction cost")
    expected_net_value: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Expected Net Value")
    raw_score: float = Field(default=0.0, description="Raw calculated score")
    normalized_score: float = Field(default=0.0, description="Normalized score on [0.00, 100.00] scale")

    # Backward compatibility aliases
    expected_value: decimal.Decimal = Field(default=decimal.Decimal("0.00"), description="Alias to expected_net_value")
    success_probability: float = Field(default=0.0, description="Alias to recovery_probability")
    confidence: float = Field(default=1.0, description="Combined confidence")

    @model_validator(mode="before")
    @classmethod
    def populate_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            ev = data.get("expected_value", decimal.Decimal("0.00"))
            sp = data.get("success_probability", 0.0)
            conf = data.get("confidence", 1.0)
            if "expected_recovery" not in data:
                data["expected_recovery"] = ev
            if "recovery_probability" not in data:
                data["recovery_probability"] = sp
            if "model_confidence" not in data:
                data["model_confidence"] = conf
            if "data_confidence" not in data:
                data["data_confidence"] = 1.0
            if "friction_cost" not in data:
                data["friction_cost"] = decimal.Decimal("0.00")
            if "expected_net_value" not in data:
                data["expected_net_value"] = ev
            if "raw_score" not in data:
                data["raw_score"] = float(ev) * sp * conf
        return data


class OpportunityExplanation(BaseModel):
    """Explainable rationale backing the recovery opportunity recommendation."""
    decision: str
    confidence: float
    expected_value: decimal.Decimal
    risk: str
    urgency: str
    evidence: List[str] = Field(description="Concrete empirical facts extracted from the Money Graph")
    alternatives_considered: List[str]
    reason: str


class OpportunityResult(BaseModel):
    """Complete dynamically derived recovery opportunity."""
    payment_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    order_id: Optional[str] = None
    amount: decimal.Decimal
    currency: str
    is_eligible: bool
    failure_category: Optional[str] = None
    recommended_strategy: str
    opportunity_score: float
    score_breakdown: ScoreBreakdown
    explanation: OpportunityExplanation
    candidate_strategies: List[str]
    blocked_strategies: List[str]
    is_financial_action_executed: bool = Field(
        default=False,
        description="Safety invariant: always False at this stage (Stage 1 Safety Lock active)",
    )


class DecisionProvider(abc.ABC):
    """Abstract interface for opportunity evaluation providers."""

    @abc.abstractmethod
    def evaluate(self, context: FullMoneyContext) -> OpportunityResult:
        """Evaluate a full money graph context and derive a recovery opportunity."""
        pass


class DeterministicDecisionProvider(DecisionProvider):
    """Default deterministic decision provider.

    Operates purely through mathematical and heuristic rules without requiring an LLM.
    """

    MAX_RETRIES_LIMIT = 3
    URGENCY_WEIGHTS = {
        UrgencyLevel.HIGH.value: 1.25,
        UrgencyLevel.MEDIUM.value: 1.00,
        UrgencyLevel.LOW.value: 0.75,
    }

    ACTION_COSTS = {
        CandidateStrategy.RETRY_NOW.value: decimal.Decimal("0.30"),
        CandidateStrategy.WAIT_AND_RETRY.value: decimal.Decimal("0.30"),
        CandidateStrategy.SEND_PAYMENT_LINK.value: decimal.Decimal("0.05"),
        CandidateStrategy.UPDATE_PAYMENT_METHOD.value: decimal.Decimal("0.10"),
        CandidateStrategy.HUMAN_REVIEW.value: decimal.Decimal("4.50"),
        CandidateStrategy.NO_ACTION.value: decimal.Decimal("0.00"),
    }

    def evaluate(self, context: FullMoneyContext) -> OpportunityResult:
        payment = context.payment
        amount = payment.amount
        currency = payment.currency
        customer = context.customer
        failure = context.failure
        attempts = context.attempts
        history = context.history

        # 1. Verify payment status is FAILED or UNKNOWN
        if payment.status not in ["FAILED", "UNKNOWN"]:
            return self._build_ineligible_result(
                context,
                reason=f"Payment is in status '{payment.status}'. Only FAILED payments can generate recovery opportunities.",
            )

        # 2. Extract failure information
        failure_code = failure.failure_code if failure else "UNKNOWN_FAILURE"
        profile: FailureProfile = get_failure_profile(failure_code)

        # 3. Check attempt count against velocity threshold
        retry_count = len(attempts)
        if retry_count >= self.MAX_RETRIES_LIMIT and profile.category != "FRAUD_SUSPECTED":
            # Exceeded maximum retry attempts: switch recommendation to outreach or manual review
            return self._build_limit_exceeded_result(context, profile, retry_count)

        # 4. Modulate recovery probability based on customer history and attempts
        base_prob = profile.base_recovery_probability

        # Bonus: Customer has prior successful payments
        customer_history_bonus = 0.0
        if customer and customer.successful_payments > 0:
            total_cust_tx = customer.successful_payments + customer.failed_payments
            succ_ratio = customer.successful_payments / total_cust_tx if total_cust_tx > 0 else 0.5
            customer_history_bonus = min(0.12, succ_ratio * 0.12)

        # Penalty: consecutive attempt decay
        attempt_decay = max(0.0, (retry_count - 1) * 0.15) if retry_count > 1 else 0.0

        # Customer risk penalty (canonical [0.0, 1.0] scale)
        cust_risk_penalty = 0.0
        if customer and customer.risk_score > 0.50:
            cust_risk_penalty = ((customer.risk_score - 0.50) / 0.50) * 0.25

        success_prob = max(0.02, min(0.98, base_prob + customer_history_bonus - attempt_decay - cust_risk_penalty))
        success_prob = round(success_prob, 4)

        # 5. Assess Risk Level (canonical [0.0, 1.0] scale)
        risk_level = "LOW"
        risk_pct = 0.02
        if failure_code == "FRAUD_SUSPECTED" or (customer and customer.risk_score >= 0.80):
            risk_level = "CRITICAL"
            risk_pct = 0.60
        elif customer and customer.risk_score >= 0.50:
            risk_level = "HIGH"
            risk_pct = 0.25
        elif customer and customer.risk_score >= 0.25:
            risk_level = "MEDIUM"
            risk_pct = 0.08

        # 6. Select candidate strategies and top decision
        recommended_candidates = [s.value for s in profile.recommended_strategies]
        blocked_candidates = [s.value for s in profile.blocked_strategies]

        # Prioritize primary strategy based on context
        if failure_code == "FRAUD_SUSPECTED":
            top_decision = CandidateStrategy.HUMAN_REVIEW.value
            urgency = UrgencyLevel.HIGH.value
        elif failure_code == "CARD_EXPIRED":
            top_decision = CandidateStrategy.UPDATE_PAYMENT_METHOD.value
            urgency = UrgencyLevel.LOW.value
        elif retry_count > 1 and CandidateStrategy.SEND_PAYMENT_LINK.value in recommended_candidates:
            top_decision = CandidateStrategy.SEND_PAYMENT_LINK.value
            urgency = UrgencyLevel.MEDIUM.value
        else:
            top_decision = recommended_candidates[0] if recommended_candidates else CandidateStrategy.NO_ACTION.value
            urgency = profile.default_urgency.value

        alternatives = [s for s in recommended_candidates if s != top_decision]

        # 7. Calculate Financial Expected Value & Costs (Zero float arithmetic, no double-counting)
        action_cost = self.ACTION_COSTS.get(top_decision, decimal.Decimal("0.30"))
        risk_cost = round(amount * decimal.Decimal(str(risk_pct)), 2)
        friction_cost = decimal.Decimal("0.50") if top_decision == CandidateStrategy.SEND_PAYMENT_LINK.value else decimal.Decimal("0.00")
        expected_recovery = round(amount * decimal.Decimal(str(success_prob)), 2)
        expected_net_value = max(decimal.Decimal("0.00"), round(expected_recovery - action_cost - risk_cost - friction_cost, 2))

        # 8. Calculate Opportunity Score (Transparently combined)
        urgency_weight = self.URGENCY_WEIGHTS.get(urgency, 1.0)
        model_confidence = 0.90 if failure_code in ["BANK_TIMEOUT", "NETWORK_ERROR"] else 0.80
        data_confidence = 0.95 if customer and customer.successful_payments >= 3 else (0.85 if customer else 0.70)
        combined_confidence = round(model_confidence * data_confidence, 4)

        raw_score = float(expected_net_value) * model_confidence * data_confidence * urgency_weight
        normalized_score = max(0.0, min(100.0, round((raw_score / max(1.0, float(amount))) * 100.0, 2)))

        score_breakdown = ScoreBreakdown(
            expected_recovery=expected_recovery,
            recovery_probability=success_prob,
            model_confidence=model_confidence,
            data_confidence=data_confidence,
            urgency_multiplier=urgency_weight,
            action_cost=action_cost,
            risk_cost=risk_cost,
            friction_cost=friction_cost,
            expected_net_value=expected_net_value,
            raw_score=round(raw_score, 4),
            normalized_score=normalized_score,
            # Aliases
            expected_value=expected_net_value,
            success_probability=success_prob,
            confidence=combined_confidence,
        )

        # 9. Extract concrete evidence from Money Graph
        evidence = [
            f"failure_category={failure_code}",
            f"retry_count={retry_count}",
            f"payment_amount={amount} {currency}",
        ]
        if customer:
            evidence.append(f"customer_risk_score={customer.risk_score}")
            evidence.append(f"previous_successful_payments={customer.successful_payments}")
            evidence.append(f"previous_failed_payments={customer.failed_payments}")
            evidence.append(f"customer_ltv={customer.lifetime_value}")
        if history:
            evidence.append(f"customer_historical_retry_success_rate={history.retry_success_rate}")
        if attempts:
            evidence.append(f"last_gateway_used={attempts[-1].gateway_name}")

        reason = self._synthesize_reason(top_decision, failure_code, customer, retry_count)

        explanation = OpportunityExplanation(
            decision=top_decision,
            confidence=combined_confidence,
            expected_value=expected_net_value,
            risk=risk_level,
            urgency=urgency,
            evidence=evidence,
            alternatives_considered=alternatives,
            reason=reason,
        )

        return OpportunityResult(
            payment_id=str(payment.id),
            merchant_id=str(payment.merchant_id),
            customer_id=str(payment.customer_id),
            order_id=str(payment.order_id),
            amount=amount,
            currency=currency,
            is_eligible=True,
            failure_category=failure_code,
            recommended_strategy=top_decision,
            opportunity_score=normalized_score,
            score_breakdown=score_breakdown,
            explanation=explanation,
            candidate_strategies=recommended_candidates,
            blocked_strategies=blocked_candidates,
            is_financial_action_executed=False,
        )

    def _synthesize_reason(self, decision: str, failure_code: str, customer: Any, retry_count: int) -> str:
        from services.opportunities.failure_intelligence import FAILURE_PROFILES
        if failure_code not in FAILURE_PROFILES:
            return f"Unrecognized failure code '{failure_code}'. Manual investigation required before any automated action."
        if failure_code == "FRAUD_SUSPECTED":
            return "Transaction flagged with high-confidence fraud heuristics. Autonomous retries are strictly blocked to avoid chargeback penalties; human review required."

        if failure_code == "CARD_EXPIRED":
            return "Payment credential is expired. Retrying against card networks incurs network non-compliance penalties; automated credential update request recommended."
        if failure_code == "BANK_TIMEOUT":
            if customer and customer.successful_payments > 0:
                return "Transient network/acquirer timeout for an established customer. Scheduled retry provides high probability of authorization."
            return "Transient banking timeout observed. Safe automated retry recommended once issuing bank clears backlog."
        if failure_code == "INSUFFICIENT_FUNDS":
            return "Cardholder account balance depletion detected. Immediate retries burn velocity limits; scheduled delay or alternate payment link recommended."
        if retry_count >= 2:
            return f"Multiple retry attempts ({retry_count}) failed. Shifting from direct gateway retries to customer payment link outreach."
        return f"Payment failure classified as {failure_code}. Recommended strategy '{decision}' optimizes recoverable value while adhering to risk policy."

    def _build_ineligible_result(self, context: FullMoneyContext, reason: str) -> OpportunityResult:
        payment = context.payment
        zero = decimal.Decimal("0.00")
        return OpportunityResult(
            payment_id=str(payment.id),
            merchant_id=str(payment.merchant_id),
            customer_id=str(payment.customer_id),
            order_id=str(payment.order_id),
            amount=payment.amount,
            currency=payment.currency,
            is_eligible=False,
            failure_category=context.failure.failure_code if context.failure else None,
            recommended_strategy=CandidateStrategy.NO_ACTION.value,
            opportunity_score=0.0,
            score_breakdown=ScoreBreakdown(
                expected_value=zero,
                success_probability=0.0,
                confidence=1.0,
                urgency_multiplier=0.0,
                risk_cost=zero,
                action_cost=zero,
                raw_score=0.0,
                normalized_score=0.0,
            ),
            explanation=OpportunityExplanation(
                decision=CandidateStrategy.NO_ACTION.value,
                confidence=1.0,
                expected_value=zero,
                risk="NONE",
                urgency="NONE",
                evidence=[f"payment_status={payment.status}"],
                alternatives_considered=[],
                reason=reason,
            ),
            candidate_strategies=[CandidateStrategy.NO_ACTION.value],
            blocked_strategies=[
                CandidateStrategy.RETRY_NOW.value,
                CandidateStrategy.WAIT_AND_RETRY.value,
                CandidateStrategy.SEND_PAYMENT_LINK.value,
                CandidateStrategy.UPDATE_PAYMENT_METHOD.value,
            ],
            is_financial_action_executed=False,
        )

    def _build_limit_exceeded_result(
        self, context: FullMoneyContext, profile: FailureProfile, retry_count: int
    ) -> OpportunityResult:
        payment = context.payment
        zero = decimal.Decimal("0.00")
        decision = CandidateStrategy.SEND_PAYMENT_LINK.value
        return OpportunityResult(
            payment_id=str(payment.id),
            merchant_id=str(payment.merchant_id),
            customer_id=str(payment.customer_id),
            order_id=str(payment.order_id),
            amount=payment.amount,
            currency=payment.currency,
            is_eligible=True,
            failure_category=profile.category,
            recommended_strategy=decision,
            opportunity_score=35.0,
            score_breakdown=ScoreBreakdown(
                expected_value=payment.amount * decimal.Decimal("0.40"),
                success_probability=0.40,
                confidence=0.85,
                urgency_multiplier=0.80,
                risk_cost=zero,
                action_cost=decimal.Decimal("0.05"),
                raw_score=35.0,
                normalized_score=35.0,
            ),
            explanation=OpportunityExplanation(
                decision=decision,
                confidence=0.85,
                expected_value=payment.amount * decimal.Decimal("0.40"),
                risk="MEDIUM",
                urgency="LOW",
                evidence=[
                    f"retry_count={retry_count}",
                    f"max_retry_limit_reached={self.MAX_RETRIES_LIMIT}",
                    f"failure_category={profile.category}",
                ],
                alternatives_considered=[CandidateStrategy.HUMAN_REVIEW.value, CandidateStrategy.NO_ACTION.value],
                reason=f"Maximum gateway attempt limit ({self.MAX_RETRIES_LIMIT}) reached. Direct retries blocked to protect merchant reputation; shifting to customer payment link.",
            ),
            candidate_strategies=[decision, CandidateStrategy.HUMAN_REVIEW.value],
            blocked_strategies=[CandidateStrategy.RETRY_NOW.value, CandidateStrategy.WAIT_AND_RETRY.value],
            is_financial_action_executed=False,
        )


class AIDecisionProvider(DecisionProvider):
    """AI Decision Provider interface with strict Pydantic validation and prompt injection defense.

    Core Invariant:
    AI recommends. Deterministic policy authorizes. Deterministic action layer executes.
    Any AI failure, schema violation, or hallucination automatically falls back to
    the DeterministicDecisionProvider.
    """

    def __init__(self, fallback_provider: Optional[DecisionProvider] = None):
        self.fallback_provider = fallback_provider or DeterministicDecisionProvider()

    def evaluate(self, context: FullMoneyContext) -> OpportunityResult:
        """Evaluate with prompt sanitization and deterministic fallback."""
        from services.opportunities.ai_reasoning import PromptInjectionSanitizer

        # 1. Sanitize untrusted metadata in context
        if context.customer:
            context.customer.name = PromptInjectionSanitizer.sanitize_untrusted_text(context.customer.name)
        if context.failure:
            context.failure.raw_message = PromptInjectionSanitizer.sanitize_untrusted_text(context.failure.raw_message)

        # 2. Evaluate through deterministic baseline
        return self.fallback_provider.evaluate(context)

    def evaluate_with_ai_recommendation(
        self,
        context: FullMoneyContext,
        ai_recommendation: Any,
    ) -> OpportunityResult:
        """Evaluate contextual Money Graph with explicit AI reasoning validation."""
        from services.opportunities.ai_reasoning import (
            AIReasoningValidator,
            AIStrategyRecommendation,
            PromptInjectionSanitizer,
        )

        # 1. Sanitize untrusted customer/order inputs
        if context.customer:
            context.customer.name = PromptInjectionSanitizer.sanitize_untrusted_text(context.customer.name)
        if context.failure:
            context.failure.raw_message = PromptInjectionSanitizer.sanitize_untrusted_text(context.failure.raw_message)

        # 2. Validate AI structured output
        if isinstance(ai_recommendation, dict):
            try:
                ai_recommendation = AIStrategyRecommendation(**ai_recommendation)
            except Exception:
                # Validation error -> fall back
                return self.fallback_provider.evaluate(context)

        if not isinstance(ai_recommendation, AIStrategyRecommendation):
            return self.fallback_provider.evaluate(context)

        is_valid, error = AIReasoningValidator.validate_recommendation(ai_recommendation, context)
        if not is_valid:
            # Hallucination or invalid evidence -> fall back to deterministic provider
            return self.fallback_provider.evaluate(context)

        # 3. Valid recommendation: incorporate into opportunity result
        base_result = self.fallback_provider.evaluate(context)
        if not base_result.is_eligible:
            return base_result

        # Update strategy and explanation with validated AI diagnosis
        base_result.recommended_strategy = ai_recommendation.recommended_strategy.value
        base_result.explanation.decision = ai_recommendation.recommended_strategy.value
        base_result.explanation.reason = f"[AI-Guided Diagnosis] {ai_recommendation.diagnosis}"
        base_result.score_breakdown.model_confidence = ai_recommendation.confidence_score
        return base_result
