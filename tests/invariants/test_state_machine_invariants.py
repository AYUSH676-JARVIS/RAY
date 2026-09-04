"""Exhaustive Invariant Tests for Financial State Machines & Consistency (Phase 4).

Specifically tests:
- SUCCESS → PENDING (Illegal)
- CAPTURED → PENDING (Illegal)
- FAILED → SUCCESS (Illegal without new authorization)
- UNKNOWN → SUCCESS without authoritative evidence (Illegal)
- UNKNOWN → CAPTURED without authoritative evidence (Illegal)
- UNKNOWN → SUCCESS with authoritative evidence (Legal)
- UNKNOWN → CAPTURED with authoritative evidence (Legal)
- BLOCKED → EXECUTED (Illegal)
- EXECUTED → BLOCKED (Illegal)
- SETTLED → RETRY (Illegal)
- Duplicate webhook transitions
- Out-of-order webhook transitions
"""

import pytest

from services.money_graph.models import (
    PaymentStatus,
    ActionExecutionStatus,
    SettlementStatus,
)
from services.money_graph.state_machine import (
    InvalidStateTransitionError,
    MissingOutcomeEvidenceError,
    WebhookStatus,
    validate_payment_transition,
    validate_action_transition,
    validate_settlement_transition,
    validate_webhook_transition,
)


def test_illegal_success_to_pending():
    with pytest.raises(InvalidStateTransitionError):
        validate_payment_transition(PaymentStatus.SUCCESS, PaymentStatus.PENDING)


def test_illegal_captured_to_pending():
    with pytest.raises(InvalidStateTransitionError):
        validate_payment_transition(PaymentStatus.CAPTURED, PaymentStatus.PENDING)


def test_illegal_failed_to_success():
    with pytest.raises(InvalidStateTransitionError):
        validate_payment_transition(PaymentStatus.FAILED, PaymentStatus.SUCCESS)


def test_unknown_to_success_without_evidence_fails():
    with pytest.raises(MissingOutcomeEvidenceError):
        validate_payment_transition(PaymentStatus.UNKNOWN, PaymentStatus.SUCCESS, evidence_id=None)

    with pytest.raises(MissingOutcomeEvidenceError):
        validate_payment_transition(PaymentStatus.UNKNOWN, PaymentStatus.SUCCESS, evidence_id="")


def test_unknown_to_captured_without_evidence_fails():
    with pytest.raises(MissingOutcomeEvidenceError):
        validate_payment_transition(PaymentStatus.UNKNOWN, PaymentStatus.CAPTURED, evidence_id=None)


def test_unknown_to_success_with_evidence_succeeds():
    assert validate_payment_transition(
        PaymentStatus.UNKNOWN, PaymentStatus.SUCCESS, evidence_id="inquiry:tx_998877"
    ) is True


def test_unknown_to_captured_with_evidence_succeeds():
    assert validate_payment_transition(
        PaymentStatus.UNKNOWN, PaymentStatus.CAPTURED, evidence_id="webhook:rzp:evt_1122"
    ) is True


def test_illegal_blocked_to_executed():
    # In ActionExecutionStatus, SUCCEEDED represents EXECUTED
    with pytest.raises(InvalidStateTransitionError):
        validate_action_transition(
            ActionExecutionStatus.BLOCKED_STAGE1_SAFETY,
            ActionExecutionStatus.SUCCEEDED,
        )


def test_illegal_executed_to_blocked():
    with pytest.raises(InvalidStateTransitionError):
        validate_action_transition(
            ActionExecutionStatus.SUCCEEDED,
            ActionExecutionStatus.BLOCKED_STAGE1_SAFETY,
        )


def test_illegal_settled_to_pending_or_unknown():
    with pytest.raises(InvalidStateTransitionError):
        validate_settlement_transition(
            SettlementStatus.SETTLED,
            SettlementStatus.PENDING,
        )

    with pytest.raises(InvalidStateTransitionError):
        validate_settlement_transition(
            SettlementStatus.SETTLED,
            SettlementStatus.UNKNOWN,
        )


def test_settled_allows_partial_and_mismatch():
    assert validate_settlement_transition(
        SettlementStatus.SETTLED, SettlementStatus.MISMATCH
    ) is True
    assert validate_settlement_transition(
        SettlementStatus.SETTLED, SettlementStatus.PARTIAL
    ) is True


def test_webhook_delivery_transitions():
    assert validate_webhook_transition(WebhookStatus.RECEIVED, WebhookStatus.PROCESSED) is True
    assert validate_webhook_transition(WebhookStatus.RECEIVED, WebhookStatus.DUPLICATE) is True
    assert validate_webhook_transition(WebhookStatus.RECEIVED, WebhookStatus.REJECTED_OUT_OF_ORDER) is True

    # Terminal state PROCESSED cannot transition again
    with pytest.raises(InvalidStateTransitionError):
        validate_webhook_transition(WebhookStatus.PROCESSED, WebhookStatus.RECEIVED)

    with pytest.raises(InvalidStateTransitionError):
        validate_webhook_transition(WebhookStatus.PROCESSED, WebhookStatus.DUPLICATE)
