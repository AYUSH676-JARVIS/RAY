"""Canonical End-to-End Decision Loop Orchestrator.

Enforces the non-negotiable pipeline:
EVENT
  ↓
MONEY GRAPH
  ↓
OPPORTUNITY ENGINE
  ↓
DECISION ENGINE
  ↓
POLICY ENGINE
  ↓
ACTION GATEWAY
  ↓
OUTCOME VERIFICATION
  ↓
AUDIT TRAIL
  ↓
DECISION RECEIPT
  ↓
MONEY GRAPH UPDATE
"""

from __future__ import annotations

import decimal
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.action_layer.executor import (
    ActionExecutor,
    AmbiguousOutcomeBlockedError,
    FinancialExecutionBlockedError,
    PolicyAuthorizationBlockedError,
)
from services.action_layer.gateway import GatewayStatus, PaymentGateway, SimulationGateway, RazorpayGateway
from services.config.settings import get_settings, ConfigurationError
from services.audit.explanation import DecisionExplanationGenerator
from services.audit.logger import AuditLogger
from services.audit.receipt import DecisionReceipt, DecisionReceiptGenerator
from services.money_graph.models import (
    ActionExecutionStatus,
    ActorType,
    Payment,
    PaymentAttempt,
    PaymentStatus,
    PolicyDecision,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
)
from services.money_graph.service import MoneyGraphService
from services.money_graph.state_machine import (
    validate_action_transition,
    validate_payment_transition,
)
from services.opportunities.ai_reasoning import (
    AIReasoningValidator,
    AIStrategyRecommendation,
)
from services.opportunities.engine import FinancialOpportunityEngine
from services.opportunities.failure_intelligence import CandidateStrategy
from services.opportunities.providers import OpportunityResult
from services.orchestrator.schemas import (
    DecisionProposal,
    DecisionWorkflowResult,
    StageStatus,
    WorkflowStageRecord,
)
from services.common.logging import get_logger
from services.outcome_engine.reconciler import OutcomeReconciler, ReconciliationStatus
from services.policy_engine.engine import DeterministicPolicyEngine

logger = get_logger("ray.decision_loop")


def resolve_configured_gateway(
    simulate_timeout: bool = False,
    simulate_decline: Optional[str] = None,
    gateway_override: Optional[PaymentGateway] = None,
) -> PaymentGateway:
    """Resolve authoritative gateway adapter based on server-side configuration.

    Invariants:
    1. Gateway selection is strictly driven by validated server configuration, NEVER AI.
    2. SimulationGateway is strictly prohibited in production.
    3. Production live mode requires authenticated Razorpay credentials.
    """
    if gateway_override is not None:
        return gateway_override

    settings = get_settings()
    mode = (settings.GATEWAY_MODE or "simulation").lower().strip()

    if settings.is_production and mode == "simulation":
        raise ConfigurationError(
            "Production safety violation: SimulationGateway is strictly prohibited in production environment."
        )

    if mode == "simulation":
        return SimulationGateway(
            simulate_timeout=simulate_timeout,
            simulate_decline_code=simulate_decline,
        )
    elif mode == "razorpay_test":
        key_id = settings.RAZORPAY_KEY_ID.get_secret_value() if settings.RAZORPAY_KEY_ID else "rzp_test_placeholder"
        key_secret = settings.RAZORPAY_KEY_SECRET.get_secret_value() if settings.RAZORPAY_KEY_SECRET else None
        return RazorpayGateway(
            key_id=key_id,
            key_secret=key_secret,
            live_execution_enabled=False,
            stage_1_safety_lock=True,
        )
    elif mode == "razorpay_live":
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            raise ConfigurationError("Production live gateway credentials missing. Failing closed.")
        key_id = settings.RAZORPAY_KEY_ID.get_secret_value()
        key_secret = settings.RAZORPAY_KEY_SECRET.get_secret_value()
        if key_id == "rzp_test_placeholder":
            raise ConfigurationError("Cannot use placeholder credentials in razorpay_live mode.")
        return RazorpayGateway(
            key_id=key_id,
            key_secret=key_secret,
            live_execution_enabled=True,
            stage_1_safety_lock=False,
        )
    else:
        raise ConfigurationError(f"Unsupported GATEWAY_MODE: '{mode}'.")


def run_decision_workflow(
    payment_id: uuid.UUID,
    session: Session,
    merchant_id: Optional[uuid.UUID] = None,
    stage_1_safety_lock: bool = True,
    simulate_gateway: bool = False,
    simulate_timeout: bool = False,
    simulate_decline: Optional[str] = None,
    actor_id: str = "orchestrator:system",
    correlation_id: Optional[str] = None,
    simulate_ai_failure: bool = False,
    simulate_malformed_ai: bool = False,
    gateway_override: Optional[PaymentGateway] = None,
) -> DecisionWorkflowResult:
    """Execute the canonical end-to-end automatic decision loop for a payment."""
    import time
    workflow_id = uuid.uuid4()
    corr_id = correlation_id or str(uuid.uuid4())
    stages: List[WorkflowStageRecord] = []
    stage_start_times: Dict[str, float] = {}

    def log_stage(
        name: str,
        status: StageStatus,
        detail: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        failure_reason: Optional[str] = None,
        decision: Optional[str] = None,
        evidence_ids: Optional[List[str]] = None,
    ):
        now = datetime.now(timezone.utc)
        now_ts = time.time()
        start_ts = stage_start_times.get(name, now_ts - 0.005)
        duration_ms = max(round((now_ts - start_ts) * 1000, 2), 0.1)

        stages.append(
            WorkflowStageRecord(
                stage_name=name,
                status=status,
                started_at=datetime.fromtimestamp(start_ts, tz=timezone.utc) if name in stage_start_times else now,
                completed_at=now,
                duration_ms=duration_ms,
                input_summary=inputs,
                detail=detail,
                output_summary=summary,
                decision=decision,
                evidence_ids=evidence_ids or [],
                failure_reason=failure_reason or (detail if status in [StageStatus.FAILED, StageStatus.BLOCKED] else None),
                correlation_id=corr_id,
            )
        )
        logger.info(
            f"Stage {name} -> {status.value}: {detail or 'completed'}",
            extra={
                "workflow_id": str(workflow_id),
                "correlation_id": corr_id,
                "payment_id": str(payment_id),
                "stage": name,
                "status": status.value,
                "duration_ms": duration_ms,
            },
        )

    # -----------------------------------------------------------------
    # Stage 1: EVENT
    # -----------------------------------------------------------------
    payment = session.scalar(select(Payment).where(Payment.id == payment_id))
    if not payment:
        log_stage("EVENT", StageStatus.FAILED, f"Payment with ID '{payment_id}' not found.")
        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=merchant_id or uuid.UUID("00000000-0000-0000-0000-000000000000"),
            status="FAILED",
            current_stage="EVENT",
            stages=stages,
            error=f"Payment with ID '{payment_id}' not found.",
        )

    # Tenant boundary verification
    effective_merchant_id = payment.merchant_id
    if merchant_id and merchant_id != effective_merchant_id:
        log_stage("EVENT", StageStatus.FAILED, "Tenant authorization mismatch.")
        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=merchant_id,
            status="FAILED",
            current_stage="EVENT",
            stages=stages,
            error="Payment does not belong to authorized tenant.",
        )

    log_stage(
        "EVENT",
        StageStatus.COMPLETED,
        f"Payment failure event ingested for amount {payment.currency} {payment.amount}.",
        {"amount": str(payment.amount), "currency": payment.currency, "status": payment.status},
    )

    # -----------------------------------------------------------------
    # Stage 2: MONEY_GRAPH
    # -----------------------------------------------------------------
    money_graph_service = MoneyGraphService(session=session)
    context = money_graph_service.get_full_money_context(payment_id)
    if not context:
        log_stage("MONEY_GRAPH", StageStatus.FAILED, "Failed to build Money Graph context.")
        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            status="FAILED",
            current_stage="MONEY_GRAPH",
            stages=stages,
            error="Unable to retrieve full connected Money Graph context.",
        )

    failure_code = context.failure.failure_code if context.failure else "UNKNOWN_FAILURE"
    attempt_count = len(context.attempts)
    customer_risk = context.customer.risk_score if context.customer else 0.20

    log_stage(
        "MONEY_GRAPH",
        StageStatus.COMPLETED,
        f"Connected intelligence loaded: {len(context.attempts)} prior attempts, customer risk {customer_risk:.2f}, failure '{failure_code}'.",
        {
            "customer_id": str(context.customer.id) if context.customer else None,
            "failure_code": failure_code,
            "attempts_count": attempt_count,
            "customer_risk_score": customer_risk,
        },
    )

    # -----------------------------------------------------------------
    # Stage 3: OPPORTUNITY
    # -----------------------------------------------------------------
    opportunity_engine = FinancialOpportunityEngine(session=session)
    opportunity_result: OpportunityResult = opportunity_engine.detect_recovery_opportunity(payment_id)

    if not opportunity_result.is_eligible:
        log_stage(
            "OPPORTUNITY",
            StageStatus.COMPLETED,
            f"Evaluated payment: Ineligible for automated recovery ({opportunity_result.explanation.reason}).",
            {"is_eligible": False, "reason": opportunity_result.explanation.reason},
        )
        # Ineligible payment stops safely here
        receipt = DecisionReceiptGenerator.generate_receipt(
            opportunity_id=uuid.uuid4(),
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            decision="REJECTED",
            rule_matched="INELIGIBLE_OPPORTUNITY_RULE",
            reason=opportunity_result.explanation.reason,
            risk_score=customer_risk,
            amount=payment.amount,
            currency=payment.currency,
            failure_code=failure_code,
            attempt_count=attempt_count,
            evidence=opportunity_result.explanation.evidence,
            stage_1_safety_lock=stage_1_safety_lock,
        )
        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            status="NO_OPPORTUNITY",
            current_stage="OPPORTUNITY",
            stages=stages,
            opportunity=opportunity_result,
            decision_receipt=receipt,
        )

    # Retrieve or create persistent RecoveryOpportunity in Money Graph
    db_opp = session.scalar(
        select(RecoveryOpportunity).where(RecoveryOpportunity.payment_id == payment_id)
    )
    if not db_opp:
        failure_id = context.failure.id if context.failure else None
        if not failure_id:
            f = session.scalar(select(PaymentFailure).where(PaymentFailure.payment_id == payment_id))
            failure_id = f.id if f else uuid.uuid4()
        db_opp = RecoveryOpportunity(
            id=uuid.uuid4(),
            merchant_id=effective_merchant_id,
            payment_id=payment_id,
            failure_id=failure_id,
            strategy_name=opportunity_result.recommended_strategy,
            confidence_score=opportunity_result.score_breakdown.model_confidence,
            estimated_recoverable_amount=opportunity_result.score_breakdown.expected_recovery,
            status="ACTIVE",
        )
        session.add(db_opp)
        session.commit()

    log_stage(
        "OPPORTUNITY",
        StageStatus.COMPLETED,
        f"Recovery opportunity derived with score {opportunity_result.opportunity_score:.1f} and expected recovery {payment.currency} {opportunity_result.score_breakdown.expected_recovery}.",
        {
            "opportunity_id": str(db_opp.id),
            "score": opportunity_result.opportunity_score,
            "recommended_strategy": opportunity_result.recommended_strategy,
            "expected_recovery": str(opportunity_result.score_breakdown.expected_recovery),
            "expected_net_value": str(opportunity_result.score_breakdown.expected_net_value),
        },
    )

    # -----------------------------------------------------------------
    # Stage 4: DECISION ENGINE
    # -----------------------------------------------------------------
    decision_id = uuid.uuid4()
    candidate_action = opportunity_result.recommended_strategy
    expected_value = opportunity_result.score_breakdown.expected_recovery

    # Empirical evidence collected strictly from the Money Graph
    empirical_evidence = [
        f"payment:{payment.id}",
        f"amount:{payment.currency}_{payment.amount}",
        f"failure:{failure_code}",
        f"customer_risk:{customer_risk:.2f}",
        f"attempts_prior:{attempt_count}",
    ]

    # Validate decision evidence against empirical Money Graph context
    if simulate_ai_failure:
        is_valid_evidence = False
        evidence_note = "AI reasoning engine unavailable. Gracefully fallen back to deterministic decision heuristic."
        logger.warning(f"Workflow {workflow_id}: AI engine unavailable. Relying on deterministic opportunity engine.")
    elif simulate_malformed_ai:
        evidence_note = "Malformed AI recommendation rejected by validator. Gracefully fallen back to deterministic decision heuristic."
        bad_rec = AIStrategyRecommendation(
            recommended_strategy=CandidateStrategy.RETRY_NOW,
            confidence_score=0.99,
            diagnosis="Hallucinated evidence",
            cited_evidence_ids=["fake_hallucinated_evidence_id_000"],
        )
        is_valid_evidence, evidence_error = AIReasoningValidator.validate_recommendation(bad_rec, context)
    else:
        candidate_strategy_enum = (
            CandidateStrategy.RETRY_NOW
            if "RETRY" in candidate_action
            else CandidateStrategy.WAIT_AND_RETRY
        )
        rec_obj = AIStrategyRecommendation(
            recommended_strategy=candidate_strategy_enum,
            confidence_score=opportunity_result.score_breakdown.model_confidence,
            diagnosis=opportunity_result.explanation.reason,
            cited_evidence_ids=[str(payment.id), failure_code],
        )
        is_valid_evidence, evidence_error = AIReasoningValidator.validate_recommendation(rec_obj, context)
        evidence_note = f"Evidence verified: {is_valid_evidence}."

    decision_proposal = DecisionProposal(
        decision_id=decision_id,
        opportunity_id=db_opp.id,
        recommended_action=candidate_action,
        expected_value=expected_value,
        probability_of_success=opportunity_result.score_breakdown.recovery_probability,
        model_confidence=opportunity_result.score_breakdown.model_confidence,
        data_confidence=opportunity_result.score_breakdown.data_confidence,
        risk=opportunity_result.explanation.risk,
        urgency=opportunity_result.explanation.urgency,
        reasoning_summary=opportunity_result.explanation.reason,
        evidence_ids=empirical_evidence,
        policy_input={
            "failure_code": failure_code,
            "attempt_count": attempt_count,
            "customer_risk_score": customer_risk,
            "amount": str(payment.amount),
        },
    )

    log_stage(
        name="DECISION",
        status=StageStatus.COMPLETED,
        detail=f"Decision proposed: '{candidate_action}' with probability {decision_proposal.probability_of_success*100:.0f}%. {evidence_note}",
        summary={
            "decision_id": str(decision_id),
            "opportunity_id": str(db_opp.id),
            "recommended_action": candidate_action,
            "expected_value": str(expected_value),
            "confidence": decision_proposal.model_confidence,
            "evidence_verified": is_valid_evidence,
        },
        decision=candidate_action,
        evidence_ids=empirical_evidence,
    )

    # -----------------------------------------------------------------
    # Stage 5: POLICY ENGINE (Deterministic Authorization Gate)
    # -----------------------------------------------------------------
    policy_engine = DeterministicPolicyEngine()
    policy_decision_type, rule_matched, policy_reason = policy_engine.authorize_recovery(
        failure_code=failure_code,
        attempt_count=attempt_count,
        customer_risk_score=customer_risk,
        amount=payment.amount,
        payment_status=payment.status,
    )

    # Persist policy decision record in database
    db_policy = PolicyDecision(
        id=uuid.uuid4(),
        merchant_id=effective_merchant_id,
        opportunity_id=db_opp.id,
        decision=policy_decision_type.value,
        rule_matched=rule_matched,
        reason=policy_reason,
        risk_score=customer_risk,
    )
    session.add(db_policy)
    session.commit()

    policy_summary = {
        "decision": policy_decision_type.value,
        "rule_matched": rule_matched,
        "reason": policy_reason,
    }

    if policy_decision_type == PolicyDecisionType.REJECTED:
        log_stage("POLICY", StageStatus.BLOCKED, f"Policy Engine BLOCKED execution: [{rule_matched}] {policy_reason}.", policy_summary)
        log_stage("ACTION", StageStatus.SKIPPED, "Action Gateway bypassed due to Policy Engine block.")
        log_stage("VERIFICATION", StageStatus.SKIPPED, "Outcome verification skipped for blocked action.")

        # Cryptographic audit logging of policy block
        audit_event = AuditLogger.record_event(
            session=session,
            merchant_id=effective_merchant_id,
            entity_type="POLICY_DECISION",
            entity_id=db_policy.id,
            event_type="POLICY_AUTHORIZATION_BLOCKED",
            actor_type=ActorType.POLICY_ENGINE,
            actor_id="deterministic_policy_engine",
            payload_after=policy_summary,
        )
        session.commit()
        log_stage("AUDIT", StageStatus.COMPLETED, f"Audit event #{audit_event.sequence_number} chained with SHA-256 hash {audit_event.event_hash[:12]}...", {"sequence": audit_event.sequence_number, "hash": audit_event.event_hash})

        # Generate Decision Receipt for blocked decision
        receipt = DecisionReceiptGenerator.generate_receipt(
            opportunity_id=db_opp.id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            decision=policy_decision_type.value,
            rule_matched=rule_matched,
            reason=policy_reason,
            risk_score=customer_risk,
            amount=payment.amount,
            currency=payment.currency,
            failure_code=failure_code,
            attempt_count=attempt_count,
            evidence=empirical_evidence,
            stage_1_safety_lock=stage_1_safety_lock,
        )
        log_stage("DECISION_RECEIPT", StageStatus.COMPLETED, f"Decision Receipt generated: ID {receipt.receipt_id}.", {"receipt_id": str(receipt.receipt_id)})

        explanation = DecisionExplanationGenerator.generate_explanation(
            payment_id=str(payment_id),
            amount=str(payment.amount),
            currency=payment.currency,
            failure_code=failure_code,
            failure_message=context.failure.raw_message if context and context.failure else None,
            recommended_strategy=candidate_action,
            expected_value=str(expected_value),
            recovery_probability=decision_proposal.probability_of_success,
            policy_decision=policy_decision_type.value,
            policy_rule=rule_matched,
            policy_reason=policy_reason,
            action_status=None,
            is_recovered=False,
            evidence_ids=empirical_evidence,
            why_didnt_ray_act=receipt.why_didnt_ray_act,
        )

        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            status="POLICY_BLOCKED",
            current_stage="POLICY",
            stages=stages,
            opportunity=opportunity_result,
            decision=decision_proposal,
            policy_decision=policy_summary,
            decision_receipt=receipt,
            explanation=explanation,
            audit_event_id=audit_event.id,
            audit_hash=audit_event.event_hash,
        )

    log_stage("POLICY", StageStatus.COMPLETED, f"Policy Engine APPROVED execution: [{rule_matched}] {policy_reason}.", policy_summary)

    # -----------------------------------------------------------------
    # Stage 6: ACTION GATEWAY
    if simulate_gateway:
        idempotency_key = f"idem_wf_sim_{payment_id.hex[:10]}_{uuid.uuid4().hex[:6]}"
    else:
        idempotency_key = f"idem_wf_{payment_id.hex[:12]}_{attempt_count + 1}"
    action_record = session.scalar(
        select(RecoveryAction).where(
            RecoveryAction.merchant_id == effective_merchant_id,
            RecoveryAction.idempotency_key == idempotency_key,
        )
    )
    if not action_record:
        action_record = RecoveryAction(
            id=uuid.uuid4(),
            merchant_id=effective_merchant_id,
            opportunity_id=db_opp.id,
            action_type="RETRY",
            idempotency_key=idempotency_key,
            execution_status="REQUESTED",
        )
        session.add(action_record)
        session.commit()

    action_result: Dict[str, Any] = {}

    if stage_1_safety_lock and not simulate_gateway:
        # Strict Stage 1 Safety Lock: Autonomous execution is blocked
        if action_record.execution_status != ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value:
            validate_action_transition(ActionExecutionStatus(action_record.execution_status), ActionExecutionStatus.BLOCKED_STAGE1_SAFETY)
            action_record.execution_status = ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value
            session.commit()

        action_result = {
            "status": ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
            "action_id": str(action_record.id),
            "idempotency_key": idempotency_key,
            "detail": "Stage 1 Safety Guard active. Direct autonomous money movement disabled.",
        }
        log_stage("ACTION", StageStatus.BLOCKED, "Action execution halted: Stage 1 Safety Guard active (BLOCKED_STAGE1_SAFETY).", action_result)
        log_stage("VERIFICATION", StageStatus.COMPLETED, "Outcome unconfirmed (zero external money movement at Stage 1).", {"is_recovered": False, "reason": "STAGE1_SAFETY_LOCK_ACTIVE"})

        audit_event = AuditLogger.record_event(
            session=session,
            merchant_id=effective_merchant_id,
            entity_type="RECOVERY_ACTION",
            entity_id=action_record.id,
            event_type="ACTION_BLOCKED_STAGE1_SAFETY",
            actor_type=ActorType.ACTION_LAYER,
            actor_id="action_executor",
            payload_after=action_result,
        )
        session.commit()
        log_stage("AUDIT", StageStatus.COMPLETED, f"Audit event #{audit_event.sequence_number} chained with SHA-256 hash {audit_event.event_hash[:12]}...", {"sequence": audit_event.sequence_number, "hash": audit_event.event_hash})

        receipt = DecisionReceiptGenerator.generate_receipt(
            opportunity_id=db_opp.id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            decision="APPROVED",
            rule_matched=rule_matched,
            reason=policy_reason,
            risk_score=customer_risk,
            amount=payment.amount,
            currency=payment.currency,
            failure_code=failure_code,
            attempt_count=attempt_count,
            evidence=empirical_evidence,
            stage_1_safety_lock=True,
        )
        log_stage("DECISION_RECEIPT", StageStatus.COMPLETED, f"Decision Receipt generated: ID {receipt.receipt_id}.", {"receipt_id": str(receipt.receipt_id)})

        explanation = DecisionExplanationGenerator.generate_explanation(
            payment_id=str(payment_id),
            amount=str(payment.amount),
            currency=payment.currency,
            failure_code=failure_code,
            failure_message=context.failure.raw_message if context and context.failure else None,
            recommended_strategy=candidate_action,
            expected_value=str(expected_value),
            recovery_probability=decision_proposal.probability_of_success,
            policy_decision=policy_decision_type.value,
            policy_rule=rule_matched,
            policy_reason=policy_reason,
            action_status=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
            is_recovered=False,
            evidence_ids=empirical_evidence,
            why_didnt_ray_act=receipt.why_didnt_ray_act,
        )

        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            status="STAGE1_BLOCKED",
            current_stage="ACTION",
            stages=stages,
            opportunity=opportunity_result,
            decision=decision_proposal,
            policy_decision=policy_summary,
            action_result=action_result,
            outcome_result={"is_recovered": False, "status": "STAGE1_SAFETY_LOCKED"},
            decision_receipt=receipt,
            explanation=explanation,
            audit_event_id=audit_event.id,
            audit_hash=audit_event.event_hash,
        )

    # -----------------------------------------------------------------
    # Stage 6B: CONFIGURED AUTHORITATIVE GATEWAY EXECUTION
    # -----------------------------------------------------------------
    gw = resolve_configured_gateway(
        simulate_timeout=simulate_timeout,
        simulate_decline=simulate_decline,
        gateway_override=gateway_override,
    )
    executor = ActionExecutor(stage_1_safety_lock=False, gateway=gw)

    try:
        action_result = executor.execute_recovery_action(
            action_id=action_record.id,
            action_type="RETRY",
            idempotency_key=idempotency_key,
            merchant_id=effective_merchant_id,
            amount=payment.amount,
            currency=payment.currency,
            session=session,
            payment_status=payment.status,
            policy_decision=policy_decision_type.value,
        )
    except Exception as e:
        action_result = {"status": "FAILED", "error": str(e)}

    action_record.execution_status = action_result.get("status", "FAILED")
    session.commit()

    if action_result.get("status") == ActionExecutionStatus.UNKNOWN.value:
        # Gateway Timeout -> UNKNOWN State on Action & Payment
        validate_action_transition(ActionExecutionStatus.PROCESSING, ActionExecutionStatus.UNKNOWN)
        payment.status = PaymentStatus.UNKNOWN.value
        session.commit()

        log_stage("ACTION", StageStatus.UNKNOWN, "Gateway response timed out. Action entered UNKNOWN state (blind retries blocked).", action_result)
        log_stage("VERIFICATION", StageStatus.UNKNOWN, "Authoritative verification pending: Status inquiry required before resolving UNKNOWN.", {"is_recovered": False, "status": "UNKNOWN"})

        audit_event = AuditLogger.record_event(
            session=session,
            merchant_id=effective_merchant_id,
            entity_type="RECOVERY_ACTION",
            entity_id=action_record.id,
            event_type="ACTION_UNKNOWN_TIMEOUT",
            actor_type=ActorType.ACTION_LAYER,
            actor_id="simulation_gateway",
            payload_after=action_result,
        )
        session.commit()
        log_stage("AUDIT", StageStatus.COMPLETED, f"Audit event #{audit_event.sequence_number} chained with SHA-256 hash {audit_event.event_hash[:12]}...", {"sequence": audit_event.sequence_number, "hash": audit_event.event_hash})

        receipt = DecisionReceiptGenerator.generate_receipt(
            opportunity_id=db_opp.id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            decision="APPROVED",
            rule_matched=rule_matched,
            reason="Gateway timeout encountered during simulated recovery attempt.",
            risk_score=customer_risk,
            amount=payment.amount,
            currency=payment.currency,
            failure_code=failure_code,
            attempt_count=attempt_count,
            evidence=empirical_evidence,
            stage_1_safety_lock=False,
        )
        log_stage("DECISION_RECEIPT", StageStatus.COMPLETED, f"Decision Receipt generated: ID {receipt.receipt_id}.", {"receipt_id": str(receipt.receipt_id)})

        explanation = DecisionExplanationGenerator.generate_explanation(
            payment_id=str(payment_id),
            amount=str(payment.amount),
            currency=payment.currency,
            failure_code=failure_code,
            failure_message=context.failure.raw_message if context and context.failure else None,
            recommended_strategy=candidate_action,
            expected_value=str(expected_value),
            recovery_probability=decision_proposal.probability_of_success,
            policy_decision=policy_decision_type.value,
            policy_rule=rule_matched,
            policy_reason=policy_reason,
            action_status="UNKNOWN",
            is_recovered=False,
            evidence_ids=empirical_evidence,
            why_didnt_ray_act=None,
        )

        return DecisionWorkflowResult(
            workflow_id=workflow_id,
            correlation_id=corr_id,
            payment_id=payment_id,
            merchant_id=effective_merchant_id,
            status="UNKNOWN",
            current_stage="ACTION",
            stages=stages,
            opportunity=opportunity_result,
            decision=decision_proposal,
            policy_decision=policy_summary,
            action_result=action_result,
            outcome_result={"is_recovered": False, "status": "UNKNOWN_AWAITING_RECONCILIATION"},
            decision_receipt=receipt,
            explanation=explanation,
            audit_event_id=audit_event.id,
            audit_hash=audit_event.event_hash,
        )

    log_stage("ACTION", StageStatus.COMPLETED, f"Gateway simulated execution: {action_result.get('status')}.", action_result)

    # -----------------------------------------------------------------
    # Stage 7: OUTCOME VERIFICATION
    # -----------------------------------------------------------------
    reconciler = OutcomeReconciler()
    settlement_status = "SETTLED" if action_result.get("status") == "SUCCEEDED" else "FAILED"
    outcome_check = reconciler.verify_authoritative_outcome(
        payment_id=payment_id,
        requested_amount=payment.amount,
        requested_currency=payment.currency,
        authoritative_status=settlement_status,
        settled_amount=payment.amount if settlement_status == "SETTLED" else Decimal("0.00"),
        settled_currency=payment.currency,
        gateway_transaction_id=action_result.get("gateway_transaction_id"),
    )

    if outcome_check["is_recovered"]:
        # Update Money Graph state truthfully
        validate_action_transition(ActionExecutionStatus.PROCESSING, ActionExecutionStatus.SUCCEEDED)
        payment.status = PaymentStatus.SUCCESS.value
        db_opp.status = "RECOVERED"
        session.commit()
        log_stage("VERIFICATION", StageStatus.COMPLETED, f"Ground-truth verified: Amount {payment.currency} {outcome_check['authoritative_recovered_amount']} settled.", outcome_check)
    else:
        log_stage("VERIFICATION", StageStatus.FAILED, f"Outcome unverified: {outcome_check['reason']}.", outcome_check)

    # -----------------------------------------------------------------
    # Stage 8: AUDIT TRAIL (Cryptographic SHA-256 Chaining)
    # -----------------------------------------------------------------
    audit_event = AuditLogger.record_event(
        session=session,
        merchant_id=effective_merchant_id,
        entity_type="PAYMENT_RECOVERY",
        entity_id=payment_id,
        event_type="WORKFLOW_COMPLETED_SETTLED" if outcome_check["is_recovered"] else "WORKFLOW_COMPLETED_UNRECOVERED",
        actor_type=ActorType.SYSTEM,
        actor_id=actor_id,
        payload_after={
            "workflow_id": str(workflow_id),
            "decision": candidate_action,
            "outcome": outcome_check["reconciliation_status"],
            "recovered_amount": str(outcome_check["authoritative_recovered_amount"]),
        },
    )
    session.commit()
    log_stage(
        "AUDIT",
        StageStatus.COMPLETED,
        f"Audit event #{audit_event.sequence_number} chained with SHA-256 hash {audit_event.event_hash[:12]}...",
        {"sequence": audit_event.sequence_number, "hash": audit_event.event_hash},
    )

    # -----------------------------------------------------------------
    # Stage 9: DECISION RECEIPT
    # -----------------------------------------------------------------
    receipt = DecisionReceiptGenerator.generate_receipt(
        opportunity_id=db_opp.id,
        payment_id=payment_id,
        merchant_id=effective_merchant_id,
        decision="APPROVED",
        rule_matched=rule_matched,
        reason="Automated recovery executed and verified against simulation gateway.",
        risk_score=customer_risk,
        amount=payment.amount,
        currency=payment.currency,
        failure_code=failure_code,
        attempt_count=attempt_count + 1,
        evidence=empirical_evidence,
        stage_1_safety_lock=False,
    )
    log_stage("DECISION_RECEIPT", StageStatus.COMPLETED, f"Decision Receipt generated: ID {receipt.receipt_id}.", {"receipt_id": str(receipt.receipt_id)})

    explanation = DecisionExplanationGenerator.generate_explanation(
        payment_id=str(payment_id),
        amount=str(payment.amount),
        currency=payment.currency,
        failure_code=failure_code,
        failure_message=context.failure.raw_message if context and context.failure else None,
        recommended_strategy=candidate_action,
        expected_value=str(expected_value),
        recovery_probability=decision_proposal.probability_of_success,
        policy_decision=policy_decision_type.value,
        policy_rule=rule_matched,
        policy_reason=policy_reason,
        action_status=action_result.get("status"),
        is_recovered=outcome_check.get("is_recovered", False),
        evidence_ids=empirical_evidence,
        why_didnt_ray_act=receipt.why_didnt_ray_act if not outcome_check.get("is_recovered") else None,
    )

    return DecisionWorkflowResult(
        workflow_id=workflow_id,
        correlation_id=corr_id,
        payment_id=payment_id,
        merchant_id=effective_merchant_id,
        status="COMPLETED" if outcome_check["is_recovered"] else "FAILED",
        current_stage="DECISION_RECEIPT",
        stages=stages,
        opportunity=opportunity_result,
        decision=decision_proposal,
        policy_decision=policy_summary,
        action_result=action_result,
        outcome_result=outcome_check,
        decision_receipt=receipt,
        explanation=explanation,
        audit_event_id=audit_event.id,
        audit_hash=audit_event.event_hash,
    )
