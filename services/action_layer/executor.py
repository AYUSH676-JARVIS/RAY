"""Deterministic Action Layer — Safe Execution Interface.

Core Invariants:
1. Deterministic action layer executes (strictly isolated from raw LLMs).
2. True transactional idempotency: concurrent duplicate requests are serialized,
   and sequential duplicate requests return the cached receipt.
3. Ambiguous outcomes (gateway network timeout) enter UNKNOWN state and block blind retries.
4. Policy boundary enforcement: Re-evaluates policy clearance immediately prior to execution.
5. Stage 1 Safety Guard (BLOCKED_STAGE1_SAFETY): No autonomous money movement
   is executed at Stage 1.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from services.action_layer.gateway import GatewayResult, GatewayStatus, PaymentGateway, SimulationGateway
from services.action_layer.idempotency import IdempotencyManager
from services.concurrency.distributed_lock import distributed_lock
from services.money_graph.models import ActionExecutionStatus, PaymentStatus, PolicyDecisionType
from services.money_graph.state_machine import validate_action_transition, validate_payment_transition


class FinancialExecutionBlockedError(Exception):
    """Raised when any financial execution is attempted while Stage 1 safety lock is active."""
    pass


class InvalidIdempotencyKeyError(ValueError):
    """Raised when an idempotency key is missing, empty, or malformed."""
    pass


class PolicyAuthorizationBlockedError(Exception):
    """Raised when an action is rejected by policy engine or policy becomes blocked before execution."""
    pass


class AmbiguousOutcomeBlockedError(Exception):
    """Raised when an action is attempted on an UNKNOWN payment without authoritative reconciliation."""
    pass


class PaymentAlreadySettledError(Exception):
    """Raised when an action is attempted on an already successful or settled payment."""
    pass


class ActionExecutor:
    """Safeguarded action layer interface with transactional idempotency, policy gates, and gateway abstraction."""

    def __init__(
        self,
        stage_1_safety_lock: bool = True,
        gateway: Optional[PaymentGateway] = None,
        idempotency_manager: Optional[IdempotencyManager] = None,
    ):
        self.stage_1_safety_lock = stage_1_safety_lock
        self.gateway = gateway or SimulationGateway()
        self.idempotency_manager = idempotency_manager or IdempotencyManager()

    def validate_idempotency_key(self, idempotency_key: Optional[str]) -> str:
        """Enforce non-empty idempotency key format."""
        if not idempotency_key or not isinstance(idempotency_key, str) or len(idempotency_key.strip()) < 8:
            raise InvalidIdempotencyKeyError(
                "Financial actions require a valid idempotency key of at least 8 characters."
            )
        return idempotency_key.strip()

    def execute_recovery_action(
        self,
        action_id: uuid.UUID,
        action_type: str,
        idempotency_key: str,
        merchant_id: Optional[uuid.UUID] = None,
        amount: Optional[Decimal] = None,
        currency: str = "USD",
        customer_id: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        session: Optional[Any] = None,
        policy_decision: Optional[str] = None,
        payment_status: Optional[str] = None,
        policy_check_fn: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Attempt to execute a financial action with true idempotency and safety gates."""
        clean_key = self.validate_idempotency_key(idempotency_key)
        effective_merchant_id = merchant_id or uuid.UUID("00000000-0000-0000-0000-000000000000")

        # 1. Check idempotency slot / cached response (memory or durable database ledger)
        is_new, cached_receipt = self.idempotency_manager.acquire_execution_slot(
            merchant_id=effective_merchant_id,
            idempotency_key=clean_key,
            session=session,
        )
        if not is_new and cached_receipt:
            return {
                **cached_receipt.response_payload,
                "idempotent_replay": True,
            }

        try:
            # 2. Payment State Invariants (Reject UNKNOWN and already SETTLED payments)
            if payment_status:
                norm_payment_status = payment_status.upper().strip()
                if norm_payment_status == PaymentStatus.UNKNOWN.value:
                    raise AmbiguousOutcomeBlockedError(
                        f"Financial action {action_id} blocked: Payment is in UNKNOWN state. "
                        "Authoritative reconciliation required before executing further actions."
                    )
                if norm_payment_status in [PaymentStatus.SUCCESS.value, PaymentStatus.CAPTURED.value, "SETTLED"]:
                    raise PaymentAlreadySettledError(
                        f"Financial action {action_id} blocked: Payment is already in {norm_payment_status} state. "
                        "Further recovery actions are prohibited."
                    )

            # 3. Policy Re-check & Authorization Boundary Invariant
            if policy_decision and policy_decision.upper() == PolicyDecisionType.REJECTED.value:
                raise PolicyAuthorizationBlockedError(
                    f"Financial action {action_id} blocked: Policy decision is REJECTED. "
                    "External financial execution is strictly prohibited."
                )

            if policy_check_fn is not None and not policy_check_fn():
                raise PolicyAuthorizationBlockedError(
                    f"Financial action {action_id} blocked: Stale policy condition detected. "
                    "Dynamic policy re-check refused execution authorization."
                )

            # 4. Stage 1 Safety Guard Invariant
            if self.stage_1_safety_lock:
                validate_action_transition(
                    ActionExecutionStatus.REQUESTED,
                    ActionExecutionStatus.BLOCKED_STAGE1_SAFETY,
                )
                raise FinancialExecutionBlockedError(
                    f"Financial action {action_id} of type '{action_type}' execution halted: "
                    f"Stage 1 Safety Guard active ({ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value}). "
                    f"Direct execution is disabled in this phase."
                )

            # 5. Distributed Emergency Kill-Switch Check
            from services.action_layer.stage2_activation import Stage2ActivationManager
            stage2_mgr = Stage2ActivationManager.get_instance()
            if stage2_mgr.get_state().kill_switch_engaged:
                raise FinancialExecutionBlockedError(
                    f"Financial action {action_id} execution halted: "
                    "Distributed emergency kill switch is actively engaged."
                )

            # 6. Live Execution Authorization & Volume Cap Boundary (for Live Gateways)
            is_live_gateway = getattr(self.gateway, "live_execution_enabled", False)
            if is_live_gateway:
                if not stage2_mgr.is_live_execution_authorized(merchant_id=effective_merchant_id, session=session):
                    raise FinancialExecutionBlockedError(
                        f"Financial action {action_id} execution halted: "
                        "Live financial execution is unauthorized. Stage 2 administrative approval required."
                    )
                stage2_mgr.validate_and_track_transaction(
                    amount=amount or Decimal("0.00"),
                    merchant_id=effective_merchant_id,
                    session=session,
                )

            # 7. Execution via Gateway Abstraction (Stage 2+)
            gw_result: GatewayResult = self.gateway.execute_retry(
                amount=amount or Decimal("0.00"),
                currency=currency,
                idempotency_key=clean_key,
                customer_id=customer_id or "cust_unknown",
                metadata=parameters,
            )

            # 6. Handle Outcome States
            if gw_result.status == GatewayStatus.UNKNOWN:
                response_payload = {
                    "status": ActionExecutionStatus.UNKNOWN.value,
                    "idempotency_key": clean_key,
                    "action_id": str(action_id),
                    "gateway_transaction_id": None,
                    "gateway_status": GatewayStatus.UNKNOWN,
                    "detail": "Gateway response timed out. Transaction outcome is ambiguous and awaiting status inquiry.",
                }
            elif gw_result.status == GatewayStatus.SUCCEEDED:
                response_payload = {
                    "status": ActionExecutionStatus.SUCCEEDED.value,
                    "idempotency_key": clean_key,
                    "action_id": str(action_id),
                    "gateway_transaction_id": gw_result.transaction_id,
                    "gateway_status": GatewayStatus.SUCCEEDED,
                }
            else:
                response_payload = {
                    "status": ActionExecutionStatus.FAILED.value,
                    "idempotency_key": clean_key,
                    "action_id": str(action_id),
                    "gateway_transaction_id": gw_result.transaction_id,
                    "gateway_status": GatewayStatus.FAILED,
                    "failure_code": gw_result.raw_code,
                }

            # 7. Commit receipt to idempotency cache & release lock
            self.idempotency_manager.commit_receipt(
                merchant_id=effective_merchant_id,
                idempotency_key=clean_key,
                response_payload=response_payload,
            )

            # 8. If database session provided, persist state updates & audit trail
            if session is not None:
                try:
                    from services.money_graph.models import RecoveryAction
                    from sqlalchemy import select
                    act = session.scalar(select(RecoveryAction).where(RecoveryAction.id == action_id))
                    if act:
                        act.execution_status = response_payload["status"]
                        act.executed_at = datetime.now(timezone.utc)
                    session.commit()
                except Exception:
                    session.rollback()

            return response_payload

        except (FinancialExecutionBlockedError, PolicyAuthorizationBlockedError, AmbiguousOutcomeBlockedError, PaymentAlreadySettledError):
            self.idempotency_manager.release_failed_slot(effective_merchant_id, clean_key)
            raise
        except Exception:
            self.idempotency_manager.release_failed_slot(effective_merchant_id, clean_key)
            raise
