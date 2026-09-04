"""Centralized Production Environment Settings & Secrets Management.

Enforces:
1. Strict Pydantic type validation for all configuration variables.
2. Complete secret masking in logging and string representations.
3. Fail-closed production startup verification:
   In ENVIRONMENT=production, missing or placeholder secrets raise ConfigurationError immediately.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when critical configuration or production security prerequisites are violated."""
    pass


class AppSettings(BaseSettings):
    """Central configuration for RAY Merchant Intelligence Engine."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Runtime
    ENVIRONMENT: str = Field(default="development", description="Runtime environment: development, test, staging, production")
    APP_NAME: str = Field(default="RAY Merchant Money Intelligence Engine", description="Application name")
    VERSION: str = Field(default="1.0.0", description="SemVer release version")
    HOST: str = Field(default="127.0.0.1", description="API bind host")
    API_PORT: int = Field(default=8000, description="API port")
    PORT: int = Field(default=8000, description="Port")

    # Database Configuration
    DATABASE_URL: str = Field(
        default_factory=lambda: f"postgresql+psycopg://{os.getenv('USER', 'postgres')}@localhost:5432/ray_db",
        description="Database connection URI",
    )
    DB_POOL_SIZE: int = Field(default=20, ge=5, le=100, description="Database connection pool size")
    DB_MAX_OVERFLOW: int = Field(default=30, ge=0, le=100, description="Maximum overflow connections")
    DB_POOL_TIMEOUT: int = Field(default=30, ge=5, le=120, description="Connection acquisition timeout in seconds")
    DB_POOL_RECYCLE: int = Field(default=1800, ge=60, description="Connection recycle interval in seconds")

    # Security & CORS
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated CORS origins",
    )
    JWT_SECRET_KEY: SecretStr = Field(
        default=SecretStr("ray_dev_jwt_secret_key_minimum_32_bytes_long"),
        description="HMAC secret for JWT verification",
    )
    JWT_ALGORITHM: str = Field(default="HS256", description="JWT signing algorithm")
    STAGE2_APPROVAL_SECRET: Optional[SecretStr] = Field(
        default=None,
        description="Cryptographic dual-key approval secret for Stage 2 live money movement",
    )

    # Gateway Credentials & Routing
    GATEWAY_MODE: Optional[str] = Field(
        default=None,
        description="Gateway execution routing mode: simulation, razorpay_test, or razorpay_live",
    )
    RAZORPAY_KEY_ID: Optional[SecretStr] = Field(default=None, description="Razorpay live or test API key ID")
    RAZORPAY_KEY_SECRET: Optional[SecretStr] = Field(default=None, description="Razorpay live or test API secret")
    RAZORPAY_WEBHOOK_SECRET: Optional[SecretStr] = Field(default=None, description="HMAC secret for verifying Razorpay webhooks")
    RAZORPAY_BASE_URL: str = Field(default="https://api.razorpay.com/v1", description="Razorpay API base endpoint")
    RAZORPAY_TEST_MODE: bool = Field(default=True, description="Enforces Razorpay test-mode isolation")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="before")
    @classmethod
    def resolve_canonical_env_aliases(cls, data: Any) -> Any:
        """Resolve legacy environment variable aliases into canonical names with deprecation logging."""
        if isinstance(data, dict):
            # 1. ENV -> ENVIRONMENT
            if "ENVIRONMENT" not in data and "ENV" in os.environ:
                data["ENVIRONMENT"] = os.environ["ENV"]
            # 2. JWT_SECRET -> JWT_SECRET_KEY
            if "JWT_SECRET_KEY" not in data and "JWT_SECRET" in os.environ:
                data["JWT_SECRET_KEY"] = os.environ["JWT_SECRET"]
            # 3. WEBHOOK_SIGNING_SECRET -> RAZORPAY_WEBHOOK_SECRET
            if "RAZORPAY_WEBHOOK_SECRET" not in data and "WEBHOOK_SIGNING_SECRET" in os.environ:
                data["RAZORPAY_WEBHOOK_SECRET"] = os.environ["WEBHOOK_SIGNING_SECRET"]
        return data

    @model_validator(mode="after")
    def validate_production_fail_closed(self) -> AppSettings:
        """Enforce fail-closed security invariants in production environments."""
        if not self.is_production:
            return self

        errors: List[str] = []
        PLACEHOLDER_SUBSTRINGS = {"change_me", "example", "test", "development", "default", "insecure"}

        # Invariant 1: SQLite is strictly forbidden in production
        if self.DATABASE_URL.startswith("sqlite"):
            errors.append("Production violation: SQLite database is prohibited in production.")

        # Invariant 2: Wildcard CORS with credentials is forbidden
        if "*" in self.origins_list:
            errors.append("Production violation: Wildcard CORS origin is prohibited when credentials are enabled.")

        # Invariant 3: Development / placeholder JWT secret cannot be used in production
        dev_jwt = "ray_dev_jwt_secret_key_minimum_32_bytes_long"
        jwt_val = self.JWT_SECRET_KEY.get_secret_value()
        if jwt_val == dev_jwt or len(jwt_val) < 32:
            errors.append("Production violation: JWT_SECRET_KEY must be a cryptographically secure key >= 32 characters.")
        elif any(ph in jwt_val.lower() for ph in PLACEHOLDER_SUBSTRINGS):
            errors.append("Production violation: JWT_SECRET_KEY contains insecure placeholder value. Explicit production secret required.")

        # Invariant 4: Razorpay webhook secret is mandatory and cannot be a placeholder
        if not self.RAZORPAY_WEBHOOK_SECRET or not self.RAZORPAY_WEBHOOK_SECRET.get_secret_value():
            errors.append("Production violation: RAZORPAY_WEBHOOK_SECRET is mandatory for webhook cryptographic verification.")
        else:
            wh_val = self.RAZORPAY_WEBHOOK_SECRET.get_secret_value()
            if any(ph in wh_val.lower() for ph in PLACEHOLDER_SUBSTRINGS):
                errors.append("Production violation: RAZORPAY_WEBHOOK_SECRET contains insecure placeholder value.")

        # Invariant 5: Database URL cannot contain default insecure credentials
        if any(ph in self.DATABASE_URL.lower() for ph in ["change_me", "postgres:postgres@"]):
            errors.append("Production violation: DATABASE_URL contains default or placeholder database credentials.")

        # Invariant 6: Stage 2 approval secret cannot be a placeholder if configured
        if self.STAGE2_APPROVAL_SECRET and any(ph in self.STAGE2_APPROVAL_SECRET.get_secret_value().lower() for ph in PLACEHOLDER_SUBSTRINGS):
            errors.append("Production violation: STAGE2_APPROVAL_SECRET contains insecure placeholder value.")

        # Invariant 7: Test Razorpay credentials cannot be loaded in production
        key_id_val = self.RAZORPAY_KEY_ID.get_secret_value() if self.RAZORPAY_KEY_ID else None
        if key_id_val and key_id_val.startswith("rzp_test_"):
            errors.append("Production violation: Test Razorpay credentials (rzp_test_*) cannot be loaded in PRODUCTION environment. Fail closed.")

        # Invariant 8: SimulationGateway is strictly forbidden in production
        if self.GATEWAY_MODE and self.GATEWAY_MODE.lower().strip() == "simulation":
            errors.append("Production violation: SimulationGateway is prohibited in production. Configure razorpay_live or razorpay_test.")

        # Invariant 9: Live mode requires real gateway credentials
        if self.GATEWAY_MODE and self.GATEWAY_MODE.lower().strip() == "razorpay_live":
            key_id = self.RAZORPAY_KEY_ID.get_secret_value() if self.RAZORPAY_KEY_ID else ""
            key_sec = self.RAZORPAY_KEY_SECRET.get_secret_value() if self.RAZORPAY_KEY_SECRET else ""
            if not key_id or not key_sec or key_id == "rzp_test_placeholder":
                errors.append("Production violation: Live gateway credentials required for razorpay_live mode.")

        # Invariant 10: Invalid gateway mode
        if self.GATEWAY_MODE:
            valid_modes = {"simulation", "razorpay_test", "razorpay_live"}
            if self.GATEWAY_MODE.lower().strip() not in valid_modes:
                errors.append(f"Production violation: Invalid GATEWAY_MODE '{self.GATEWAY_MODE}'. Allowed: {valid_modes}")

        if errors:
            raise ConfigurationError(
                "Fail-Closed Startup Aborted due to Production Security Violations:\n - "
                + "\n - ".join(errors)
            )

        return self
    @model_validator(mode="after")
    def validate_razorpay_credentials_isolation(self) -> AppSettings:
        """Prevent accidental live credential usage in test mode, or test credential usage in production."""
        key_id_val = self.RAZORPAY_KEY_ID.get_secret_value() if self.RAZORPAY_KEY_ID else None
        if key_id_val:
            # Check 1: In test mode, live keys (rzp_live_) are strictly forbidden
            if self.RAZORPAY_TEST_MODE and key_id_val.startswith("rzp_live_"):
                raise ConfigurationError(
                    "Configuration violation: Live Razorpay credentials (rzp_live_*) cannot be loaded when RAZORPAY_TEST_MODE=true. Fail closed."
                )
            # Check 2: In production environment, test keys (rzp_test_) are strictly forbidden
            if self.is_production and key_id_val.startswith("rzp_test_"):
                raise ConfigurationError(
                    "Production violation: Test Razorpay credentials (rzp_test_*) cannot be loaded in PRODUCTION environment. Fail closed."
                )
        return self

    def get_redacted_dict(self) -> Dict[str, Any]:
        """Return safe configuration dictionary with all secret values masked."""
        data = self.model_dump()
        for k, v in data.items():
            if isinstance(v, SecretStr):
                data[k] = "********"
            elif isinstance(v, str) and ("secret" in k.lower() or "key" in k.lower()):
                data[k] = "********" if v else None
        return data


# Global cached settings instance
_settings: Optional[AppSettings] = None


def get_settings() -> AppSettings:
    """Return singleton AppSettings instance."""
    global _settings
    if _settings is None:
        _settings = AppSettings()
    return _settings
