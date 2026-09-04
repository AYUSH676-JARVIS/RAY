"""ML Risk Model — Decline & Fraud Risk Classifier.

Classifies likelihood of transaction fraud and terminal decline based on
payment characteristics and historical customer telemetry.
"""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier


class TransactionRiskClassifier:
    """Predicts likelihood of transaction fraud and terminal decline."""

    def __init__(self, n_estimators: int = 50, random_state: int = 42):
        self.model = GradientBoostingClassifier(
            n_estimators=n_estimators,
            max_depth=3,
            learning_rate=0.1,
            random_state=random_state,
        )
        self.is_trained = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> TransactionRiskClassifier:
        """Train classifier on feature matrix.
        
        Features:
          [amount, customer_risk_score (0.0-1.0), prior_attempts, hour_of_day]
        """
        self.model.fit(X, y)
        self.is_trained = True
        return self

    def predict_risk_score(
        self,
        amount: float,
        customer_risk: float,
        attempts: int,
        hour: int,
    ) -> float:
        """Predict fraud/decline risk probability [0.0 - 1.0]."""
        # Enforce canonical customer risk bound
        bounded_risk = float(np.clip(customer_risk, 0.0, 1.0))

        if not self.is_trained:
            # Fallback heuristic if model uninitialized
            heuristic = 0.7 * bounded_risk + 0.1 * min(attempts / 4.0, 1.0)
            return float(round(np.clip(heuristic, 0.0, 1.0), 4))

        X_input = np.array([[float(amount), bounded_risk, int(attempts), int(hour)]])
        prob = self.model.predict_proba(X_input)[0][1]
        return float(round(float(prob), 4))
