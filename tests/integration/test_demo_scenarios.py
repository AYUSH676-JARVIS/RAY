"""TASK 19 — Deterministic Demo Scenario Test Suite.

Verifies:
1. Scenario A: Healthy Recovery (₹2,500 Bank Timeout -> Smart Retry -> Succeeded)
2. Scenario B: Fraud Block (₹2,500 Fraud Suspected -> Policy Deny -> Zero Gateway Calls)
3. Scenario C: Unknown Gateway (₹2,500 Bank Timeout -> Gateway Timeout -> UNKNOWN State)
4. Scenario D: Terminal Decline (₹2,500 Card Expired -> Prohibited Retry -> FAILED)
5. Scenario E: Duplicate Request (Concurrent Replay -> Exactly 1 Execution)
6. API Endpoints: GET /api/scenarios and POST /api/scenarios/run
"""

from __future__ import annotations

import uuid
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from services.money_graph.models import Base, Merchant, PaymentStatus
from services.scenarios.engine import DemoScenarioEngine, ScenarioID, SCENARIO_DEFINITIONS


@pytest.fixture
def scenario_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    merchant_id = uuid.uuid4()
    merchant = Merchant(
        id=merchant_id,
        name="Demo Scenarios Merchant",
        slug=f"demo-merch-{uuid.uuid4().hex[:6]}",
    )
    session.add(merchant)
    session.commit()

    yield session, merchant_id
    session.close()


def test_scenario_a_healthy_recovery(scenario_db):
    """Scenario A: Healthy recovery with temporary bank timeout executes and settles."""
    session, merchant_id = scenario_db

    result = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_A)

    assert result.status in ("COMPLETED", "RECOVERY_SUCCESS")
    assert result.outcome_result["is_recovered"] is True
    assert result.opportunity.amount == Decimal("2500.00")
    assert result.decision_receipt is not None
    assert result.explanation is not None
    assert "2500.00" in result.explanation.what_happened


def test_scenario_b_fraud_block(scenario_db):
    """Scenario B: Fraud velocity triggers deterministic policy block with zero gateway calls."""
    session, merchant_id = scenario_db

    result = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_B)

    assert result.status == "POLICY_BLOCKED"
    assert result.policy_decision["decision"] == "REJECTED"
    # Action stage must be skipped
    action_stage = next(s for s in result.stages if s.stage_name == "ACTION")
    assert action_stage.status.value == "SKIPPED"
    assert result.explanation is not None
    assert "fraud" in result.explanation.why_it_happened.lower()


def test_scenario_c_unknown_gateway(scenario_db):
    """Scenario C: Gateway timeout enters UNKNOWN state and blocks blind retries."""
    session, merchant_id = scenario_db

    result = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_C)

    assert result.status == "UNKNOWN"
    assert result.action_result["status"] == "UNKNOWN"
    assert result.explanation is not None
    assert "UNKNOWN" in result.explanation.what_action_was_taken


def test_scenario_d_terminal_decline(scenario_db):
    """Scenario D: Terminal decline (CARD_EXPIRED) prevents automated retry."""
    session, merchant_id = scenario_db

    result = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_D)

    assert result.status == "POLICY_BLOCKED"
    assert result.policy_decision["decision"] == "REJECTED"
    assert "EXPIRED" in result.policy_decision["rule_matched"]
    assert result.explanation is not None


def test_scenario_e_duplicate_request(scenario_db):
    """Scenario E: Duplicate request returns cached receipt and prevents double execution."""
    session, merchant_id = scenario_db

    res1 = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_E)
    res2 = DemoScenarioEngine.execute_scenario(session, merchant_id, ScenarioID.SCENARIO_E)

    assert res1.decision_receipt.receipt_id == res2.decision_receipt.receipt_id
    assert res1.status == res2.status


def test_api_list_and_run_scenarios():
    """API endpoints /api/scenarios and /api/scenarios/run work end-to-end."""
    client = TestClient(app)
    headers = {"Authorization": "Bearer ray_test_operator"}

    # 1. List scenarios
    list_res = client.get("/api/scenarios", headers=headers)
    assert list_res.status_code == 200
    scenarios = list_res.json()
    assert len(scenarios) == 6
    ids = [s["id"] for s in scenarios]
    assert "scenario_a" in ids
    assert "scenario_b" in ids
    assert "scenario_c" in ids
    assert "scenario_d" in ids
    assert "scenario_e" in ids
    assert "scenario_f" in ids

    # 2. Run Scenario A via API
    run_res = client.post(
        "/api/scenarios/run",
        json={"scenario_id": "scenario_a"},
        headers=headers,
    )
    assert run_res.status_code == 200
    data = run_res.json()
    assert data["status"] in ("COMPLETED", "RECOVERY_SUCCESS")
    assert len(data["stages"]) >= 9
    assert data["explanation"] is not None


def test_scenario_f_unknown_outcome(scenario_db):
    """Scenario F: Ambiguous gateway outcome enters UNKNOWN state and requires reconciliation."""
    session, merchant_id = scenario_db
    result = DemoScenarioEngine.execute_scenario(
        session=session,
        merchant_id=merchant_id,
        scenario_id=ScenarioID.SCENARIO_F,
    )
    assert result.status == "UNKNOWN"
    assert result.decision_receipt is not None
    action_stage = next(s for s in result.stages if s.stage_name == "ACTION")
    assert action_stage.status.value == "UNKNOWN"


