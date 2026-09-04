"""Batch 4 Audit Hash Chain & Decision Replay Invariant Tests."""

from __future__ import annotations

import decimal
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.audit.logger import AuditLogger
from services.audit.receipt import DecisionReceiptGenerator
from services.money_graph.models import ActorType, AuditEvent, Base, Merchant, PolicyDecisionType


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_audit_hash_chain_valid(test_db: Session):
    """Sequential audit events form a valid cryptographic SHA-256 hash chain."""
    merchant_id = uuid.uuid4()
    m = Merchant(id=merchant_id, name="Chain Test", slug=f"chain-{uuid.uuid4().hex[:6]}")
    test_db.add(m)
    test_db.commit()

    # Record 5 sequential audit events
    for i in range(5):
        AuditLogger.record_event(
            session=test_db,
            merchant_id=merchant_id,
            entity_type="PAYMENT",
            entity_id=uuid.uuid4(),
            event_type=f"PAYMENT_MUTATION_{i+1}",
            actor_type=ActorType.ACTION_LAYER,
            actor_id="executor_service",
            payload_after={"step": i + 1, "value": f"val_{i}"},
        )
    test_db.commit()

    is_valid, error = AuditLogger.verify_audit_chain(test_db, merchant_id)
    assert is_valid is True, f"Audit chain should be valid, but failed: {error}"
    assert error is None


def test_audit_hash_chain_detects_payload_tampering(test_db: Session):
    """Modifying an event payload in the database invalidates the cryptographic chain."""
    merchant_id = uuid.uuid4()
    m = Merchant(id=merchant_id, name="Tamper Test", slug=f"tamper-{uuid.uuid4().hex[:6]}")
    test_db.add(m)
    test_db.commit()

    for i in range(4):
        AuditLogger.record_event(
            session=test_db,
            merchant_id=merchant_id,
            entity_type="PAYMENT",
            entity_id=uuid.uuid4(),
            event_type=f"EVENT_{i+1}",
            actor_type=ActorType.AI_AGENT,
            actor_id="recovery_agent",
            payload_after={"amount": 100 * (i + 1)},
        )
    test_db.commit()

    # Tamper with event 2 directly in DB
    ev2 = test_db.query(AuditEvent).filter(AuditEvent.merchant_id == merchant_id, AuditEvent.sequence_number == 2).first()
    assert ev2 is not None
    ev2.payload_after_json = {"amount": 999999}  # Rogue DB modification
    test_db.commit()

    is_valid, error = AuditLogger.verify_audit_chain(test_db, merchant_id)
    assert is_valid is False
    assert "Audit tampering detected at sequence 2" in error


def test_audit_hash_chain_detects_deletion(test_db: Session):
    """Deleting a row from the middle of the audit chain is detected immediately."""
    merchant_id = uuid.uuid4()
    m = Merchant(id=merchant_id, name="Deletion Test", slug=f"del-{uuid.uuid4().hex[:6]}")
    test_db.add(m)
    test_db.commit()

    for i in range(4):
        AuditLogger.record_event(
            session=test_db,
            merchant_id=merchant_id,
            entity_type="PAYMENT",
            entity_id=uuid.uuid4(),
            event_type=f"EVENT_{i+1}",
            actor_type=ActorType.AI_AGENT,
            actor_id="recovery_agent",
            payload_after={"step": i + 1},
        )
    test_db.commit()

    # Delete event 3
    ev3 = test_db.query(AuditEvent).filter(AuditEvent.merchant_id == merchant_id, AuditEvent.sequence_number == 3).first()
    test_db.delete(ev3)
    test_db.commit()

    is_valid, error = AuditLogger.verify_audit_chain(test_db, merchant_id)
    assert is_valid is False
    assert "Audit chain break" in error


def test_decision_receipt_and_why_didnt_ray_act():
    """DecisionReceipt captures deterministic explanation for why RAY refused to act."""
    receipt = DecisionReceiptGenerator.generate_receipt(
        opportunity_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        decision=PolicyDecisionType.REJECTED.value,
        rule_matched="FRAUD_ZERO_TOLERANCE_RULE",
        reason="Transaction flagged with fraud indicators.",
        risk_score=0.92,
        amount=decimal.Decimal("500.00"),
        currency="USD",
        failure_code="FRAUD_SUSPECTED",
        attempt_count=1,
        evidence=["failure_category=FRAUD_SUSPECTED", "risk_score=0.92"],
        stage_1_safety_lock=True,
    )

    assert receipt.decision == "REJECTED"
    assert receipt.rule_matched == "FRAUD_ZERO_TOLERANCE_RULE"
    assert "fraud indicators" in receipt.why_didnt_ray_act


def test_decision_replay_exact_match():
    """Replaying stored context against policy engine asserts 100% deterministic reproducibility."""
    receipt = DecisionReceiptGenerator.generate_receipt(
        opportunity_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        decision=PolicyDecisionType.REJECTED.value,
        rule_matched="VELOCITY_LIMIT_EXCEEDED_RULE",
        reason="Velocity limit reached",
        risk_score=0.20,
        amount=decimal.Decimal("120.00"),
        currency="USD",
        failure_code="INSUFFICIENT_FUNDS",
        attempt_count=3,
        evidence=["attempt_count=3"],
    )

    is_identical, detail = DecisionReceiptGenerator.replay_decision(
        receipt=receipt,
        failure_code="INSUFFICIENT_FUNDS",
        attempt_count=3,
    )
    assert is_identical is True
    assert "100% deterministically reproducible" in detail
