"""Finance Agent — Reconciles ledger accounts and verifies settlement balances."""

from __future__ import annotations

import decimal
import uuid
from typing import Any, Dict


class FinanceAgent:
    """Monitors merchant settlement cycles and ledger balance discrepancies."""

    def __init__(self):
        self.agent_name = "finance"

    def audit_settlement_discrepancy(
        self,
        payment_id: uuid.UUID,
        expected_amount: decimal.Decimal,
        settled_amount: decimal.Decimal,
    ) -> Dict[str, Any]:
        diff = expected_amount - settled_amount
        return {
            "agent": self.agent_name,
            "payment_id": str(payment_id),
            "discrepancy": str(diff),
            "reconciled": diff == decimal.Decimal("0.00"),
        }
