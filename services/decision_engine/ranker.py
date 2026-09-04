"""Decision Engine — Strategy Ranking & Expected Value Optimization."""

from __future__ import annotations

import decimal
from typing import Dict, List


class StrategyRanker:
    """Ranks and prioritizes recovery strategies using expected recoverable yield."""

    @staticmethod
    def calculate_expected_value(amount: decimal.Decimal, confidence_score: float) -> decimal.Decimal:
        """EV = Recoverable Amount * Confidence Score."""
        return amount * decimal.Decimal(str(round(confidence_score, 4)))

    def rank_opportunities(self, opportunities: List[Dict]) -> List[Dict]:
        """Rank opportunities descending by expected recoverable value."""
        return sorted(
            opportunities,
            key=lambda opp: self.calculate_expected_value(
                opp["estimated_recoverable_amount"], opp["confidence_score"]
            ),
            reverse=True,
        )
