"""Integration tests for Money Graph and Financial Opportunity API endpoints."""

import uuid
import pytest
from starlette.testclient import TestClient

from apps.api.main import app
from services.money_graph.database import SessionLocal
from services.money_graph.models import Payment, RecoveryOpportunity


@pytest.fixture(scope="module")
def client():
    """Synchronous test client for FastAPI with authenticated test principal."""
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer ray_test_merchant_admin"
        yield c


def test_get_payment_money_context_api(client: TestClient):
    """Verify GET /api/payments/{id}/context returns complete Money Graph context."""
    db = SessionLocal()
    try:
        sample_payment = db.query(Payment).filter(Payment.status == "FAILED").first()
        assert sample_payment is not None
        payment_id = str(sample_payment.id)
    finally:
        db.close()

    res = client.get(f"/api/payments/{payment_id}/context")
    assert res.status_code == 200
    data = res.json()

    assert "payment" in data
    assert data["payment"]["id"] == payment_id
    assert "customer" in data
    assert "order" in data
    assert "failure" in data
    assert "attempts" in data
    assert "history" in data
    if data["failure"]:
        assert "failure_code" in data["failure"]


def test_post_detect_opportunity_api_dynamic(client: TestClient):
    """Verify POST /api/opportunities/detect dynamically derives an explainable opportunity."""
    db = SessionLocal()
    try:
        sample_payment = db.query(Payment).filter(Payment.status == "FAILED").first()
        assert sample_payment is not None
        payment_id = str(sample_payment.id)
    finally:
        db.close()

    res = client.post("/api/opportunities/detect", json={"payment_id": payment_id})
    assert res.status_code == 200
    data = res.json()

    assert data["payment_id"] == payment_id
    assert data["is_eligible"] is True
    assert "recommended_strategy" in data
    assert "opportunity_score" in data
    assert "score_breakdown" in data
    assert "explanation" in data

    explanation = data["explanation"]
    assert "decision" in explanation
    assert "evidence" in explanation
    assert isinstance(explanation["evidence"], list)
    assert len(explanation["evidence"]) > 0
    assert "reason" in explanation
    assert data["is_financial_action_executed"] is False


def test_post_detect_opportunity_on_successful_payment(client: TestClient):
    """Verify POST /api/opportunities/detect on a SUCCESS payment returns is_eligible=False."""
    db = SessionLocal()
    try:
        sample_payment = db.query(Payment).filter(Payment.status == "SUCCESS").first()
        assert sample_payment is not None
        payment_id = str(sample_payment.id)
    finally:
        db.close()

    res = client.post("/api/opportunities/detect", json={"payment_id": payment_id})
    assert res.status_code == 200
    data = res.json()

    assert data["is_eligible"] is False
    assert data["recommended_strategy"] == "NO_ACTION"
    assert data["opportunity_score"] == 0.0


def test_get_opportunity_by_id_api(client: TestClient):
    """Verify GET /api/opportunities/{id} retrieves opportunity record."""
    db = SessionLocal()
    try:
        sample_opp = db.query(RecoveryOpportunity).first()
        assert sample_opp is not None
        opp_id = str(sample_opp.id)
    finally:
        db.close()

    res = client.get(f"/api/opportunities/{opp_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == opp_id
    assert "strategy_name" in data
    assert "confidence_score" in data


def test_get_payment_context_404(client: TestClient):
    """Verify 404 for non-existent payment context query."""
    random_id = str(uuid.uuid4())
    res = client.get(f"/api/payments/{random_id}/context")
    assert res.status_code == 404
