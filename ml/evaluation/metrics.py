"""ML Model Evaluation & Metric Tracking."""

from typing import Dict, List
import numpy as np
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score


def evaluate_classification_performance(y_true: List[int], y_pred: List[int], y_prob: List[float]) -> Dict[str, float]:
    """Calculate standard evaluation metrics for risk models."""
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(set(y_true)) > 1 else 1.0,
    }
