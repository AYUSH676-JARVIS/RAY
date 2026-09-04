"""ML Recovery Model — Smart Retry Probability Estimator.

Estimates the empirical probability of payment recovery across candidate strategies.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestRegressor


class RecoveryProbabilityEstimator:
    """Estimates recovery probability based on failure characteristics and attempt history."""

    def __init__(self, n_estimators: int = 50, random_state: int = 42):
        self.model = RandomForestRegressor(
            n_estimators=n_estimators,
            max_depth=5,
            random_state=random_state,
        )
        self.is_trained = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> RecoveryProbabilityEstimator:
        """Train estimator on feature matrix.
        
        Features:
          [latency_ms, is_retryable (0/1), amount, attempt_number]
        """
        self.model.fit(X, y)
        self.is_trained = True
        return self

    def estimate_probability(
        self,
        latency_ms: int,
        is_retryable: bool,
        amount: float,
        attempt_num: int,
    ) -> float:
        """Estimate recovery probability strictly bounded in [0.0, 1.0]."""
        if not self.is_trained:
            # Domain heuristic fallback
            base = 0.65 if is_retryable else 0.15
            penalty = 0.15 * max(0, attempt_num - 1)
            return float(round(max(0.05, min(0.95, base - penalty)), 4))

        X_input = np.array([[float(latency_ms), 1.0 if is_retryable else 0.0, float(amount), float(attempt_num)]])
        pred = self.model.predict(X_input)[0]
        return float(round(float(np.clip(pred, 0.0, 1.0)), 4))
