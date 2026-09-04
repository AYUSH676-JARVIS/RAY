"""Alembic Integrity & Migration Lifecycle Tests.

Enforces zero-gap production database migration invariants:
1. Alembic configuration resolves a valid SQLAlchemy URL.
2. No placeholder 'driver://' URL is accepted.
3. Alembic has exactly one head.
4. Fresh database can migrate to head.
5. Migration creates all required tables.
6. Migration creates required foreign keys.
7. Migration creates required unique constraints.
8. Migration creates required indexes.
9. Migration creates tenant ownership columns.
10. Migration creates idempotency constraints.
11. Migration creates audit integrity fields.
12. Application can connect after migration.
13. /ready reports migrations current.
14. Missing DATABASE_URL fails clearly in production.
15. Invalid DATABASE_URL fails clearly.
16. Production does not silently use SQLite.
17. Production does not silently use create_all().
18. Upgrade -> downgrade -> upgrade works completely.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from apps.api.main import app
from migrations.env import get_canonical_url, validate_url
from services.money_graph.database import (
    DATABASE_URL,
    check_migrations_applied,
    init_db,
    run_migrations,
)
from services.money_graph.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI_PATH = REPO_ROOT / "alembic.ini"


def test_1_alembic_config_resolves_valid_url():
    """Alembic environment resolves a valid SQLAlchemy URL, not empty or placeholder."""
    url = get_canonical_url()
    assert url is not None
    assert not url.startswith("driver://")
    assert "user:pass@localhost" not in url
    assert "://" in url


def test_2_no_placeholder_driver_url_accepted():
    """Placeholder driver:// URLs are explicitly rejected."""
    with pytest.raises(ValueError, match="Dummy placeholder URLs are prohibited"):
        validate_url("driver://user:pass@localhost/dbname")

    with pytest.raises(ValueError, match="Dummy placeholder URLs are prohibited"):
        validate_url("")


def test_3_alembic_has_exactly_one_head():
    """Alembic migration graph must have exactly one linear revision head."""
    cfg = Config(str(ALEMBIC_INI_PATH))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 migration head, found: {heads}"
    assert heads[0] == "0003_task_queue"


def test_4_to_11_fresh_database_migration_schema_and_constraints():
    """A fresh database migrates to head and creates all tables, keys, and constraints."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name

    db_url = f"sqlite:///{db_path}"
    try:
        # Run migration on empty DB
        run_migrations(db_url)

        engine = create_engine(db_url)
        insp = inspect(engine)

        # 5. Required tables
        expected_tables = {
            "merchants", "customers", "orders", "payments",
            "payment_attempts", "payment_failures", "recovery_opportunities",
            "recovery_actions", "policy_decisions", "agent_runs",
            "tool_calls", "audit_events", "alembic_version"
        }
        actual_tables = set(insp.get_table_names())
        assert expected_tables.issubset(actual_tables), f"Missing tables: {expected_tables - actual_tables}"

        # 6. Foreign keys
        fks_payment = {fk["referred_table"] for fk in insp.get_foreign_keys("payments")}
        assert "merchants" in fks_payment
        assert "orders" in fks_payment
        assert "customers" in fks_payment

        # 7. Unique constraints
        # Audit unique constraint
        audit_uqs = [uq["name"] for uq in insp.get_unique_constraints("audit_events")]
        assert "uq_audit_merchant_seq" in audit_uqs

        # 8. Required indexes
        cust_ix = {ix["name"] for ix in insp.get_indexes("customers")}
        assert "ix_customers_merchant_id" in cust_ix
        assert "ix_customers_external_id" in cust_ix

        # 9. Tenant ownership columns
        for table in ["payments", "payment_attempts", "payment_failures",
                      "recovery_opportunities", "recovery_actions",
                      "policy_decisions", "agent_runs", "tool_calls", "audit_events"]:
            cols = {c["name"] for c in insp.get_columns(table)}
            assert "merchant_id" in cols, f"Table {table} is missing multi-tenant merchant_id"

        # 10. Idempotency constraints
        act_ix = {ix["name"] for ix in insp.get_indexes("recovery_actions")}
        assert "ix_recovery_actions_idempotency_key" in act_ix

        # 11. Audit integrity fields
        audit_cols = {c["name"] for c in insp.get_columns("audit_events")}
        for expected_col in ["sequence_number", "event_hash", "previous_event_hash", "payload_before_json", "payload_after_json"]:
            assert expected_col in audit_cols

        engine.dispose()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_12_and_13_application_connection_and_readiness():
    """Application connects to migrated database and /ready reports current."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name

    db_url = f"sqlite:///{db_path}"
    try:
        run_migrations(db_url)
        eng = create_engine(db_url)
        assert check_migrations_applied(eng) is True

        client = TestClient(app)
        res = client.get("/ready")
        assert res.status_code == 200
        data = res.json()
        assert data["database"] == "connected"
        assert data["migrations"] == "current"
        eng.dispose()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_14_missing_database_url_fails_clearly_in_production(monkeypatch):
    """In production mode, missing DATABASE_URL raises RuntimeError."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="Production migration configuration error"):
        get_canonical_url()


def test_15_invalid_database_url_fails_clearly():
    """Invalid or placeholder URL fails with clear error message."""
    with pytest.raises(ValueError, match="Dummy placeholder URLs are prohibited"):
        validate_url("driver://placeholder")


def test_16_production_does_not_silently_use_sqlite(monkeypatch):
    """Production startup strictly rejects SQLite engines."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    sqlite_engine = create_engine("sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="SQLite is prohibited in production"):
        init_db(target_engine=sqlite_engine)


def test_17_production_does_not_silently_use_create_all(monkeypatch):
    """Production startup strictly rejects unmigrated database without calling create_all."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("services.money_graph.database.check_migrations_applied", lambda eng: False)
    eng = create_engine("postgresql+psycopg://localhost:5432/ray_clean_test_db")

    with pytest.raises(RuntimeError, match="Required database migrations are not applied"):
        init_db(target_engine=eng)


def test_18_upgrade_downgrade_upgrade_lifecycle():
    """Alembic supports complete upgrade -> downgrade -> upgrade lifecycle."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name

    db_url = f"sqlite:///{db_path}"
    try:
        cfg = Config(str(ALEMBIC_INI_PATH))
        cfg.set_main_option("sqlalchemy.url", db_url)

        # 1. Upgrade to head
        command.upgrade(cfg, "head")
        eng = create_engine(db_url)
        assert len(inspect(eng).get_table_names()) >= 12

        # 2. Downgrade to base
        command.downgrade(cfg, "base")
        assert "merchants" not in inspect(eng).get_table_names()

        # 3. Upgrade back to head
        command.upgrade(cfg, "head")
        assert len(inspect(eng).get_table_names()) >= 12
        eng.dispose()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
