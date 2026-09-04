"""Financial Opportunity Engine.

Coordinates the dynamic derivation of revenue recovery opportunities from
the Money Graph and Failure Intelligence.

Principles:
- RAY reasons from actual connected merchant data.
- Opportunities are DERIVED dynamically from payment/failure/customer context.
- No financial actions are executed (Stage 1 Safety Lock active).
- Pure calculation without mutating financial transaction states.
"""

from __future__ import annotations

import uuid
from typing import Optional
from sqlalchemy.orm import Session

from services.money_graph.service import MoneyGraphService
from services.opportunities.providers import (
    DecisionProvider,
    DeterministicDecisionProvider,
    OpportunityResult,
)


class FinancialOpportunityEngine:
    """Dynamic opportunity detection engine for merchant revenue recovery."""

    def __init__(
        self,
        money_graph_service: Optional[MoneyGraphService] = None,
        decision_provider: Optional[DecisionProvider] = None,
        session: Optional[Session] = None,
    ):
        self.money_graph_service = money_graph_service or MoneyGraphService(session=session)
        self.decision_provider = decision_provider or DeterministicDecisionProvider()

    def detect_recovery_opportunity(self, payment_id: uuid.UUID) -> OpportunityResult:
        """Dynamically detect and formulate a recovery opportunity for a payment.

        Workflow:
        1. Retrieve full Money Graph context (payment, customer, order, failure, history).
        2. If payment not found, raises ValueError.
        3. Pass context to DecisionProvider (default: DeterministicDecisionProvider).
        4. Return structured, explainable OpportunityResult with zero state mutations.
        """
        # 1. Retrieve Money Graph context
        context = self.money_graph_service.get_full_money_context(payment_id)
        if not context:
            raise ValueError(f"Payment with ID '{payment_id}' not found in Money Graph.")

        # 2. Evaluate through Decision Provider
        return self.decision_provider.evaluate(context)


def detect_recovery_opportunity(
    payment_id: uuid.UUID,
    session: Optional[Session] = None,
    provider: Optional[DecisionProvider] = None,
) -> OpportunityResult:
    """Convenience functional wrapper for detecting a recovery opportunity."""
    engine = FinancialOpportunityEngine(session=session, decision_provider=provider)
    return engine.detect_recovery_opportunity(payment_id)
