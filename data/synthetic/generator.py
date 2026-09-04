"""Deterministic synthetic data generator for RAY Merchant Money Intelligence Engine.

Generates:
- 10,000 customers
- 20,000 orders
- 25,000 payments
- 5,000 failed payments with exact required failure categories:
  BANK_TIMEOUT, INSUFFICIENT_FUNDS, ISSUER_DECLINED, AUTHENTICATION_FAILED,
  NETWORK_ERROR, CARD_EXPIRED, LIMIT_EXCEEDED, FRAUD_SUSPECTED
- Linked payment attempts with unique idempotency keys
- Linked payment failures
- Sample recovery opportunities, policy decisions, agent runs, tool calls, and audit events

Reproducible via fixed random seed.
"""

from __future__ import annotations

import datetime
import decimal
import json
import random
import uuid
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from services.money_graph.models import (
    ActionExecutionStatus,
    ActionType,
    ActorType,
    AgentRun,
    AgentRunStatus,
    AuditEvent,
    Customer,
    FailureCategory,
    Merchant,
    OpportunityStatus,
    Order,
    OrderStatus,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PaymentStatus,
    PolicyDecision,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
    ToolCall,
)
from services.audit.logger import AuditLogger


FAILURE_CATEGORIES = [
    FailureCategory.BANK_TIMEOUT,
    FailureCategory.INSUFFICIENT_FUNDS,
    FailureCategory.ISSUER_DECLINED,
    FailureCategory.AUTHENTICATION_FAILED,
    FailureCategory.NETWORK_ERROR,
    FailureCategory.CARD_EXPIRED,
    FailureCategory.LIMIT_EXCEEDED,
    FailureCategory.FRAUD_SUSPECTED,
]

# Realistic error message templates for each category
FAILURE_DETAILS = {
    FailureCategory.BANK_TIMEOUT: (
        "Gateway connection timed out awaiting issuing bank ACQ-504 response.",
        True,
        "SMART_RETRY_WINDOW",
        0.88,
    ),
    FailureCategory.INSUFFICIENT_FUNDS: (
        "Decline code 51: Insufficient funds in cardholder account.",
        True,
        "PAYDAY_OPTIMIZED_RETRY",
        0.74,
    ),
    FailureCategory.ISSUER_DECLINED: (
        "Decline code 05: Do not honor - transaction refused by issuer.",
        True,
        "ROUTING_CASCADE",
        0.62,
    ),
    FailureCategory.AUTHENTICATION_FAILED: (
        "3D-Secure 2.2 authentication challenge failed or expired by customer.",
        True,
        "AUTH_REPAIR",
        0.79,
    ),
    FailureCategory.NETWORK_ERROR: (
        "Socket error 104 connection reset by peer during network packet transmission.",
        True,
        "INSTANT_NETWORK_RETRY",
        0.91,
    ),
    FailureCategory.CARD_EXPIRED: (
        "Decline code 54: Card expired or validity date mismatch.",
        False,
        "CUSTOMER_OUTREACH",
        0.68,
    ),
    FailureCategory.LIMIT_EXCEEDED: (
        "Decline code 61: Exceeded cardholder daily purchase velocity limit.",
        True,
        "NEXT_DAY_RETRY",
        0.72,
    ),
    FailureCategory.FRAUD_SUSPECTED: (
        "Risk score 94 - Transaction flagged by issuing bank real-time fraud heuristic.",
        False,
        "STEP_UP_VERIFICATION",
        0.31,
    ),
}

GATEWAYS = ["Stripe Direct", "Adyen Global", "Checkout.com", "JPMorgan Paymentech"]
FIRST_NAMES = ["James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Oliver", "Sophia", "Lucas", "Isabella", "Ethan", "Mia", "Aiden", "Harper", "Mason", "Evelyn"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson"]


class SyntheticDataGenerator:
    """Generates deterministic datasets for RAY."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_all(
        self,
        session: Session,
        num_customers: int = 10000,
        num_orders: int = 20000,
        num_payments: int = 25000,
        num_failed_payments: int = 5000,
        batch_size: int = 2000,
    ) -> Dict[str, int]:
        """Generate and commit full synthetic dataset matching exact specifications."""
        print(f"[SyntheticDataGenerator] Starting generation with seed {self.seed}...")
        
        # 1. Primary Merchant
        merchant_id = uuid.uuid4()
        merchant = Merchant(
            id=merchant_id,
            name="Apex Retail International",
            slug="apex-retail-intl",
            currency="USD",
            status="ACTIVE",
            created_at=datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc),
        )
        session.add(merchant)
        session.flush()

        # 2. Customers (10,000)
        print(f"[SyntheticDataGenerator] Generating {num_customers} customers...")
        customers: List[Customer] = []
        customer_ids: List[uuid.UUID] = []
        base_time = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        
        for i in range(num_customers):
            c_id = uuid.uuid4()
            customer_ids.append(c_id)
            fn = self.rng.choice(FIRST_NAMES)
            ln = self.rng.choice(LAST_NAMES)
            c_time = base_time + datetime.timedelta(minutes=self.rng.randint(0, 100000))
            cust = Customer(
                id=c_id,
                merchant_id=merchant_id,
                external_id=f"cust_{i+1:06d}",
                name=f"{fn} {ln}",
                email=f"{fn.lower()}.{ln.lower()}{i+1}@example.com",
                risk_score=round(self.rng.betavariate(1.5, 8.0), 4),
                created_at=c_time,
                updated_at=c_time,
            )
            customers.append(cust)
            if len(customers) >= batch_size:
                session.bulk_save_objects(customers)
                session.flush()
                customers = []
        if customers:
            session.bulk_save_objects(customers)
            session.flush()
            customers = []

        # 3. Orders (20,000)
        print(f"[SyntheticDataGenerator] Generating {num_orders} orders...")
        orders: List[Order] = []
        order_ids: List[uuid.UUID] = []
        order_customer_map: Dict[uuid.UUID, uuid.UUID] = {}
        order_amount_map: Dict[uuid.UUID, decimal.Decimal] = {}

        for i in range(num_orders):
            o_id = uuid.uuid4()
            order_ids.append(o_id)
            # Pick customer with slight power-law distribution
            cust_idx = int(self.rng.triangular(0, num_customers - 1, 0))
            cust_id = customer_ids[cust_idx]
            order_customer_map[o_id] = cust_id
            
            # Realistic amount between $12.00 and $750.00
            amt = decimal.Decimal(str(round(self.rng.uniform(12.0, 750.0), 2)))
            order_amount_map[o_id] = amt
            o_time = base_time + datetime.timedelta(minutes=self.rng.randint(1000, 200000))
            
            order = Order(
                id=o_id,
                merchant_id=merchant_id,
                customer_id=cust_id,
                amount=amt,
                currency="USD",
                status=OrderStatus.COMPLETED.value,
                created_at=o_time,
                updated_at=o_time,
            )
            orders.append(order)
            if len(orders) >= batch_size:
                session.bulk_save_objects(orders)
                session.flush()
                orders = []
        if orders:
            session.bulk_save_objects(orders)
            session.flush()
            orders = []

        # 4. Payments (25,000 total, 5,000 failed, 20,000 successful)
        print(f"[SyntheticDataGenerator] Generating {num_payments} payments ({num_failed_payments} failed)...")
        num_successful_payments = num_payments - num_failed_payments
        
        # Determine failure flags: exactly num_failed_payments failed
        # Total payments: 20,000 corresponding to orders 1-to-1, plus 5,000 retry payments
        failed_flags = [True] * num_failed_payments + [False] * num_successful_payments
        self.rng.shuffle(failed_flags)

        payments: List[Payment] = []
        attempts: List[PaymentAttempt] = []
        failures: List[PaymentFailure] = []
        opportunities: List[RecoveryOpportunity] = []
        actions: List[RecoveryAction] = []
        policies: List[PolicyDecision] = []

        # Payment failure category distribution weights
        category_weights = [0.18, 0.25, 0.15, 0.12, 0.10, 0.08, 0.07, 0.05]
        
        failed_count = 0
        success_count = 0

        for i in range(num_payments):
            p_id = uuid.uuid4()
            is_failed = failed_flags[i]
            
            # Map to an order
            if i < num_orders:
                order_id = order_ids[i]
                attempt_num = 1
            else:
                # Retry payment on a previously generated order
                order_id = order_ids[self.rng.randint(0, num_orders - 1)]
                attempt_num = 2
                
            cust_id = order_customer_map[order_id]
            amount = order_amount_map[order_id]
            status = PaymentStatus.FAILED.value if is_failed else PaymentStatus.SUCCESS.value
            p_time = base_time + datetime.timedelta(minutes=self.rng.randint(2000, 250000))
            
            payment = Payment(
                id=p_id,
                merchant_id=merchant_id,
                order_id=order_id,
                customer_id=cust_id,
                amount=amount,
                currency="USD",
                status=status,
                created_at=p_time,
                updated_at=p_time,
            )
            payments.append(payment)

            # Payment attempt with unique idempotency key
            attempt_id = uuid.uuid4()
            gateway = self.rng.choice(GATEWAYS)
            idempotency_key = f"idem_pay_{p_id}_{attempt_num}"
            gateway_tx_id = f"gw_tx_{self.rng.randint(10000000, 99999999)}"
            latency = self.rng.randint(120, 3200) if not is_failed else self.rng.randint(850, 6000)

            attempt = PaymentAttempt(
                id=attempt_id,
                merchant_id=merchant_id,
                payment_id=p_id,
                attempt_number=attempt_num,
                idempotency_key=idempotency_key,
                gateway_name=gateway,
                gateway_transaction_id=gateway_tx_id,
                status=status,
                latency_ms=latency,
                created_at=p_time,
            )
            attempts.append(attempt)

            if is_failed:
                failed_count += 1
                # Select failure category based on weighted distribution
                cat = self.rng.choices(FAILURE_CATEGORIES, weights=category_weights, k=1)[0]
                raw_msg, is_retry, strat, conf = FAILURE_DETAILS[cat]
                
                fail_id = uuid.uuid4()
                fail_record = PaymentFailure(
                    id=fail_id,
                    merchant_id=merchant_id,
                    payment_id=p_id,
                    payment_attempt_id=attempt_id,
                    failure_code=cat.value,
                    raw_message=raw_msg,
                    is_retryable=is_retry,
                    created_at=p_time,
                )
                failures.append(fail_record)

                # Recovery Opportunity
                opp_id = uuid.uuid4()
                opp_status = OpportunityStatus.OPEN.value if is_retry else OpportunityStatus.DISMISSED.value
                opp = RecoveryOpportunity(
                    id=opp_id,
                    merchant_id=merchant_id,
                    payment_id=p_id,
                    failure_id=fail_id,
                    strategy_name=strat,
                    confidence_score=conf,
                    estimated_recoverable_amount=amount,
                    status=opp_status,
                    created_at=p_time,
                    updated_at=p_time,
                )
                opportunities.append(opp)

                # Recovery Action with unique idempotency key and safety gate
                action_id = uuid.uuid4()
                action_idem_key = f"idem_act_{opp_id}_{action_id}"
                action = RecoveryAction(
                    id=action_id,
                    merchant_id=merchant_id,
                    opportunity_id=opp_id,
                    action_type=ActionType.SMART_RETRY.value if is_retry else ActionType.CUSTOMER_OUTREACH.value,
                    idempotency_key=action_idem_key,
                    execution_status=ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value,
                    parameters_json={"retry_delay_hours": self.rng.randint(2, 48), "target_gateway": gateway},
                    created_at=p_time,
                    executed_at=None,
                )
                actions.append(action)

                # Policy Decision
                pol_id = uuid.uuid4()
                policy_dec = PolicyDecision(
                    id=pol_id,
                    merchant_id=merchant_id,
                    opportunity_id=opp_id,
                    action_id=action_id,
                    decision=PolicyDecisionType.APPROVED.value if is_retry else PolicyDecisionType.REJECTED.value,
                    rule_matched="MAX_VELOCITY_CHECK" if is_retry else "NON_RETRYABLE_DECLINE_RULE",
                    reason="Transaction passed merchant safety parameters. Awaiting stage 2 execution clearance." if is_retry else "Terminal failure code, automated retries disallowed.",
                    risk_score=round(self.rng.uniform(0.05, 0.45), 3) if is_retry else round(self.rng.uniform(0.75, 0.98), 3),
                    created_at=p_time,
                )
                policies.append(policy_dec)
            else:
                success_count += 1

            # Flush in batches
            if len(payments) >= batch_size:
                session.bulk_save_objects(payments)
                session.bulk_save_objects(attempts)
                if failures:
                    session.bulk_save_objects(failures)
                    failures = []
                if opportunities:
                    session.bulk_save_objects(opportunities)
                    opportunities = []
                if actions:
                    session.bulk_save_objects(actions)
                    actions = []
                if policies:
                    session.bulk_save_objects(policies)
                    policies = []
                session.flush()
                payments = []
                attempts = []

        if payments:
            session.bulk_save_objects(payments)
            session.bulk_save_objects(attempts)
            if failures:
                session.bulk_save_objects(failures)
            if opportunities:
                session.bulk_save_objects(opportunities)
            if actions:
                session.bulk_save_objects(actions)
            if policies:
                session.bulk_save_objects(policies)
            session.flush()

        # 5. Seed Agent Runs & Tool Calls & Audit Events for Control Plane
        print("[SyntheticDataGenerator] Generating agent runs and audit events...")
        self._generate_agent_runs_and_audits(session, merchant_id, base_time)

        session.commit()
        print(f"[SyntheticDataGenerator] Successfully generated: {num_customers} customers, {num_orders} orders, {num_payments} payments ({failed_count} failed, {success_count} success).")

        return {
            "customers": num_customers,
            "orders": num_orders,
            "payments": num_payments,
            "failed_payments": failed_count,
            "successful_payments": success_count,
        }

    def _generate_agent_runs_and_audits(self, session: Session, merchant_id: uuid.UUID, base_time: datetime.datetime):
        """Generate audit logs and agent execution traces with cryptographic hash chaining."""
        import hashlib
        import json
        agent_names = ["orchestrator", "recovery", "risk", "growth", "finance"]
        
        runs: List[AgentRun] = []
        tools: List[ToolCall] = []
        audits: List[AuditEvent] = []
        prev_hash: Optional[str] = None

        for i in range(25):
            r_id = uuid.uuid4()
            agent_name = agent_names[i % len(agent_names)]
            start_t = base_time + datetime.timedelta(days=i, hours=2)
            end_t = start_t + datetime.timedelta(seconds=self.rng.randint(2, 45))
            
            run = AgentRun(
                id=r_id,
                merchant_id=merchant_id,
                agent_name=agent_name,
                trigger_type="SCHEDULED_RECOVERY_SCAN" if i % 2 == 0 else "FAILED_PAYMENT_WEBHOOK",
                status=AgentRunStatus.COMPLETED.value,
                tokens_used=self.rng.randint(450, 4200),
                cost_usd=decimal.Decimal(str(round(self.rng.uniform(0.001, 0.035), 6))),
                metadata_json={"batch_size": 100, "merchant_id": str(merchant_id)},
                started_at=start_t,
                completed_at=end_t,
            )
            runs.append(run)

            # Tool call for this run
            t_id = uuid.uuid4()
            tool = ToolCall(
                id=t_id,
                merchant_id=merchant_id,
                run_id=r_id,
                tool_name="evaluate_recovery_policy" if agent_name == "risk" else "query_failure_graph",
                input_payload_json={"merchant_id": str(merchant_id), "filter": "FAILED_PAYMENTS_24H"},
                output_payload_json={"evaluated_count": 28, "opportunities_identified": 19, "policy_cleared": 16},
                status="SUCCESS",
                duration_ms=self.rng.randint(45, 620),
                created_at=start_t + datetime.timedelta(seconds=1),
            )
            tools.append(tool)

            # Cryptographically chained audit event
            a_id = uuid.uuid4()
            seq_num = i + 1
            payload_after = {"status": "COMPLETED", "tokens": run.tokens_used}
            actor_id = f"agent:{agent_name}"
            event_hash = AuditLogger._compute_hash(
                prev_hash=prev_hash,
                merchant_id=merchant_id,
                sequence_number=seq_num,
                event_type="AGENT_RUN_EXECUTED",
                actor_id=actor_id,
                payload_after=payload_after,
            )

            audit = AuditEvent(
                id=a_id,
                merchant_id=merchant_id,
                sequence_number=seq_num,
                run_id=r_id,
                entity_type="RECOVERY_PIPELINE",
                entity_id=None,
                event_type="AGENT_RUN_EXECUTED",
                actor_type=ActorType.AI_AGENT.value,
                actor_id=f"agent:{agent_name}",
                payload_before_json=None,
                payload_after_json=payload_after,
                previous_event_hash=prev_hash,
                event_hash=event_hash,
                timestamp=end_t,
            )
            audits.append(audit)
            prev_hash = event_hash

        session.bulk_save_objects(runs)
        session.bulk_save_objects(tools)
        session.bulk_save_objects(audits)
        session.flush()
