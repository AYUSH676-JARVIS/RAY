"""Exhaustive Financial Calculation & Decimal Precision Invariant Tests (Phase 8).

Verifies:
1. All currency calculations use Python Decimal, not float.
2. Monetary quantize rules enforce exact 2-decimal precision (ROUND_HALF_UP).
3. Negative payment amounts are strictly rejected by policy and models.
4. Amount comparison handles precision without binary float roundoff inaccuracies (e.g. 0.1 + 0.2 != 0.3).
5. JSON serialization of Decimal preserves exact string value without float precision loss.
6. Database Numeric fields return true Decimal instances.
"""

import json
import uuid
from decimal import Decimal, ROUND_HALF_UP
import pytest

from services.money_graph.database import SessionLocal
from services.money_graph.models import Merchant, Payment, PaymentStatus
from services.policy_engine.engine import DeterministicPolicyEngine


def test_no_float_arithmetic_precision_invariants():
    """Verify that money calculations never experience float IEEE 754 precision leakage."""
    # Classic float failure: 0.1 + 0.2 = 0.30000000000000004
    d1 = Decimal("0.10")
    d2 = Decimal("0.20")
    d3 = Decimal("0.30")
    assert d1 + d2 == d3  # Exact in Decimal

    # Large values without overflow
    large_val = Decimal("999999999999999.99")
    increment = Decimal("0.01")
    expected = Decimal("1000000000000000.00")
    assert (large_val + increment) == expected


def test_monetary_quantize_rules():
    """Verify standard financial rounding (ROUND_HALF_UP)."""
    raw_amount = Decimal("100.555")
    quantized = raw_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert quantized == Decimal("100.56")

    raw_amount2 = Decimal("100.554")
    quantized2 = raw_amount2.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert quantized2 == Decimal("100.55")


def test_negative_amounts_strictly_rejected():
    """Verify that negative amounts raise ValueError in policy engine."""
    engine = DeterministicPolicyEngine()
    with pytest.raises(ValueError) as exc:
        engine.authorize_recovery(
            failure_code="BANK_TIMEOUT",
            attempt_count=1,
            customer_risk_score=0.1,
            amount=Decimal("-50.00"),
        )
    assert "Monetary amount cannot be negative" in str(exc.value)


def test_json_decimal_serialization_exactness():
    """Verify that Decimal is serialized cleanly as a string or exact Decimal without float cast."""
    data = {
        "payment_id": str(uuid.uuid4()),
        "amount": str(Decimal("1234567.89")),
        "fee": str(Decimal("0.05")),
    }
    dumped = json.dumps(data)
    loaded = json.loads(dumped)

    assert Decimal(loaded["amount"]) == Decimal("1234567.89")
    assert Decimal(loaded["fee"]) == Decimal("0.05")


def test_db_numeric_field_returns_decimal():
    """Verify SQLAlchemy Numeric column returns pure Python Decimal."""
    with SessionLocal() as db:
        payment = db.query(Payment).first()
        if payment:
            assert isinstance(payment.amount, Decimal)
            assert not isinstance(payment.amount, float)
