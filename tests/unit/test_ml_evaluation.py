"""Unit & Benchmark Tests for ML Pipeline & Evaluation."""

from __future__ import annotations

import numpy as np
import pytest

from ml.dataset import generate_ml_features, train_val_test_split
from ml.evaluation.evaluator import run_ml_evaluation
from ml.recovery.model import RecoveryProbabilityEstimator
from ml.risk.model import TransactionRiskClassifier


def test_dataset_generation_and_partitioning():
    """Dataset generates correct feature dimensions and 70/15/15 partition sizes."""
    X_risk, y_risk, X_rec, y_rec = generate_ml_features(num_samples=1000, random_state=42)
    assert X_risk.shape == (1000, 4)
    assert len(y_risk) == 1000
    assert X_rec.shape == (1000, 4)
    assert len(y_rec) == 1000

    X_train, y_train, X_val, y_val, X_test, y_test = train_val_test_split(X_risk, y_risk)
    assert len(X_train) == 700
    assert len(X_val) == 150
    assert len(X_test) == 150


def test_ml_evaluation_pipeline_reproducible():
    """Full ML evaluation pipeline executes and meets production benchmark gates."""
    scorecard = run_ml_evaluation(num_samples=1000, random_state=42)

    risk_metrics = scorecard["risk_classification_metrics"]
    assert risk_metrics["roc_auc"] >= 0.80, f"ROC-AUC too low: {risk_metrics['roc_auc']}"
    assert risk_metrics["brier_score"] <= 0.20, f"Brier score uncalibrated: {risk_metrics['brier_score']}"
    assert risk_metrics["f1_score"] >= 0.70

    rec_metrics = scorecard["recovery_regression_metrics"]
    assert rec_metrics["mae"] <= 0.15
    assert rec_metrics["r2_score"] >= 0.75


def test_transaction_risk_classifier_strict_bounds():
    """TransactionRiskClassifier predictions are strictly bounded in [0.0, 1.0]."""
    clf = TransactionRiskClassifier(n_estimators=20, random_state=42)
    X, y, _, _ = generate_ml_features(num_samples=200, random_state=42)
    clf.fit(X, y)

    # Test extreme edge cases
    score_low = clf.predict_risk_score(amount=10.0, customer_risk=0.01, attempts=1, hour=12)
    assert 0.0 <= score_low <= 1.0

    score_high = clf.predict_risk_score(amount=5000.0, customer_risk=0.99, attempts=4, hour=3)
    assert 0.0 <= score_high <= 1.0
    assert score_high > score_low


def test_recovery_probability_estimator_strict_bounds():
    """RecoveryProbabilityEstimator predictions are strictly bounded in [0.0, 1.0]."""
    estimator = RecoveryProbabilityEstimator(n_estimators=20, random_state=42)
    _, _, X, y = generate_ml_features(num_samples=200, random_state=42)
    estimator.fit(X, y)

    prob = estimator.estimate_probability(latency_ms=1200, is_retryable=True, amount=100.0, attempt_num=1)
    assert 0.0 <= prob <= 1.0
