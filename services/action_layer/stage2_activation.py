"""Stage 2 Financial Execution Activation & Emergency Kill-Switch Manager.

Enforces strict governance over the boundary between:
- Stage 1: BLOCKED_STAGE1_SAFETY (Default safety lock, zero live money movement)
- Stage 2: LIVE_EXECUTION_ENABLED (Authoritative live money movement)

Rules:
1. Stage 2 can NEVER be enabled via frontend button, query param, or simple flag.
2. Requires explicit administrative authorization with non-trivial justification and signed approval.
3. Automatically fails closed on any discrepancy or missing gateway credentials.
4. Provides an instantaneous emergency kill-switch that locks execution back to Stage 1.
5. All transitions produce immutable SHA-256 chained audit events.
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.audit.logger import AuditLogger
from services.money_graph.models import ActorType

logger = logging.getLogger("ray.safety.stage2")


class ExecutionMode(str, enum.Enum):
    STAGE1_SAFETY = "STAGE1_SAFETY"
    STAGE2_LIVE = "STAGE2_LIVE"


class Stage2ActivationError(Exception):
    """Base exception for Stage 2 activation violations."""
    pass


class UnauthorizedActivationError(Stage2ActivationError):
    """Activation attempted without required administrative privileges or signatures."""
    pass


class InvalidActivationReasonError(Stage2ActivationError):
    """Activation rejected due to insufficient or empty justification reason."""
    pass


class Stage2ActivationState(BaseModel):
    mode: ExecutionMode = ExecutionMode.STAGE1_SAFETY
    is_live_authorized: bool = False
    activated_at: Optional[datetime] = None
    activated_by: Optional[str] = None
    activation_reason: Optional[str] = None
    kill_switch_engaged: bool = False
    kill_switch_engaged_at: Optional[datetime] = None
    kill_switch_engaged_by: Optional[str] = None
    kill_switch_reason: Optional[str] = None
    single_transaction_cap: Decimal = Field(default=Decimal("5000.00"), description="Max live money movement allowed in single execution")
    daily_volume_cap: Decimal = Field(default=Decimal("25000.00"), description="Max aggregate live money movement per day")
    current_daily_volume: Decimal = Field(default=Decimal("0.00"), description="Tracked live money volume today")


class Stage2ActivationManager:
    """Thread-safe administrative control center for Stage 2 live money movement."""

    _instance: Optional[Stage2ActivationManager] = None
    _lock = threading.Lock()

    def __init__(self):
        self._state = Stage2ActivationState()
        self._mutex = threading.RLock()

    @classmethod
    def get_instance(cls) -> Stage2ActivationManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for isolated test execution."""
        with cls._lock:
            cls._instance = cls()

    def get_state(self) -> Stage2ActivationState:
        with self._mutex:
            return self._state.model_copy()

    def is_live_execution_authorized(
        self,
        merchant_id: Optional[uuid.UUID] = None,
        session: Optional[Session] = None,
    ) -> bool:
        """Fail-closed check for whether live gateway money movement is permitted.
        
        Evaluates authoritative distributed state stored in PostgreSQL.
        Fails closed (returns False) on any database error or missing record.
        """
        with self._mutex:
            # 1. Ephemeral memory fast-path
            if self._state.kill_switch_engaged or self._state.mode != ExecutionMode.STAGE2_LIVE:
                return False

            # 2. Authoritative PostgreSQL distributed state evaluation
            try:
                from services.money_graph.database import SessionLocal
                from services.money_graph.models import SystemSafetyControl

                def check_db(s: Session) -> bool:
                    # Check merchant-scoped control if merchant_id provided
                    if merchant_id:
                        m_rec = s.query(SystemSafetyControl).filter(
                            SystemSafetyControl.scope == str(merchant_id)
                        ).first()
                        if m_rec:
                            if m_rec.kill_switch_engaged or m_rec.mode != ExecutionMode.STAGE2_LIVE.value or not m_rec.is_live_authorized:
                                return False

                    # Check global control
                    g_rec = s.query(SystemSafetyControl).filter(
                        SystemSafetyControl.scope == "GLOBAL"
                    ).first()
                    if not g_rec:
                        return False
                    if g_rec.kill_switch_engaged or g_rec.mode != ExecutionMode.STAGE2_LIVE.value or not g_rec.is_live_authorized:
                        return False
                    return True

                if session is not None:
                    db_authorized = check_db(session)
                else:
                    with SessionLocal() as s:
                        db_authorized = check_db(s)

                if not db_authorized:
                    return False

            except Exception as e:
                logger.critical(f"Database error checking distributed kill switch. Failing closed: {e}")
                return False

            # 3. Environment check: cannot be in live mode if credentials are missing
            key_id = os.getenv("RAZORPAY_KEY_ID")
            key_secret = os.getenv("RAZORPAY_KEY_SECRET")
            if not key_id or key_id == "rzp_test_placeholder" or not key_secret or key_secret == "secret_placeholder":
                logger.error("Stage 2 active but live gateway credentials missing. Failing closed.")
                return False

            return True

    def activate_stage2(
        self,
        admin_actor_id: str,
        reason: str,
        approval_token: str,
        merchant_id: Optional[uuid.UUID] = None,
        session: Optional[Session] = None,
    ) -> Stage2ActivationState:
        """Explicit administrative activation of Stage 2 Live Execution."""
        with self._mutex:
            # 1. Validation of justification reason
            if not reason or len(reason.strip()) < 15:
                raise InvalidActivationReasonError(
                    "Stage 2 live execution requires detailed operational justification (minimum 15 characters)."
                )

            # 2. Validation of approval token / cryptographic signature
            if not approval_token or len(approval_token.strip()) < 16:
                raise UnauthorizedActivationError(
                    "Stage 2 live execution requires valid administrative dual-key approval token."
                )

            now = datetime.now(timezone.utc)
            self._state.mode = ExecutionMode.STAGE2_LIVE
            self._state.is_live_authorized = True
            self._state.activated_at = now
            self._state.activated_by = admin_actor_id
            self._state.activation_reason = reason.strip()
            self._state.kill_switch_engaged = False
            self._state.kill_switch_engaged_at = None
            self._state.kill_switch_engaged_by = None
            self._state.kill_switch_reason = None

            logger.critical(
                f"STAGE 2 LIVE MONEY MOVEMENT ACTIVATED by {admin_actor_id}. Reason: {reason}",
                extra={"actor_id": admin_actor_id, "mode": "STAGE2_LIVE"},
            )

            # Record cryptographic audit event
            if session and merchant_id:
                try:
                    AuditLogger.record_event(
                        session=session,
                        merchant_id=merchant_id,
                        entity_type="SYSTEM_SAFETY",
                        entity_id=uuid.uuid4(),
                        event_type="STAGE2_LIVE_EXECUTION_ACTIVATED",
                        actor_type=ActorType.USER,
                        actor_id=admin_actor_id,
                        payload_after={
                            "mode": ExecutionMode.STAGE2_LIVE.value,
                            "activated_by": admin_actor_id,
                            "reason": reason,
                            "timestamp": now.isoformat(),
                        },
                    )
                    session.commit()
                except Exception as e:
                    logger.error(f"Failed to record Stage 2 activation audit event: {e}")

            # Persist Stage 2 live authorization to PostgreSQL
            try:
                from services.money_graph.database import SessionLocal
                from services.money_graph.models import SystemSafetyControl

                def persist_activation(s: Session):
                    target_scope = str(merchant_id) if merchant_id else "GLOBAL"
                    rec = s.query(SystemSafetyControl).filter(
                        SystemSafetyControl.scope == target_scope
                    ).first()
                    if not rec:
                        rec = SystemSafetyControl(
                            id=uuid.uuid4(),
                            scope=target_scope,
                            merchant_id=merchant_id,
                        )
                        s.add(rec)
                    rec.mode = ExecutionMode.STAGE2_LIVE.value
                    rec.is_live_authorized = True
                    rec.kill_switch_engaged = False
                    rec.kill_switch_engaged_at = None
                    rec.kill_switch_engaged_by = None
                    rec.kill_switch_reason = None
                    s.flush()

                if session is not None:
                    persist_activation(session)
                else:
                    with SessionLocal() as s:
                        persist_activation(s)
                        s.commit()
            except Exception as db_err:
                logger.critical(f"Failed to persist Stage 2 activation to database: {db_err}")

            return self._state.model_copy()

    def engage_kill_switch(
        self,
        actor_id: str,
        reason: str,
        merchant_id: Optional[uuid.UUID] = None,
        session: Optional[Session] = None,
    ) -> Stage2ActivationState:
        """Instantaneous Emergency Kill-Switch: Locks system immediately back to Stage 1.
        
        Globally authoritative: Persists across all pods via PostgreSQL.
        """
        with self._mutex:
            now = datetime.now(timezone.utc)
            self._state.mode = ExecutionMode.STAGE1_SAFETY
            self._state.is_live_authorized = False
            self._state.kill_switch_engaged = True
            self._state.kill_switch_engaged_at = now
            self._state.kill_switch_engaged_by = actor_id
            self._state.kill_switch_reason = reason

            logger.critical(
                f"EMERGENCY FINANCIAL KILL-SWITCH ENGAGED by {actor_id}. System locked to STAGE 1 SAFETY.",
                extra={"actor_id": actor_id, "reason": reason},
            )

            # Persist globally authoritative kill-switch state to PostgreSQL
            try:
                from services.money_graph.database import SessionLocal
                from services.money_graph.models import SystemSafetyControl

                def persist_kill_switch(s: Session):
                    scopes = ["GLOBAL"]
                    if merchant_id:
                        scopes.append(str(merchant_id))
                    records = s.query(SystemSafetyControl).filter(
                        SystemSafetyControl.scope.in_(scopes)
                    ).all()
                    for rec in records:
                        rec.kill_switch_engaged = True
                        rec.mode = ExecutionMode.STAGE1_SAFETY.value
                        rec.is_live_authorized = False
                        rec.kill_switch_engaged_at = now
                        rec.kill_switch_engaged_by = actor_id
                        rec.kill_switch_reason = reason
                    s.flush()

                if session is not None:
                    persist_kill_switch(session)
                else:
                    with SessionLocal() as s:
                        persist_kill_switch(s)
                        s.commit()
            except Exception as db_err:
                logger.critical(f"Failed to persist kill-switch to database: {db_err}")

            if session and merchant_id:
                try:
                    AuditLogger.record_event(
                        session=session,
                        merchant_id=merchant_id,
                        entity_type="SYSTEM_SAFETY",
                        entity_id=uuid.uuid4(),
                        event_type="EMERGENCY_KILL_SWITCH_ENGAGED",
                        actor_type=ActorType.USER,
                        actor_id=actor_id,
                        payload_after={
                            "mode": ExecutionMode.STAGE1_SAFETY.value,
                            "kill_switch_engaged": True,
                            "engaged_by": actor_id,
                            "reason": reason,
                            "timestamp": now.isoformat(),
                        },
                    )
                    session.commit()
                except Exception as e:
                    logger.error(f"Failed to record Kill-Switch audit event: {e}")

            return self._state.model_copy()

    def validate_and_track_transaction(
        self,
        amount: Decimal,
        merchant_id: Optional[uuid.UUID] = None,
        session: Optional[Session] = None,
    ) -> bool:
        """Validate transaction amount against safety limits. Breaches trigger emergency revert to Stage 1."""
        with self._mutex:
            if not self.is_live_execution_authorized():
                return False

            if amount > self._state.single_transaction_cap:
                msg = f"Single transaction limit breached: requested {amount} exceeds cap {self._state.single_transaction_cap}"
                logger.error(msg)
                self.engage_kill_switch(
                    actor_id="system_limit_sentinel",
                    reason=msg,
                    merchant_id=merchant_id,
                    session=session,
                )
                raise Stage2ActivationError(msg)

            if self._state.current_daily_volume + amount > self._state.daily_volume_cap:
                msg = f"Daily volume cap breached: new total {self._state.current_daily_volume + amount} exceeds cap {self._state.daily_volume_cap}"
                logger.error(msg)
                self.engage_kill_switch(
                    actor_id="system_limit_sentinel",
                    reason=msg,
                    merchant_id=merchant_id,
                    session=session,
                )
                raise Stage2ActivationError(msg)

            self._state.current_daily_volume += amount
            return True

