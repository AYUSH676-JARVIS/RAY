import os
import uuid
import pytest
from decimal import Decimal

from services.action_layer.stage2_activation import (
    Stage2ActivationManager,
    ExecutionMode,
    InvalidActivationReasonError,
    UnauthorizedActivationError,
)
from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
from services.action_layer.gateway import SimulationGateway, SimulationScenario


@pytest.fixture(autouse=True)
def reset_stage2():
    Stage2ActivationManager.reset_instance()
    yield
    Stage2ActivationManager.reset_instance()


def test_stage2_default_is_stage1_safety():
    """System defaults strictly to Stage 1 safety lock."""
    mgr = Stage2ActivationManager.get_instance()
    state = mgr.get_state()
    assert state.mode == ExecutionMode.STAGE1_SAFETY
    assert state.is_live_authorized is False
    assert mgr.is_live_execution_authorized() is False


def test_stage2_activation_requires_valid_reason_and_token():
    """Stage 2 activation cannot be enabled without non-trivial justification and token."""
    mgr = Stage2ActivationManager.get_instance()

    # Short reason
    with pytest.raises(InvalidActivationReasonError):
        mgr.activate_stage2(
            admin_actor_id="admin_1",
            reason="too short",
            approval_token="valid_length_token_123456",
        )

    # Empty token
    with pytest.raises(UnauthorizedActivationError):
        mgr.activate_stage2(
            admin_actor_id="admin_1",
            reason="This is a valid production justification for live execution rollout.",
            approval_token="",
        )


def test_stage2_fails_closed_when_credentials_missing(monkeypatch):
    """Even if administratively approved, missing live credentials must fail closed."""
    mgr = Stage2ActivationManager.get_instance()
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    mgr.activate_stage2(
        admin_actor_id="admin_1",
        reason="Valid operations deployment authorization for live merchant.",
        approval_token="valid_dual_key_auth_token_998877",
    )

    state = mgr.get_state()
    assert state.mode == ExecutionMode.STAGE2_LIVE
    # But fail-closed verification refuses live authorization
    assert mgr.is_live_execution_authorized() is False


def test_stage2_authorizes_when_credentials_present(monkeypatch):
    """When administratively activated and credentials exist, live execution is authorized."""
    mgr = Stage2ActivationManager.get_instance()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_key123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret_live_456")

    mgr.activate_stage2(
        admin_actor_id="admin_1",
        reason="Valid operations deployment authorization for live merchant.",
        approval_token="valid_dual_key_auth_token_998877",
    )

    assert mgr.is_live_execution_authorized() is True


def test_emergency_kill_switch_locks_to_stage1(monkeypatch):
    """Emergency kill-switch immediately disables live execution and locks to Stage 1."""
    mgr = Stage2ActivationManager.get_instance()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_key123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret_live_456")

    mgr.activate_stage2(
        admin_actor_id="admin_1",
        reason="Valid operations deployment authorization for live merchant.",
        approval_token="valid_dual_key_auth_token_998877",
    )
    assert mgr.is_live_execution_authorized() is True

    # Engage kill-switch
    kill_state = mgr.engage_kill_switch(
        actor_id="ops_engineer",
        reason="Detected anomalous downstream network latency on bank acquiring rails.",
    )

    assert kill_state.mode == ExecutionMode.STAGE1_SAFETY
    assert kill_state.kill_switch_engaged is True
    assert mgr.is_live_execution_authorized() is False


def test_action_executor_respects_stage1_safety_lock():
    """ActionExecutor blocks live financial action execution when stage 1 lock is active."""
    executor = ActionExecutor(stage_1_safety_lock=True)
    with pytest.raises(FinancialExecutionBlockedError) as exc_info:
        executor.execute_recovery_action(
            action_id=uuid.uuid4(),
            action_type="RETRY_PAYMENT",
            idempotency_key="idem_key_safe_12345",
            amount=Decimal("100.00"),
            policy_decision="APPROVED",
        )
    assert "BLOCKED_STAGE1_SAFETY" in str(exc_info.value)


def test_stage2_limit_breach_triggers_kill_switch(monkeypatch):
    """Exceeding single transaction cap automatically engages kill-switch and reverts to Stage 1."""
    from services.action_layer.stage2_activation import Stage2ActivationError

    mgr = Stage2ActivationManager.get_instance()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_key123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret_live_456")

    mgr.activate_stage2(
        admin_actor_id="admin_1",
        reason="Valid operations deployment authorization for live merchant.",
        approval_token="valid_dual_key_auth_token_998877",
    )
    assert mgr.is_live_execution_authorized() is True

    # Attempt transaction exceeding single transaction cap ($5000)
    with pytest.raises(Stage2ActivationError, match="limit breached"):
        mgr.validate_and_track_transaction(Decimal("6000.00"))

    # System automatically reverts to Stage 1 Safety
    state = mgr.get_state()
    assert state.mode == ExecutionMode.STAGE1_SAFETY
    assert state.kill_switch_engaged is True
    assert mgr.is_live_execution_authorized() is False

