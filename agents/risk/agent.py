"""Risk Agent — Analyzes merchant risk, fraud indicators, and customer profiles."""

from __future__ import annotations

import uuid
from typing import Any, Dict


class RiskAgent:
    """Evaluates risk posture and declines recovery for suspicious transactions."""

    def __init__(self):
        self.agent_name = "risk"

    def assess_risk(self, customer_id: uuid.UUID, payment_id: uuid.UUID, failure_code: str) -> Dict[str, Any]:
        is_fraud = failure_code == "FRAUD_SUSPECTED"
        return {
            "agent": self.agent_name,
            "payment_id": str(payment_id),
            "customer_id": str(customer_id),
            "risk_score": 0.95 if is_fraud else 0.15,
            "risk_tier": "CRITICAL" if is_fraud else "LOW",
            "recommendation": "BLOCK" if is_fraud else "ALLOW_POLICY_CHECK",
        }
