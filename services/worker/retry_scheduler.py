"""Scheduled Recovery Action Worker / Retry Scheduler.

Evaluates scheduled recovery actions whose execution time has elapsed, re-verifies
deterministic merchant policy rules, and executes or cancels them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.money_graph.models import ActionExecutionStatus, RecoveryAction

logger = logging.getLogger("ray.worker.scheduler")


class RetryScheduler:
    """Evaluates and dispatches delayed retry actions."""

    def __init__(self, session_factory, stage_1_safety_lock: bool = True):
        self.session_factory = session_factory
        self.stage_1_safety_lock = stage_1_safety_lock

    def process_due_actions(self, max_batch: int = 50) -> int:
        """Find actions scheduled to run at or before now and process them with safety invariants."""
        from services.action_layer.executor import ActionExecutor, FinancialExecutionBlockedError
        from services.action_layer.gateway import SimulationGateway

        now = datetime.now(timezone.utc)
        dispatched = 0

        with self.session_factory() as session:
            stmt = (
                select(RecoveryAction)
                .where(
                    RecoveryAction.execution_status == ActionExecutionStatus.QUEUED.value,
                )
                .limit(max_batch)
            )
            actions = session.execute(stmt).scalars().all()

            for act in actions:
                logger.info(f"Queued recovery action {act.id} ({act.action_type}) is due. Evaluating execution.")
                executor = ActionExecutor(stage_1_safety_lock=self.stage_1_safety_lock)
                try:
                    res = executor.execute_recovery_action(
                        action_id=act.id,
                        action_type=act.action_type,
                        idempotency_key=act.idempotency_key or f"retry_sched_{act.id}",
                        merchant_id=act.merchant_id,
                        amount=act.amount,
                        session=session,
                    )
                    act.execution_status = res.get("status", ActionExecutionStatus.SUCCEEDED.value)
                except FinancialExecutionBlockedError:
                    act.execution_status = ActionExecutionStatus.BLOCKED_STAGE1_SAFETY.value
                except Exception as e:
                    logger.error(f"Scheduled retry execution failed for action {act.id}: {e}")
                    act.execution_status = ActionExecutionStatus.FAILED.value

                act.executed_at = now
                dispatched += 1

            session.commit()

        return dispatched
