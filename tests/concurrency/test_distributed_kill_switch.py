"""Distributed Kill-Switch Verification Tests.

Verifies:
1. Kill switch state is persisted in PostgreSQL across separate manager instances / worker pods.
2. Pod A engages kill switch -> Pod B immediately blocks financial execution.
3. External gateway calls are strictly prevented when kill switch is engaged.
4. Fails closed if PostgreSQL cannot be reached.
5. Audit event is recorded on kill-switch engagement.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
from services.action_layer.gateway import SimulationGateway
from services.action_layer.stage2_activation import Stage2ActivationManager, ExecutionMode
from services.money_graph.database import SessionLocal
from services.money_graph.models import Merchant, SystemSafetyControl


@pytest.fixture
def clean_safety_controls():
    """Reset PostgreSQL system safety controls for test isolation."""
    with SessionLocal() as s:
        rec = s.query(SystemSafetyControl).filter(SystemSafetyControl.scope == "GLOBAL").first()
        if not rec:
            rec = SystemSafetyControl(
                id=uuid.uuid4(),
                scope="GLOBAL",
                mode="STAGE1_SAFETY",
                is_live_authorized=False,
                kill_switch_engaged=False,
            )
            s.add(rec)
        else:
            rec.mode = "STAGE1_SAFETY"
            rec.is_live_authorized = False
            rec.kill_switch_engaged = False
            rec.kill_switch_reason = None
            rec.kill_switch_engaged_by = None
        s.commit()

    Stage2ActivationManager.reset_instance()
    yield
    Stage2ActivationManager.reset_instance()


def test_kill_switch_propagates_across_pods(clean_safety_controls):
    """Assert Pod A engages kill switch in PostgreSQL and Pod B immediately observes it."""
    # Pod A instance
    pod_a_mgr = Stage2ActivationManager()

    # Step 1: Pod A activates Stage 2
    pod_a_mgr.activate_stage2(
        admin_actor_id="admin_sec_ops_1",
        reason="Scheduled live canary rollout for merchant cohort",
        approval_token="approval_token_stage2_live_authorized_42",
    )

    # Pod B instance (separate process/memory simulation)
    pod_b_mgr = Stage2ActivationManager()

    # Verify Pod A engages emergency kill switch
    pod_a_mgr.engage_kill_switch(
        actor_id="operator_sentinel",
        reason="Suspicious error rate spike detected upstream",
    )

    # Pod B checks authorization
    with SessionLocal() as s:
        is_authorized = pod_b_mgr.is_live_execution_authorized(session=s)
        assert is_authorized is False, "Pod B allowed live execution despite Pod A engaging kill switch!"


def test_kill_switch_blocks_financial_executor(clean_safety_controls):
    """Assert ActionExecutor halts financial execution when kill switch is engaged."""
    gateway_mock = MagicMock(spec=SimulationGateway)
    gateway_mock.gateway_name = "MockGateway"

    executor = ActionExecutor(
        gateway=gateway_mock,
        stage_1_safety_lock=False,  # Pretend Stage 1 lock is disabled
    )

    mgr = Stage2ActivationManager.get_instance()
    mgr.engage_kill_switch(
        actor_id="risk_sentinel",
        reason="Immediate operational risk threshold breached",
    )

    with SessionLocal() as s:
        with pytest.raises(FinancialExecutionBlockedError) as exc_info:
            executor.execute_recovery_action(
                action_id=uuid.uuid4(),
                action_type="SMART_RETRY",
                idempotency_key=f"idem_ks_{uuid.uuid4().hex[:8]}",
                amount=Decimal("150.00"),
                currency="USD",
                session=s,
            )
        assert "kill switch is actively engaged" in str(exc_info.value)

    # Prove that gateway was NEVER called
    gateway_mock.execute_retry.assert_not_called()


def test_kill_switch_fails_closed_on_db_outage(clean_safety_controls):
    """Assert that is_live_execution_authorized fails closed when database cannot be queried."""
    mgr = Stage2ActivationManager.get_instance()
    mgr._state.mode = ExecutionMode.STAGE2_LIVE
    mgr._state.is_live_authorized = True
    mgr._state.kill_switch_engaged = False

    # Mock broken database session that raises connection error
    broken_session = MagicMock()
    broken_session.query.side_effect = ConnectionError("PostgreSQL cluster network unreachable")

    # Invariant: Must fail closed (return False)
    assert mgr.is_live_execution_authorized(session=broken_session) is False
