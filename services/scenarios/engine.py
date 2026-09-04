"""Deterministic Demo Scenario Framework.

Provides reproducible execution for canonical fintech demo scenarios:
1. SCENARIO A: Healthy Recovery (₹2,500 Bank Timeout -> Smart Retry -> Succeeded)
2. SCENARIO B: Fraud Block (₹2,500 Fraud Suspected -> Policy Deny -> Zero Gateway Calls)
3. SCENARIO C: Unknown Gateway (₹2,500 Bank Timeout -> Gateway Timeout -> UNKNOWN State)
4. SCENARIO D: Terminal Decline (₹2,500 Card Expired -> Issuer Declined -> Failed, No Retry)
5. SCENARIO E: Duplicate Request (Replay of identical transaction -> Exactly 1 Execution)
"""

from __future__ import annotations

import enum
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.money_graph.models import (
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
)
from services.orchestrator.schemas import DecisionWorkflowResult
from services.orchestrator.workflow import run_decision_workflow


class ScenarioID(str, enum.Enum):
    SCENARIO_A = "scenario_a"  # Healthy Recovery
    SCENARIO_B = "scenario_b"  # Fraud Block
    SCENARIO_C = "scenario_c"  # Unknown Gateway Timeout
    SCENARIO_D = "scenario_d"  # Terminal Decline
    SCENARIO_E = "scenario_e"  # Duplicate Request Idempotency
    SCENARIO_F = "scenario_f"  # Unknown Outcome & Reconciliation Required



class ScenarioDefinition(BaseModel):
    id: ScenarioID
    title: str
    description: str
    amount: str
    currency: str
    failure_code: str
    customer_risk: float
    expected_recommendation: str
    expected_policy_decision: str
    expected_workflow_status: str
    requires_reconciliation: bool = False


SCENARIO_DEFINITIONS: Dict[ScenarioID, ScenarioDefinition] = {
    ScenarioID.SCENARIO_A: ScenarioDefinition(
        id=ScenarioID.SCENARIO_A,
        title="Scenario A — Healthy Revenue Recovery",
        description="A ₹2,500 transaction encounters a temporary acquiring bank timeout. Low customer risk (0.10) clears deterministic policy. Smart retry recovers the full ₹2,500.",
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        customer_risk=0.10,
        expected_recommendation="WAIT_AND_RETRY",
        expected_policy_decision="APPROVED",
        expected_workflow_status="COMPLETED",
        requires_reconciliation=False,
    ),
    ScenarioID.SCENARIO_B: ScenarioDefinition(
        id=ScenarioID.SCENARIO_B,
        title="Scenario B — Fraud Zero-Tolerance Block",
        description="A ₹2,500 transaction exhibits fraud velocity indicators. Deterministic policy rejects autonomous execution. Exactly 0 gateway calls are dispatched.",
        amount="2500.00",
        currency="INR",
        failure_code="FRAUD_SUSPECTED",
        customer_risk=0.92,
        expected_recommendation="HUMAN_REVIEW",
        expected_policy_decision="REJECTED",
        expected_workflow_status="POLICY_BLOCKED",
        requires_reconciliation=False,
    ),
    ScenarioID.SCENARIO_C: ScenarioDefinition(
        id=ScenarioID.SCENARIO_C,
        title="Scenario C — Gateway Timeout & Ambiguous UNKNOWN",
        description="A ₹2,500 transaction encounters a gateway network timeout during execution. Enters UNKNOWN status. Blind retries are strictly blocked until authoritative evidence reconciles the ledger.",
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        customer_risk=0.10,
        expected_recommendation="WAIT_AND_RETRY",
        expected_policy_decision="APPROVED",
        expected_workflow_status="UNKNOWN",
        requires_reconciliation=True,
    ),
    ScenarioID.SCENARIO_D: ScenarioDefinition(
        id=ScenarioID.SCENARIO_D,
        title="Scenario D — Terminal Cardholder Decline",
        description="A ₹2,500 transaction fails with CARD_EXPIRED. Gateway returns terminal decline. Action layer marks outcome FAILED and prohibits any further retry attempts.",
        amount="2500.00",
        currency="INR",
        failure_code="CARD_EXPIRED",
        customer_risk=0.25,
        expected_recommendation="UPDATE_PAYMENT_METHOD",
        expected_policy_decision="REJECTED",
        expected_workflow_status="POLICY_BLOCKED",
        requires_reconciliation=False,
    ),
    ScenarioID.SCENARIO_E: ScenarioDefinition(
        id=ScenarioID.SCENARIO_E,
        title="Scenario E — Concurrent / Duplicate Request Idempotency",
        description="Multiple identical requests dispatched simultaneously. Transactional idempotency ensures exactly 1 gateway action executes and all subsequent callers receive the identical cached receipt.",
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        customer_risk=0.10,
        expected_recommendation="WAIT_AND_RETRY",
        expected_policy_decision="APPROVED",
        expected_workflow_status="STAGE1_BLOCKED",
        requires_reconciliation=False,
    ),
    ScenarioID.SCENARIO_F: ScenarioDefinition(
        id=ScenarioID.SCENARIO_F,
        title="Scenario F — Ambiguous UNKNOWN Outcome & Reconciliation Required",
        description="A ₹2,500 transaction encounters an unacknowledged gateway disconnect. State locks in UNKNOWN (UNKNOWN != FAILED). Authoritative webhook or status inquiry reconciliation is required before releasing funds.",
        amount="2500.00",
        currency="INR",
        failure_code="BANK_TIMEOUT",
        customer_risk=0.10,
        expected_recommendation="WAIT_AND_RETRY",
        expected_policy_decision="APPROVED",
        expected_workflow_status="UNKNOWN",
        requires_reconciliation=True,
    ),
}


class DemoScenarioEngine:
    """Manages deterministic setup and execution of demo scenarios."""

    @classmethod
    def list_scenarios(cls) -> List[ScenarioDefinition]:
        return list(SCENARIO_DEFINITIONS.values())

    @classmethod
    def ensure_scenario_payment(
        cls,
        session: Session,
        merchant_id: uuid.UUID,
        scenario_id: ScenarioID,
    ) -> Payment:
        """Deterministically locate or construct the exact seed payment for a scenario."""
        defn = SCENARIO_DEFINITIONS[scenario_id]

        # Use deterministic UUID based on merchant_id and scenario_id
        target_payment_id = uuid.uuid5(merchant_id, f"scenario_payment_{scenario_id.value}")

        payment = session.get(Payment, target_payment_id)
        if payment:
            # Ensure failure record matches scenario parameters
            failure = session.scalar(
                select(PaymentFailure).where(PaymentFailure.payment_id == payment.id)
            )
            if not failure:
                attempt_id = uuid.uuid5(merchant_id, f"scenario_attempt_{scenario_id.value}")
                attempt = session.get(PaymentAttempt, attempt_id)
                if not attempt:
                    attempt = PaymentAttempt(
                        id=attempt_id,
                        merchant_id=merchant_id,
                        payment_id=payment.id,
                        attempt_number=1,
                        gateway_name="Razorpay",
                        gateway_transaction_id=f"tx_demo_{scenario_id.value}",
                        status="FAILED",
                        idempotency_key=f"idem_demo_{scenario_id.value}_{attempt_id.hex[:6]}",
                    )
                    session.add(attempt)
                    session.commit()

                failure = PaymentFailure(
                    id=uuid.uuid5(merchant_id, f"scenario_failure_{scenario_id.value}"),
                    merchant_id=merchant_id,
                    payment_id=payment.id,
                    payment_attempt_id=attempt.id,
                    failure_code=defn.failure_code,
                    raw_message=f"Simulated failure for {defn.title}",
                    is_retryable=(defn.failure_code != "CARD_EXPIRED"),
                )
                session.add(failure)
                session.commit()
            else:
                failure.failure_code = defn.failure_code
                payment.status = PaymentStatus.FAILED.value
                session.commit()
            return payment

        # Create fresh customer and order for this scenario
        cust_id = uuid.uuid5(merchant_id, f"scenario_customer_{scenario_id.value}")
        customer = session.get(Customer, cust_id)
        if not customer:
            customer = Customer(
                id=cust_id,
                merchant_id=merchant_id,
                external_id=f"cust_{scenario_id.value}",
                email=f"{scenario_id.value}@demo.merchant.com",
                name=f"Demo Customer {scenario_id.value.upper()}",
                risk_score=defn.customer_risk,
            )
            session.add(customer)
            session.commit()

        order_id = uuid.uuid5(merchant_id, f"scenario_order_{scenario_id.value}")
        order = session.get(Order, order_id)
        if not order:
            order = Order(
                id=order_id,
                merchant_id=merchant_id,
                customer_id=customer.id,
                amount=Decimal(defn.amount),
                currency=defn.currency,
            )
            session.add(order)
            session.commit()

        payment = Payment(
            id=target_payment_id,
            merchant_id=merchant_id,
            order_id=order.id,
            customer_id=customer.id,
            amount=Decimal(defn.amount),
            currency=defn.currency,
            status=PaymentStatus.FAILED.value,
        )
        session.add(payment)
        session.commit()

        # Create or ensure payment attempt exists for foreign key constraint
        attempt_id = uuid.uuid5(merchant_id, f"scenario_attempt_{scenario_id.value}")
        attempt = session.get(PaymentAttempt, attempt_id)
        if not attempt:
            attempt = PaymentAttempt(
                id=attempt_id,
                merchant_id=merchant_id,
                payment_id=payment.id,
                attempt_number=1,
                gateway_name="Razorpay",
                gateway_transaction_id=f"tx_demo_{scenario_id.value}",
                status="FAILED",
                idempotency_key=f"idem_demo_{scenario_id.value}_{attempt_id.hex[:6]}",
            )
            session.add(attempt)
            session.commit()

        failure = PaymentFailure(
            id=uuid.uuid5(merchant_id, f"scenario_failure_{scenario_id.value}"),
            merchant_id=merchant_id,
            payment_id=payment.id,
            payment_attempt_id=attempt.id,
            failure_code=defn.failure_code,
            raw_message=f"Deterministic failure for {defn.title}",
            is_retryable=(defn.failure_code != "CARD_EXPIRED"),
        )
        session.add(failure)
        session.commit()

        return payment

    @classmethod
    def execute_scenario(
        cls,
        session: Session,
        merchant_id: uuid.UUID,
        scenario_id: ScenarioID,
        actor_id: str = "demo:runner",
    ) -> DecisionWorkflowResult:
        """Run the end-to-end decision workflow under deterministic scenario constraints."""
        payment = cls.ensure_scenario_payment(session, merchant_id, scenario_id)

        if scenario_id == ScenarioID.SCENARIO_A:
            # Healthy Recovery: Simulated gateway executes successfully
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=False,
                simulate_gateway=True,
                actor_id=actor_id,
            )
        elif scenario_id == ScenarioID.SCENARIO_B:
            # Fraud Block: Deterministic policy blocks execution
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=True,
                simulate_gateway=True,
                actor_id=actor_id,
            )
        elif scenario_id == ScenarioID.SCENARIO_C:
            # Unknown Gateway: Gateway timeout yields UNKNOWN status
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=False,
                simulate_gateway=True,
                simulate_timeout=True,
                actor_id=actor_id,
            )
        elif scenario_id == ScenarioID.SCENARIO_D:
            # Terminal Decline: Card expired prevents recovery
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=True,
                simulate_gateway=True,
                simulate_decline="CARD_EXPIRED",
                actor_id=actor_id,
            )
        elif scenario_id == ScenarioID.SCENARIO_E:
            # Duplicate Request: Runs under Stage 1 lock safely twice, returns result
            run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=True,
                actor_id=actor_id,
            )
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=True,
                actor_id=actor_id,
            )
        elif scenario_id == ScenarioID.SCENARIO_F:
            return run_decision_workflow(
                payment_id=payment.id,
                session=session,
                merchant_id=merchant_id,
                stage_1_safety_lock=False,
                simulate_gateway=True,
                simulate_timeout=True,
                actor_id=actor_id,
            )
        else:
            raise ValueError(f"Unknown scenario ID: {scenario_id}")

