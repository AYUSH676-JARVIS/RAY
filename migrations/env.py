"""Alembic migration environment for RAY Merchant Money Intelligence Engine.

Invariants:
1. Dynamically resolves canonical DATABASE_URL from environment or database configuration.
2. Rejects any dummy or placeholder 'driver://' URLs.
3. Fails closed in production if DATABASE_URL is missing or invalid.
4. Supports both online and offline migration modes.
5. All 12 models are registered via Base.metadata.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.config import Config
from sqlalchemy import engine_from_config, pool

# Ensure repository root is in python path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.money_graph.models import Base

# Safe Config object initialization (supports both CLI runner and direct import)
if hasattr(context, "config") and context.config is not None:
    config = context.config
else:
    ini_path = REPO_ROOT / "alembic.ini"
    config = Config(str(ini_path)) if ini_path.exists() else Config()

# Setup logging if running within Alembic CLI with valid ini file
if config and config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

# Model metadata for autogenerate and migrations
target_metadata = Base.metadata


def get_canonical_url() -> str:
    """Resolve and validate the canonical database URL for migrations.
    
    Order of precedence:
    1. Programmatic CLI option passed to Alembic Config (if non-placeholder)
    2. DATABASE_URL environment variable
    3. Production check: Must fail closed if unset
    4. Development default from services.money_graph.database
    """
    # 1. Programmatic override (e.g. from pytest fixtures)
    cli_url = config.get_main_option("sqlalchemy.url") if config else None
    if cli_url and not cli_url.startswith("driver://") and "user:pass@localhost" not in cli_url:
        return cli_url.strip()

    # 2. Environment variable
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        clean = env_url.strip()
        if clean and not clean.startswith("driver://") and "user:pass@localhost" not in clean:
            return clean

    # 3. In production, missing DATABASE_URL must fail closed!
    is_prod = os.getenv("ENVIRONMENT", "development").lower() == "production"
    if is_prod:
        raise RuntimeError(
            "Production migration configuration error: DATABASE_URL environment variable is required but not set."
        )

    # 4. Canonical development default
    from services.money_graph.database import DEFAULT_URL
    return DEFAULT_URL


def validate_url(url: str) -> str:
    """Ensure URL is valid and supported by SQLAlchemy."""
    if not url or url.startswith("driver://") or "user:pass@localhost" in url:
        raise ValueError(
            f"Invalid database URL '{url}': Dummy placeholder URLs are prohibited. Set a valid DATABASE_URL."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = validate_url(get_canonical_url())
    if config:
        config.set_main_option("sqlalchemy.url", url)

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    url = validate_url(get_canonical_url())
    if config:
        config.set_main_option("sqlalchemy.url", url)

    configuration = config.get_section(config.config_ini_section, {}) if config else {}
    configuration["sqlalchemy.url"] = url

    is_sqlite = url.startswith("sqlite")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


# Only execute migration dispatch if executed inside Alembic runner
if hasattr(context, "is_offline_mode") and hasattr(context, "config"):
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
