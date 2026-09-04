"""Integration Test Suite for Merchant Configuration Hierarchy & Auditing.

Verifies:
1. Hierarchical resolution: Global -> Merchant -> Payment levels.
2. GET /api/v1/merchants/config returns resolved parameters.
3. PUT /api/v1/merchants/config persists new configuration and writes AuditEvent.
4. RBAC protection: READ_ONLY cannot modify configuration (403 Forbidden).
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from services.config.hierarchy import ConfigHierarchyResolver
from services.money_graph.database import SessionLocal
from services.money_graph.models import AuditEvent


@pytest.fixture
def client():
    return TestClient(app)


def test_config_hierarchy_resolver_precedence():
    """Verify Global -> Merchant -> Payment precedence hierarchy."""
    # 1. Base resolution: Global defaults
    base = ConfigHierarchyResolver.resolve()
    assert base.max_retries == 3
    assert base.risk_threshold == 0.65
    assert base.source == "GLOBAL_DEFAULT"

    # 2. Merchant override
    merchant_ovr = {"max_retries": 5, "risk_threshold": 0.80}
    m_resolved = ConfigHierarchyResolver.resolve(merchant_overrides=merchant_ovr)
    assert m_resolved.max_retries == 5
    assert m_resolved.risk_threshold == 0.80
    assert m_resolved.cooldown_seconds == 30  # Preserves global default
    assert m_resolved.source == "MERCHANT_OVERRIDE"

    # 3. Payment override
    pay_ovr = {"max_retries": 1, "cooldown_seconds": 60}
    p_resolved = ConfigHierarchyResolver.resolve(
        merchant_overrides=merchant_ovr, payment_overrides=pay_ovr
    )
    assert p_resolved.max_retries == 1  # Overridden by payment
    assert p_resolved.risk_threshold == 0.80  # Inherited from merchant
    assert p_resolved.cooldown_seconds == 60  # Overridden by payment
    assert p_resolved.source == "PAYMENT_OVERRIDE"


def test_merchant_config_get_and_put_api(client):
    """GET and PUT /api/v1/merchants/config with cryptographic audit logging."""
    headers = {"Authorization": "Bearer ray_test_merchant_admin"}

    # 1. Get current config
    get_res = client.get("/api/v1/merchants/config", headers=headers)
    assert get_res.status_code == 200
    cfg = get_res.json()
    assert "max_retries" in cfg
    assert "enabled_strategies" in cfg

    # 2. Update config
    update_payload = {
        "max_retries": 4,
        "cooldown_seconds": 45,
        "risk_threshold": 0.70,
        "enabled_strategies": ["OPTIMAL_RETRY_WINDOW", "SMART_ROUTING"],
        "notification_preferences": {"email_alerts": True, "notify_on_recovery": True},
    }
    put_res = client.put("/api/v1/merchants/config", headers=headers, json=update_payload)
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["max_retries"] == 4
    assert updated["cooldown_seconds"] == 45
    assert updated["risk_threshold"] == 0.70

    # 3. Verify audit record written
    with SessionLocal() as session:
        audit = (
            session.query(AuditEvent)
            .filter_by(event_type="MERCHANT_CONFIG_UPDATED")
            .order_by(AuditEvent.timestamp.desc())
            .first()
        )
        assert audit is not None
        assert audit.payload_after_json["max_retries"] == 4


def test_merchant_config_rbac_forbidden(client):
    """Users without SETTINGS_MANAGE cannot update configuration."""
    headers = {"Authorization": "Bearer ray_test_analyst"}
    update_payload = {"max_retries": 2}
    res = client.put("/api/v1/merchants/config", headers=headers, json=update_payload)
    assert res.status_code == 403
