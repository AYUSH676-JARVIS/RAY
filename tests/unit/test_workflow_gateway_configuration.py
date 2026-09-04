"""Gateway Configuration & Production Security Verification Tests.

Verifies:
1. Gateway selection is strictly driven by validated server configuration, NEVER by AI.
2. In non-production with GATEWAY_MODE=simulation -> SimulationGateway is resolved.
3. In test with GATEWAY_MODE=razorpay_test -> RazorpayGateway is resolved.
4. In production with GATEWAY_MODE=simulation -> Fail closed (prohibited).
5. In production with GATEWAY_MODE=razorpay_live but missing credentials -> Fail closed.
6. In production with valid live credentials -> RazorpayGateway is resolved.
7. Invalid GATEWAY_MODE raises ConfigurationError.
"""

from __future__ import annotations

import os
import pytest
from pydantic import SecretStr

from services.action_layer.gateway import RazorpayGateway, SimulationGateway
from services.config.settings import ConfigurationError, get_settings
from services.orchestrator.workflow import resolve_configured_gateway


@pytest.fixture(autouse=True)
def reset_settings():
    import services.config.settings
    services.config.settings._settings = None
    yield
    services.config.settings._settings = None


def test_simulation_mode_resolves_simulation_gateway(monkeypatch):
    """Assert non-production environment resolves SimulationGateway when configured."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GATEWAY_MODE", "simulation")

    gw = resolve_configured_gateway()
    assert isinstance(gw, SimulationGateway)
    assert gw.gateway_name == "SimulationGateway"


def test_razorpay_test_mode_resolves_razorpay_gateway(monkeypatch):
    """Assert razorpay_test mode resolves RazorpayGateway with test flags."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GATEWAY_MODE", "razorpay_test")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_testkey123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_test_secret123")

    gw = resolve_configured_gateway()
    assert isinstance(gw, RazorpayGateway)
    assert gw.is_test_mode is True
    assert gw.stage_1_safety_lock is True


def test_production_strictly_forbids_simulation_gateway(monkeypatch):
    """Assert production environment with GATEWAY_MODE=simulation fails closed."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GATEWAY_MODE", "simulation")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod_super_secure_key_that_is_at_least_32_bytes_long_123456")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "prod_webhook_secret_key_1234567890")

    # Invariant: AppSettings validation or resolve_configured_gateway must raise ConfigurationError
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_configured_gateway()
    assert "SimulationGateway is prohibited in production" in str(exc_info.value)


def test_production_fails_closed_when_live_credentials_missing(monkeypatch):
    """Assert production environment in razorpay_live mode without credentials fails closed."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GATEWAY_MODE", "razorpay_live")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod_super_secure_key_that_is_at_least_32_bytes_long_123456")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "prod_webhook_secret_key_1234567890")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        resolve_configured_gateway()
    assert "Live gateway credentials" in str(exc_info.value)


def test_production_resolves_live_gateway_when_configured(monkeypatch):
    """Assert production environment resolves live RazorpayGateway when valid credentials exist."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GATEWAY_MODE", "razorpay_live")
    monkeypatch.setenv("RAZORPAY_TEST_MODE", "false")
    monkeypatch.setenv("JWT_SECRET_KEY", "prod_super_secure_key_that_is_at_least_32_bytes_long_123456")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "prod_webhook_secret_key_1234567890")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_real_merchant_key_1234")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "rzp_live_real_merchant_secret_5678")

    gw = resolve_configured_gateway()
    assert isinstance(gw, RazorpayGateway)
    assert gw.is_test_mode is False
    assert gw.live_execution_enabled is True


def test_unsupported_gateway_mode_raises_configuration_error(monkeypatch):
    """Assert unrecognized gateway mode raises ConfigurationError."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GATEWAY_MODE", "stripe_live")

    with pytest.raises(ConfigurationError) as exc_info:
        resolve_configured_gateway()
    assert "Unsupported GATEWAY_MODE" in str(exc_info.value)
