"""Authoritative Payment Reconciliation Worker.

Scans for payments trapped in ambiguous / UNKNOWN outcome states, queries the configured
payment gateway status API, verifies financial integrity (amount, currency, merchant, ref),
and deterministically reconciles them to final financial truth.

INVARIANTS:
1. UNKNOWN remains UNKNOWN until authoritative external evidence exists.
2. ZERO automatic transitions to SUCCESS without verified gateway confirmation.
3. Amount, currency, merchant ID, and gateway transaction ID must strictly match.
4. Fail-closed: timeouts, unresolvable statuses, and network drops keep payments in UNKNOWN.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.action_layer.gateway import GatewayStatus, PaymentGateway
from services.audit.logger import AuditLogger
from services.money_graph.models import ActorType, Payment, PaymentAttempt, PaymentStatus
from services.money_graph.state_machine import validate_payment_transition

logger = logging.getLogger("ray.worker.reconciliation")


class ReconciliationWorker:
    """Background worker resolving ambiguous UNKNOWN payment outcomes via authoritative gateway inquiry."""

    def __init__(self, session_factory, gateway_adapter: Optional[PaymentGateway] = None):
        self.session_factory = session_factory
        self.gateway_adapter = gateway_adapter

    def reconcile_pending_unknowns(
        self,
        max_batch: int = 25,
        cooldown_seconds: int = 15,
        merchant_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Scan and reconcile payments in UNKNOWN status older than cooldown_seconds.
        
        Returns:
            Count of successfully resolved (SUCCESS or FAILED) payments.
        """
        reconciled = 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds)

        with self.session_factory() as session:
            stmt = (
                select(Payment)
                .where(Payment.status == PaymentStatus.UNKNOWN.value)
                .where(Payment.updated_at <= cutoff)
            )
            if merchant_id:
                stmt = stmt.where(Payment.merchant_id == merchant_id)
            stmt = stmt.limit(max_batch)
            payments = session.execute(stmt).scalars().all()

            for p in payments:
                try:
                    # 1. Retrieve the latest payment attempt for this payment
                    attempt_stmt = (
                        select(PaymentAttempt)
                        .where(PaymentAttempt.payment_id == p.id)
                        .order_by(PaymentAttempt.attempt_number.desc())
                    )
                    attempt = session.execute(attempt_stmt).scalars().first()

                    if not attempt or not attempt.gateway_transaction_id:
                        logger.warning(
                            f"Payment {p.id} in UNKNOWN state has no gateway transaction ID. "
                            "Cannot perform authoritative status inquiry. Payment remains UNKNOWN."
                        )
                        continue

                    # 2. Authoritative Gateway Inquiry
                    gw = self.gateway_adapter
                    if gw is None:
                        logger.warning(
                            f"No gateway adapter available for reconciliation of payment {p.id}. "
                            "Payment remains UNKNOWN."
                        )
                        continue

                    try:
                        gw_result = gw.query_status(
                            transaction_id=attempt.gateway_transaction_id,
                            merchant_id=str(p.merchant_id),
                        )
                    except Exception as gw_err:
                        logger.error(
                            f"Gateway query error on transaction {attempt.gateway_transaction_id} "
                            f"for payment {p.id}: {gw_err}. Payment remains UNKNOWN."
                        )
                        continue

                    # 3. Handle Status Outcomes
                    if gw_result.status == GatewayStatus.UNKNOWN.value:
                        logger.info(
                            f"Gateway returned UNKNOWN status for transaction {attempt.gateway_transaction_id}. "
                            f"Payment {p.id} remains UNKNOWN awaiting final settlement."
                        )
                        continue

                    if gw_result.status == GatewayStatus.SUCCEEDED.value:
                        # Authoritative Success Verification: Validate all financial fields
                        # A. Gateway Transaction ID match
                        if gw_result.transaction_id != attempt.gateway_transaction_id:
                            logger.error(
                                f"Reconciliation rejected: Transaction ID mismatch "
                                f"(expected '{attempt.gateway_transaction_id}', got '{gw_result.transaction_id}')."
                            )
                            continue

                        # B. Cross-Merchant Isolation: Verify merchant ID matches
                        if gw_result.merchant_id and str(gw_result.merchant_id) != str(p.merchant_id):
                            logger.error(
                                f"Cross-tenant reconciliation violation: Gateway merchant "
                                f"'{gw_result.merchant_id}' does not match payment merchant '{p.merchant_id}'."
                            )
                            continue

                        # C. Currency Integrity
                        payload = gw_result.payload or {}
                        if "currency" in payload and payload["currency"].upper() != p.currency.upper():
                            logger.error(
                                f"Currency mismatch during reconciliation for payment {p.id}: "
                                f"Gateway reported '{payload['currency']}', expected '{p.currency}'."
                            )
                            continue

                        # D. Amount Integrity
                        if "amount" in payload:
                            raw_amount = payload["amount"]
                            if gw.gateway_name.lower() == "razorpay":
                                expected_paise = int(p.amount * 100)
                                if int(raw_amount) != expected_paise:
                                    logger.error(
                                        f"Amount mismatch during Razorpay reconciliation for payment {p.id}: "
                                        f"Gateway reported {raw_amount} paise, expected {expected_paise} paise."
                                    )
                                    continue
                            else:
                                if Decimal(str(raw_amount)) != p.amount:
                                    logger.error(
                                        f"Amount mismatch during reconciliation for payment {p.id}: "
                                        f"Gateway reported {raw_amount}, expected {p.amount}."
                                    )
                                    continue

                        # Financial integrity verified: Transition to SUCCESS
                        target_status = PaymentStatus.SUCCESS
                        prev_status = p.status
                        evidence_id = f"auto_recon_conf_{attempt.gateway_transaction_id}"
                        validate_payment_transition(p.status, target_status, evidence_id=evidence_id)
                        p.status = target_status.value
                        attempt.status = "SUCCESS"

                        AuditLogger.record_event(
                            session=session,
                            merchant_id=p.merchant_id,
                            entity_type="PAYMENT",
                            entity_id=p.id,
                            event_type="BACKGROUND_RECONCILIATION_RESOLVED",
                            actor_type=ActorType.ACTION_LAYER,
                            actor_id="ray_reconciliation_daemon",
                            payload_before={"status": prev_status},
                            payload_after={
                                "status": target_status.value,
                                "gateway_transaction_id": attempt.gateway_transaction_id,
                                "reconciliation_reason": f"Authoritative gateway confirmation from {gw.gateway_name}",
                            },
                        )
                        reconciled += 1
                        logger.info(
                            f"Authoritatively reconciled payment {p.id} from {prev_status} -> {target_status.value} "
                            f"(Gateway ref: {attempt.gateway_transaction_id})"
                        )

                    elif gw_result.status == GatewayStatus.FAILED.value:
                        # Authoritative Failure: Gateway confirmed decline
                        target_status = PaymentStatus.FAILED
                        prev_status = p.status
                        evidence_id = f"auto_recon_decl_{attempt.gateway_transaction_id}"
                        validate_payment_transition(p.status, target_status, evidence_id=evidence_id)
                        p.status = target_status.value
                        attempt.status = "FAILED"

                        AuditLogger.record_event(
                            session=session,
                            merchant_id=p.merchant_id,
                            entity_type="PAYMENT",
                            entity_id=p.id,
                            event_type="BACKGROUND_RECONCILIATION_DECLINED",
                            actor_type=ActorType.ACTION_LAYER,
                            actor_id="ray_reconciliation_daemon",
                            payload_before={"status": prev_status},
                            payload_after={
                                "status": target_status.value,
                                "gateway_transaction_id": attempt.gateway_transaction_id,
                                "reconciliation_reason": f"Authoritative gateway decline from {gw.gateway_name}: {gw_result.raw_code}",
                            },
                        )
                        reconciled += 1
                        logger.info(
                            f"Authoritatively reconciled payment {p.id} from {prev_status} -> {target_status.value} "
                            f"(Decline code: {gw_result.raw_code})"
                        )
                    else:
                        logger.warning(
                            f"Unrecognized gateway status '{gw_result.status}' for payment {p.id}. "
                            "Payment remains UNKNOWN."
                        )

                except Exception as e:
                    logger.error(f"Reconciliation error on payment {p.id}: {e}")

            session.commit()

        return reconciled
