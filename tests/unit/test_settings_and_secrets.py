"""Unit Test Suite for Settings & Secrets Fail-Closed Validation.

Verifies:
1. AppSettings loads valid development defaults.
2. Secret redaction masks sensitive credentials.
3. Fail-closed production validation:
   - Rejects development JWT secret in production.
   - Rejects wildcard CORS in production.
   - Rejects SQLite in production.
   - Requires RAZORPAY_WEBHOOK_SECRET.
"""

import pytest
from pydantic import SecretStr

from services.config.settings import AppSettings, ConfigurationError


def test_development_settings_defaults():
    """AppSettings initializes cleanly with development defaults."""
    settings = AppSettings(ENVIRONMENT="development")
    assert settings.ENVIRONMENT == "development"
    assert not settings.is_production
    assert settings.API_PORT == 8000
    assert len(settings.origins_list) >= 1


def test_secret_redaction():
    """Secrets are masked in redacted dictionary."""
    settings = AppSettings(
        ENVIRONMENT="development",
        RAZORPAY_KEY_SECRET=SecretStr("super_confidential_secret"),
    )
    redacted = settings.get_redacted_dict()
    assert redacted["RAZORPAY_KEY_SECRET"] == "********"


def test_production_fail_closed_on_dev_jwt():
    """Production mode rejects default development JWT secret."""
    with pytest.raises(ConfigurationError, match="JWT_SECRET_KEY"):
        AppSettings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            RAZORPAY_WEBHOOK_SECRET=SecretStr("valid_webhook_secret_key_prod"),
            JWT_SECRET_KEY=SecretStr("ray_dev_jwt_secret_key_minimum_32_bytes_long"),
        )


def test_production_fail_closed_on_sqlite():
    """Production mode rejects SQLite database connection."""
    with pytest.raises(ConfigurationError, match="SQLite database is prohibited"):
        AppSettings(
            ENVIRONMENT="production",
            DATABASE_URL="sqlite:///test.db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("valid_webhook_secret_key_prod"),
        )


def test_production_fail_closed_on_missing_webhook_secret():
    """Production mode rejects missing webhook secret."""
    with pytest.raises(ConfigurationError, match="RAZORPAY_WEBHOOK_SECRET"):
        AppSettings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=None,
        )


def test_production_valid_configuration():
    """Production mode passes when all prerequisites and strong secrets are provided."""
    settings = AppSettings(
        ENVIRONMENT="production",
        DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
        JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
        RAZORPAY_WEBHOOK_SECRET=SecretStr("prod_webhook_hmac_secret_key_12345"),
    )
    assert settings.is_production is True


def test_production_fail_closed_on_wildcard_cors():
    """Production mode rejects wildcard CORS origin."""
    with pytest.raises(ConfigurationError, match="Wildcard CORS origin is prohibited"):
        AppSettings(
            ENVIRONMENT="production",
            ALLOWED_ORIGINS="*",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("prod_webhook_hmac_secret_key_12345"),
        )


def test_production_fail_closed_on_short_jwt_secret():
    """Production mode rejects JWT secret shorter than 32 characters."""
    with pytest.raises(ConfigurationError, match="JWT_SECRET_KEY must be a cryptographically secure key >= 32 characters"):
        AppSettings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("too_short_key"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("prod_webhook_hmac_secret_key_12345"),
        )


def test_production_fail_closed_on_explicit_simulation_gateway():
    """Production mode rejects explicit GATEWAY_MODE=simulation."""
    with pytest.raises(ConfigurationError, match="SimulationGateway is prohibited in production"):
        AppSettings(
            ENVIRONMENT="production",
            GATEWAY_MODE="simulation",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("prod_webhook_hmac_secret_key_12345"),
        )


def test_production_fail_closed_on_live_gateway_missing_credentials():
    """Production mode in razorpay_live mode rejects missing credentials."""
    with pytest.raises(ConfigurationError, match="Live gateway credentials required"):
        AppSettings(
            ENVIRONMENT="production",
            GATEWAY_MODE="razorpay_live",
            DATABASE_URL="postgresql+psycopg://user:pass@db:5432/ray_db",
            JWT_SECRET_KEY=SecretStr("a_very_strong_random_secret_string_32_chars_long"),
            RAZORPAY_WEBHOOK_SECRET=SecretStr("prod_webhook_hmac_secret_key_12345"),
            RAZORPAY_KEY_ID=None,
        )
