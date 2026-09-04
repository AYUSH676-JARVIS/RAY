"""Dynamic Multi-Tier Configuration Hierarchy.

Precedence Order:
1. Payment-level overrides (highest priority, per-request)
2. Merchant-level overrides (per-tenant customization)
3. Global platform defaults (system-wide baseline)

Parameters:
- max_retries: Maximum automated retries per payment attempt
- cooldown_seconds: Minimum delay between successive attempts
- risk_threshold: Maximum customer risk score (0.0 to 1.0) before policy hard-blocks
- enabled_strategies: Set of permitted recovery strategies
- notification_preferences: Channels and thresholds for merchant alerts
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("ray.config")

# Canonical System-wide Global Defaults
GLOBAL_DEFAULTS: Dict[str, Any] = {
    "max_retries": 3,
    "cooldown_seconds": 30,
    "risk_threshold": 0.65,
    "enabled_strategies": [
        "OPTIMAL_RETRY_WINDOW",
        "SMART_ROUTING",
        "CUSTOMER_OUTREACH",
        "PARTIAL_CAPTURE",
    ],
    "notification_preferences": {
        "email_alerts": True,
        "slack_webhook": None,
        "notify_on_recovery": True,
        "notify_on_fraud": True,
    },
}


class MerchantConfigSchema(BaseModel):
    max_retries: int = Field(default=3, ge=1, le=10)
    cooldown_seconds: int = Field(default=30, ge=0, le=86400)
    risk_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    enabled_strategies: List[str] = Field(
        default_factory=lambda: [
            "OPTIMAL_RETRY_WINDOW",
            "SMART_ROUTING",
            "CUSTOMER_OUTREACH",
            "PARTIAL_CAPTURE",
        ]
    )
    notification_preferences: Dict[str, Any] = Field(
        default_factory=lambda: {
            "email_alerts": True,
            "slack_webhook": None,
            "notify_on_recovery": True,
            "notify_on_fraud": True,
        }
    )


class ResolvedConfig(BaseModel):
    source: str  # "GLOBAL_DEFAULT", "MERCHANT_OVERRIDE", "PAYMENT_OVERRIDE"
    max_retries: int
    cooldown_seconds: int
    risk_threshold: float
    enabled_strategies: List[str]
    notification_preferences: Dict[str, Any]


class ConfigHierarchyResolver:
    """Resolves hierarchical configuration by merging Global -> Merchant -> Payment levels."""

    @classmethod
    def resolve(
        cls,
        merchant_overrides: Optional[Dict[str, Any]] = None,
        payment_overrides: Optional[Dict[str, Any]] = None,
    ) -> ResolvedConfig:
        """Resolve effective configuration honoring strict hierarchy."""
        # 1. Start with deep copy of Global Defaults
        effective = copy.deepcopy(GLOBAL_DEFAULTS)
        source = "GLOBAL_DEFAULT"

        # 2. Merge Merchant-level overrides
        if merchant_overrides:
            for k, v in merchant_overrides.items():
                if k in effective and v is not None:
                    if isinstance(effective[k], dict) and isinstance(v, dict):
                        effective[k].update(v)
                    else:
                        effective[k] = v
            source = "MERCHANT_OVERRIDE"

        # 3. Merge Payment-level overrides
        if payment_overrides:
            for k, v in payment_overrides.items():
                if k in effective and v is not None:
                    if isinstance(effective[k], dict) and isinstance(v, dict):
                        effective[k].update(v)
                    else:
                        effective[k] = v
            source = "PAYMENT_OVERRIDE"

        return ResolvedConfig(
            source=source,
            max_retries=effective["max_retries"],
            cooldown_seconds=effective["cooldown_seconds"],
            risk_threshold=effective["risk_threshold"],
            enabled_strategies=effective["enabled_strategies"],
            notification_preferences=effective["notification_preferences"],
        )
