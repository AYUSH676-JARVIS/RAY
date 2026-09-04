"""Regression & Negative Tests for Phase 1 Authentication Hardening.

Verifies:
1. In production (ENVIRONMENT=production), test tokens (ray_test_*, test_*) MUST be rejected with HTTP 401.
2. In production, missing or invalid merchant identities fail closed.
3. In production, legitimate JWT tokens and live API keys authenticate successfully.
4. No authentication path can implicitly select a merchant or fall back to arbitrary database order in production.
"""

from __future__ import annotations

import os
import uuid
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from services.auth.models import Role, Permission
from services.auth.service import get_current_principal, create_access_token
from services.config.settings import AppSettings, get_settings
from services.money_graph.database import SessionLocal
from services.money_graph.models import Merchant


@pytest.fixture
def test_db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def existing_merchant(test_db):
    m = test_db.query(Merchant).first()
    if not m:
        m = Merchant(
            id=uuid.uuid4(),
            name="Auth Test Merchant",
            slug=f"auth-test-{uuid.uuid4().hex[:6]}",
            currency="USD",
            status="ACTIVE",
        )
        test_db.add(m)
        test_db.commit()
    return m


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod_super_secure_key_that_is_at_least_32_bytes_long_123456")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "prod_webhook_secret_key_1234567890")
    import services.config.settings
    services.config.settings._settings = None
    yield
    services.config.settings._settings = None


def test_production_rejects_ray_test_operator(prod_env, test_db):
    """Assert that ray_test_operator is rejected with HTTP 401 in production."""
    with pytest.raises(HTTPException) as exc_info:
        get_current_principal(
            authorization="Bearer ray_test_operator",
            x_merchant_id=None,
            db=test_db,
        )
    assert exc_info.value.status_code == 401
    assert "strictly forbidden in production" in exc_info.value.detail


def test_production_rejects_test_operator(prod_env, test_db):
    """Assert that test_operator is rejected with HTTP 401 in production."""
    with pytest.raises(HTTPException) as exc_info:
        get_current_principal(
            authorization="Bearer test_operator",
            x_merchant_id=None,
            db=test_db,
        )
    assert exc_info.value.status_code == 401
    assert "strictly forbidden in production" in exc_info.value.detail


def test_production_rejects_missing_merchant_jwt(prod_env, test_db):
    """Assert that a JWT without merchant_id claim is rejected."""
    settings = get_settings()

    token = create_access_token(
        {"role": "MERCHANT_ADMIN", "user_id": "test_user"},
        settings.JWT_SECRET_KEY.get_secret_value(),
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_principal(
            authorization=f"Bearer {token}",
            x_merchant_id=None,
            db=test_db,
        )
    assert exc_info.value.status_code == 401


def test_production_rejects_nonexistent_merchant_jwt(prod_env, test_db):
    """Assert that a JWT referencing a nonexistent merchant UUID is rejected."""
    settings = get_settings()

    fake_mid = str(uuid.uuid4())
    token = create_access_token(
        {"merchant_id": fake_mid, "role": "OPERATOR", "user_id": "operator_1"},
        settings.JWT_SECRET_KEY.get_secret_value(),
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_principal(
            authorization=f"Bearer {token}",
            x_merchant_id=None,
            db=test_db,
        )
    assert exc_info.value.status_code == 401
    assert "nonexistent merchant" in exc_info.value.detail


def test_production_authenticates_valid_jwt(prod_env, test_db, existing_merchant):
    """Assert that a properly signed JWT for a valid active merchant succeeds in production."""
    settings = get_settings()

    token = create_access_token(
        {"merchant_id": str(existing_merchant.id), "role": "OPERATOR", "user_id": "operator_42"},
        settings.JWT_SECRET_KEY.get_secret_value(),
    )

    principal = get_current_principal(
        authorization=f"Bearer {token}",
        x_merchant_id=None,
        db=test_db,
    )

    assert principal.merchant_id == existing_merchant.id
    assert principal.role == Role.OPERATOR
    assert principal.user_id == "operator_42"
    assert Permission.ACTION_EXECUTE in principal.permissions


def test_production_authenticates_valid_live_api_key(prod_env, test_db, existing_merchant):
    """Assert that a live API key (ray_live_<slug>_<key>) succeeds in production."""
    principal = get_current_principal(
        authorization=f"Bearer ray_live_{existing_merchant.slug}_sec9988776655",
        x_merchant_id=None,
        db=test_db,
    )

    assert principal.merchant_id == existing_merchant.id
    assert principal.role == Role.MERCHANT_ADMIN
    assert principal.user_id == f"api_key:{existing_merchant.slug}"


def test_non_production_allows_test_token(monkeypatch, test_db, existing_merchant):
    """Assert that development environment continues to support test tokens."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    import services.config.settings
    services.config.settings._settings = None

    principal = get_current_principal(
        authorization=f"Bearer ray_test_{existing_merchant.slug}_OPERATOR",
        x_merchant_id=None,
        db=test_db,
    )
    assert principal.merchant_id == existing_merchant.id
    assert principal.role == Role.OPERATOR

    services.config.settings._settings = None


def test_auth_token_issuance_in_dev(test_db, existing_merchant):
    """Assert that /api/v1/auth/token issues valid signed JWT in development mode."""
    from fastapi.testclient import TestClient
    from apps.api.main import app

    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/token",
        json={"merchant_slug": existing_merchant.slug, "role": "OPERATOR"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["role"] == "OPERATOR"
    assert data["merchant_id"] == str(existing_merchant.id)

    # Verify that the issued token authenticates against /api/v1/auth/me
    token = data["access_token"]
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["role"] == "OPERATOR"
    assert me_data["merchant_id"] == str(existing_merchant.id)


def test_auth_token_production_requires_credentials(prod_env, test_db, existing_merchant):
    """Assert that /api/v1/auth/token fails closed in production without credentials."""
    from fastapi.testclient import TestClient
    from apps.api.main import app

    client = TestClient(app)
    # Attempting to get token without credentials in production -> 401
    response = client.post(
        "/api/v1/auth/token",
        json={"merchant_slug": existing_merchant.slug, "role": "OPERATOR"},
    )
    assert response.status_code == 401
    assert "client_secret or api_key" in response.json()["detail"]
