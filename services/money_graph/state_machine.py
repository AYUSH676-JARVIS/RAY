"""Financial State Machines & Transition Validation.

Enforces valid financial transitions and prevents impossible transaction states.
Guarantees:
- Terminal states (SUCCESS, CAPTURED, FAILED, REFUNDED, CHARGEBACK) cannot regress to PENDING.
- Exiting UNKNOWN state strictly requires authoritative evidence.
- Settlement state consistency: settled transactions cannot be retried.
- Action execution safety: blocked actions cannot be executed; succeeded actions cannot be blocked.
"""

from __future__ import annotations

import enum
from typing import Optional, Set
from services.money_graph.models import PaymentStatus, ActionExecutionStatus, SettlementStatus


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal financial state transition is attempted."""
    pass


class MissingOutcomeEvidenceError(ValueError):
    """Raised when resolving an UNKNOWN state without authoritative evidence."""
    pass


class IllegalSettlementActionError(ValueError):
    """Raised when attempting action on an already settled transaction."""
    pass


# Valid Payment Status Transitions
VALID_PAYMENT_TRANSITIONS: dict[PaymentStatus, Set[PaymentStatus]] = {
    PaymentStatus.CREATED: {
        PaymentStatus.PROCESSING,
        PaymentStatus.PENDING,
        PaymentStatus.CANCELLED,
        PaymentStatus.FAILED,
    },
    PaymentStatus.PROCESSING: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.SUCCESS,
        PaymentStatus.PENDING,
        PaymentStatus.UNKNOWN,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    },
    PaymentStatus.PENDING: {
        PaymentStatus.PROCESSING,
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.SUCCESS,
        PaymentStatus.UNKNOWN,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    },
    # Ambiguous gateway timeout state requires authoritative evidence to exit
    PaymentStatus.UNKNOWN: {
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    },
    PaymentStatus.AUTHORIZED: {
        PaymentStatus.CAPTURED,
        PaymentStatus.SUCCESS,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    },
    PaymentStatus.CAPTURED: {
        PaymentStatus.SUCCESS,
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CHARGEBACK,
    },
    PaymentStatus.SUCCESS: {
        PaymentStatus.REFUNDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.CHARGEBACK,
    },
    # Terminal states — regression to PENDING or earlier states is strictly prohibited
    PaymentStatus.FAILED: set(),
    PaymentStatus.REFUNDED: set(),
    PaymentStatus.CHARGEBACK: set(),
    PaymentStatus.CANCELLED: set(),
    PaymentStatus.PARTIALLY_REFUNDED: {
        PaymentStatus.REFUNDED,
        PaymentStatus.CHARGEBACK,
    },
}


# Valid Action Execution Transitions
VALID_ACTION_TRANSITIONS: dict[ActionExecutionStatus, Set[ActionExecutionStatus]] = {
    ActionExecutionStatus.REQUESTED: {
        ActionExecutionStatus.AUTHORIZED,
        ActionExecutionStatus.BLOCKED_STAGE1_SAFETY,
        ActionExecutionStatus.CANCELLED,
        ActionExecutionStatus.REJECTED,
    },
    ActionExecutionStatus.BLOCKED_STAGE1_SAFETY: {
        ActionExecutionStatus.CANCELLED,
    },
    ActionExecutionStatus.AUTHORIZED: {
        ActionExecutionStatus.QUEUED,
        ActionExecutionStatus.PROCESSING,
        ActionExecutionStatus.CANCELLED,
    },
    ActionExecutionStatus.QUEUED: {
        ActionExecutionStatus.PROCESSING,
        ActionExecutionStatus.CANCELLED,
    },
    ActionExecutionStatus.PROCESSING: {
        ActionExecutionStatus.SENT,
        ActionExecutionStatus.UNKNOWN,
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    },
    ActionExecutionStatus.SENT: {
        ActionExecutionStatus.ACCEPTED,
        ActionExecutionStatus.REJECTED,
        ActionExecutionStatus.UNKNOWN,
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    },
    ActionExecutionStatus.UNKNOWN: {
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
        ActionExecutionStatus.ACCEPTED,
        ActionExecutionStatus.REJECTED,
    },
    ActionExecutionStatus.ACCEPTED: {
        ActionExecutionStatus.SUCCEEDED,
        ActionExecutionStatus.FAILED,
    },
    # Terminal states
    ActionExecutionStatus.SUCCEEDED: set(),
    ActionExecutionStatus.FAILED: set(),
    ActionExecutionStatus.REJECTED: set(),
    ActionExecutionStatus.CANCELLED: set(),
}


# Valid Settlement Status Transitions
VALID_SETTLEMENT_TRANSITIONS: dict[SettlementStatus, Set[SettlementStatus]] = {
    SettlementStatus.UNKNOWN: {
        SettlementStatus.PENDING,
        SettlementStatus.SETTLED,
        SettlementStatus.PARTIAL,
        SettlementStatus.MISMATCH,
        SettlementStatus.FAILED,
    },
    SettlementStatus.PENDING: {
        SettlementStatus.SETTLED,
        SettlementStatus.PARTIAL,
        SettlementStatus.MISMATCH,
        SettlementStatus.FAILED,
    },
    SettlementStatus.SETTLED: {
        SettlementStatus.MISMATCH,
        SettlementStatus.PARTIAL,
    },
    SettlementStatus.PARTIAL: {
        SettlementStatus.SETTLED,
        SettlementStatus.MISMATCH,
    },
    SettlementStatus.MISMATCH: {
        SettlementStatus.SETTLED,
        SettlementStatus.FAILED,
    },
    SettlementStatus.FAILED: set(),
}


# Valid Webhook Delivery Lifecycle Transitions
class WebhookStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    REJECTED = "REJECTED"
    REJECTED_OUT_OF_ORDER = "REJECTED_OUT_OF_ORDER"
    DUPLICATE = "DUPLICATE"


VALID_WEBHOOK_TRANSITIONS: dict[WebhookStatus, Set[WebhookStatus]] = {
    WebhookStatus.RECEIVED: {
        WebhookStatus.PROCESSED,
        WebhookStatus.REJECTED,
        WebhookStatus.REJECTED_OUT_OF_ORDER,
        WebhookStatus.DUPLICATE,
    },
    WebhookStatus.PROCESSED: set(),
    WebhookStatus.REJECTED: set(),
    WebhookStatus.REJECTED_OUT_OF_ORDER: set(),
    WebhookStatus.DUPLICATE: set(),
}


def validate_payment_transition(
    current: PaymentStatus | str,
    target: PaymentStatus | str,
    evidence_id: Optional[str] = None,
) -> bool:
    """Validate payment lifecycle state transition.
    
    Invariants:
    - Rejects transitions not defined in VALID_PAYMENT_TRANSITIONS.
    - Exiting UNKNOWN state strictly requires non-empty evidence_id.
    """
    curr_enum = PaymentStatus(current) if isinstance(current, str) else current
    target_enum = PaymentStatus(target) if isinstance(target, str) else target

    if curr_enum == target_enum:
        return True

    allowed = VALID_PAYMENT_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal payment transition from '{curr_enum.value}' to '{target_enum.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}."
        )

    # UNKNOWN resolution invariant: requires evidence
    if curr_enum == PaymentStatus.UNKNOWN and not evidence_id:
        raise MissingOutcomeEvidenceError(
            f"Cannot resolve payment from UNKNOWN to '{target_enum.value}' without authoritative evidence (e.g. gateway inquiry or webhook ID)."
        )

    return True


def validate_action_transition(
    current: ActionExecutionStatus | str,
    target: ActionExecutionStatus | str,
) -> bool:
    """Validate recovery action execution lifecycle state transition."""
    curr_enum = ActionExecutionStatus(current) if isinstance(current, str) else current
    target_enum = ActionExecutionStatus(target) if isinstance(target, str) else target

    if curr_enum == target_enum:
        return True

    allowed = VALID_ACTION_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal action transition from '{curr_enum.value}' to '{target_enum.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}."
        )

    return True


def validate_settlement_transition(
    current: SettlementStatus | str,
    target: SettlementStatus | str,
    evidence_id: Optional[str] = None,
) -> bool:
    """Validate settlement reconciliation state transition."""
    curr_enum = SettlementStatus(current) if isinstance(current, str) else current
    target_enum = SettlementStatus(target) if isinstance(target, str) else target

    if curr_enum == target_enum:
        return True

    allowed = VALID_SETTLEMENT_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal settlement transition from '{curr_enum.value}' to '{target_enum.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}."
        )

    return True


def validate_webhook_transition(
    current: WebhookStatus | str,
    target: WebhookStatus | str,
) -> bool:
    """Validate webhook delivery lifecycle transition."""
    curr_enum = WebhookStatus(current) if isinstance(current, str) else current
    target_enum = WebhookStatus(target) if isinstance(target, str) else target

    if curr_enum == target_enum:
        return True

    allowed = VALID_WEBHOOK_TRANSITIONS.get(curr_enum, set())
    if target_enum not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal webhook transition from '{curr_enum.value}' to '{target_enum.value}'. "
            f"Allowed next states: {[s.value for s in allowed]}."
        )

    return True
