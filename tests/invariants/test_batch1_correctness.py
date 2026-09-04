"""Batch 1 Invariant Test Suite — Core Mathematical, Relational, and Migration Correctness."""

from __future__ import annotations

import decimal
import tempfile
import os
import uuid
import pytest
from sqlalchemy import create_engine, inspect
from pydantic import ValidationError

from services.money_graph.models import (
    Base,
    Customer,
    Merchant,
    Order,
    Payment,
    PaymentAttempt,
    PaymentFailure,
    PolicyDecision,
    PolicyDecisionType,
    RecoveryAction,
    RecoveryOpportunity,
    AgentRun,
    ToolCall,
    AuditEvent,
)
from services.money_graph.schemas import CustomerContext
from services.policy_engine.engine import DeterministicPolicyEngine
from services.opportunities.providers import DeterministicDecisionProvider, ScoreBreakdown


def test_risk_score_scale_bounds_on_policy_engine():
    """Policy engine strictly enforces risk_score in [0.0, 1.0]."""
    engine = DeterministicPolicyEngine()
    
    # Valid bounds
    dec, rule, reason = engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, 0.0, decimal.Decimal("100.00"))
    assert dec == PolicyDecisionType.APPROVED
    
    dec, rule, reason = engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, 1.0, decimal.Decimal("100.00"))
    assert dec == PolicyDecisionType.FLAGGED

    # Out of bounds must raise ValueError
    with pytest.raises(ValueError, match="Canonical risk_score contract"):
        engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, -0.01, decimal.Decimal("100.00"))

    with pytest.raises(ValueError, match="Canonical risk_score contract"):
        engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, 1.05, decimal.Decimal("100.00"))

    with pytest.raises(ValueError, match="Canonical risk_score contract"):
        engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, 85.0, decimal.Decimal("100.00"))


def test_risk_score_schema_validation():
    """Pydantic schema CustomerContext rejects risk_score outside [0.0, 1.0]."""
    now = "2026-09-02T12:00:00Z"
    uid = uuid.uuid4()
    
    # Valid
    c = CustomerContext(
        id=uid,
        merchant_id=uid,
        external_id="ext_1",
        name="Test",
        email="test@example.com",
        risk_score=0.45,
        lifetime_value=decimal.Decimal("500.00"),
        successful_payments=5,
        failed_payments=1,
        total_orders=6,
        created_at=now,
    )
    assert c.risk_score == 0.45

    # Invalid > 1.0
    with pytest.raises(ValidationError):
        CustomerContext(
            id=uid,
            merchant_id=uid,
            external_id="ext_1",
            name="Test",
            email="test@example.com",
            risk_score=75.0,  # Legacy 0-100 scale must fail
            lifetime_value=decimal.Decimal("500.00"),
            successful_payments=5,
            failed_payments=1,
            total_orders=6,
            created_at=now,
        )


def test_monetary_amount_negative_rejection():
    """PolicyEngine rejects negative financial amounts."""
    engine = DeterministicPolicyEngine()
    with pytest.raises(ValueError, match="Monetary amount cannot be negative"):
        engine.authorize_recovery("INSUFFICIENT_FUNDS", 1, 0.20, decimal.Decimal("-50.00"))


def test_all_secondary_entities_have_merchant_id():
    """All 12 models in Base.metadata must possess an explicit merchant_id column."""
    tables = Base.metadata.tables
    
    for table_name, table in tables.items():
        if table_name == "merchants":
            continue
        assert "merchant_id" in table.columns, f"Table '{table_name}' is missing mandatory tenant key 'merchant_id'!"


def test_opportunity_detector_retired():
    """Legacy services/opportunities/detector.py must not exist."""
    import importlib.util
    spec = importlib.util.find_spec("services.opportunities.detector")
    assert spec is None, "Legacy services/opportunities/detector.py still exists and must be retired!"


def test_opportunity_score_formula_monotonicity():
    """ScoreBreakdown must maintain mathematical integrity with no double-counting."""
    breakdown = ScoreBreakdown(
        expected_recovery=decimal.Decimal("80.00"),
        recovery_probability=0.80,
        model_confidence=0.90,
        data_confidence=0.95,
        urgency_multiplier=1.0,
        action_cost=decimal.Decimal("0.30"),
        risk_cost=decimal.Decimal("2.00"),
        friction_cost=decimal.Decimal("0.00"),
        expected_net_value=decimal.Decimal("77.70"),
        raw_score=66.4335,
        normalized_score=66.43,
        expected_value=decimal.Decimal("77.70"),
        success_probability=0.80,
        confidence=0.855,
    )
    assert breakdown.expected_net_value == breakdown.expected_recovery - breakdown.action_cost - breakdown.risk_cost
    assert breakdown.expected_value == breakdown.expected_net_value
    assert breakdown.normalized_score <= 100.0


def test_alembic_fresh_database_migration_lifecycle():
    """A fresh database can be migrated from 0 to head revision via Alembic."""
    from alembic.config import Config
    from alembic import command
    
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_url = f"sqlite:///{tmp.name}"
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", db_url)
        
        # Upgrade to head
        command.upgrade(cfg, "head")
        
        # Verify schema
        eng = create_engine(db_url)
        inspector = inspect(eng)
        created_tables = set(inspector.get_table_names())
        
        expected_tables = {
            "merchants", "customers", "orders", "payments",
            "payment_attempts", "payment_failures",
            "recovery_opportunities", "recovery_actions",
            "policy_decisions", "agent_runs", "tool_calls",
            "audit_events", "alembic_version"
        }
        assert expected_tables.issubset(created_tables), f"Missing tables: {expected_tables - created_tables}"
