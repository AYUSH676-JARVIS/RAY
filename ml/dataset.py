"""ML Dataset Generation & Train/Val/Test Partitioning.

Generates statistically grounded dataset from payment and customer telemetry,
with strict train (70%), validation (15%), and test (15%) partitions.
"""

from __future__ import annotations

from typing import Tuple
import numpy as np


def generate_ml_features(
    num_samples: int = 1200,
    random_state: int = 42,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate reproducible feature matrices and ground-truth labels for ML models.
    
    Risk Model Features:
      [amount, customer_risk_score (0.0-1.0), prior_attempts, hour_of_day]
    Risk Model Target:
      binary fraud/terminal decline (0 or 1)
      
    Recovery Model Features:
      [latency_ms, is_retryable (0/1), amount, attempt_number]
    Recovery Model Target:
      recovery_probability (continuous 0.0 - 1.0)
    """
    rng = np.random.default_rng(random_state)

    # 1. Generate inputs
    amounts = rng.exponential(scale=120.0, size=num_samples) + 5.0
    customer_risk = rng.beta(1.5, 8.0, size=num_samples)  # Strictly [0.0, 1.0]
    prior_attempts = rng.integers(1, 5, size=num_samples)
    hour_of_day = rng.integers(0, 24, size=num_samples)
    latency_ms = rng.normal(loc=1200.0, scale=400.0, size=num_samples).clip(100, 8000)
    is_retryable = rng.choice([0, 1], p=[0.25, 0.75], size=num_samples)

    # 2. Ground-truth target generation with realistic signal
    # Risk score driven by customer risk, high amounts, prior attempts, and noise
    risk_signal = (
        0.55 * customer_risk
        + 0.30 * np.clip(amounts / 400.0, 0.0, 1.0)
        + 0.15 * ((prior_attempts - 1) / 3.0)
    )
    noise = rng.normal(0, 0.05, size=num_samples)
    threshold = float(np.percentile(risk_signal, 70))
    risk_labels = ((risk_signal + noise) >= threshold).astype(int)

    # Recovery target: higher for retryable, lower for multiple attempts, lower for high latency
    recovery_targets = (
        0.55 * is_retryable
        + 0.25 * (1.0 - customer_risk)
        - 0.10 * (prior_attempts - 1)
        - 0.05 * (latency_ms / 4000.0)
        + rng.normal(0, 0.05, size=num_samples)
    ).clip(0.0, 1.0)

    # Construct feature matrices
    X_risk = np.column_stack([amounts, customer_risk, prior_attempts, hour_of_day])
    y_risk = risk_labels

    X_rec = np.column_stack([latency_ms, is_retryable, amounts, prior_attempts])
    y_rec = recovery_targets

    return X_risk, y_risk, X_rec, y_rec


def train_val_test_split(
    X: np.ndarray,
    y: np.ndarray,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Deterministically split data into Train (70%), Validation (15%), and Test (15%)."""
    n = len(X)
    train_end = int(n * train_ratio)
    val_end = train_end + int(n * val_ratio)

    X_train, y_train = X[:train_end], y[:train_end]
    X_val, y_val = X[train_end:val_end], y[train_end:val_end]
    X_test, y_test = X[val_end:], y[val_end:]

    return X_train, y_train, X_val, y_val, X_test, y_test
