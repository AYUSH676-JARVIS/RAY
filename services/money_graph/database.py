"""Database configuration, session management, and migration lifecycle."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Generator
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from services.money_graph.models import Base

load_dotenv()

logger = logging.getLogger("ray.database")

# Build database URL with sensible defaults
DEFAULT_URL = f"postgresql+psycopg://{os.getenv('USER', 'postgres')}@localhost:5432/ray_db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()

# Configure engine
engine_kwargs = {"echo": False, "future": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs["pool_pre_ping"] = True
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
    engine_kwargs["connect_args"] = {"connect_timeout": 10}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_migrations_applied(target_engine=None) -> bool:
    """Verify that Alembic migrations have been applied and tables exist."""
    eng = target_engine or engine
    inspector = inspect(eng)
    existing_tables = set(inspector.get_table_names())
    required_tables = {"merchants", "customers", "orders", "payments", "alembic_version"}
    return required_tables.issubset(existing_tables)


def run_migrations(target_url: str | None = None) -> None:
    """Apply all pending Alembic migrations to head revision."""
    from alembic import command
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parent.parent.parent
    ini_path = repo_root / "alembic.ini"
    
    cfg = Config(str(ini_path))
    effective_url = target_url or DATABASE_URL
    cfg.set_main_option("sqlalchemy.url", effective_url)
    
    command.upgrade(cfg, "head")
    logger.info("Database migrations upgraded to head successfully.")


def init_db(target_engine=None):
    """Initialize database schema adhering to production environment rules.
    
    In production mode:
      Verifies that migrations have been applied; NEVER calls Base.metadata.create_all().
    In development/testing mode:
      Applies Alembic migrations, falling back to metadata creation for in-memory SQLite.
    """
    eng = target_engine or engine
    current_env = os.getenv("ENVIRONMENT", ENVIRONMENT).lower()
    
    if current_env == "production":
        if str(eng.url).startswith("sqlite"):
            raise RuntimeError("Production safety violation: SQLite is prohibited in production environment.")
        if not check_migrations_applied(eng):
            raise RuntimeError(
                "Production startup failed: Required database migrations are not applied. "
                "Run 'alembic upgrade head' before starting the production service."
            )
        logger.info("Production database schema verified via migrations.")
        return

    # Development / Testing: in-memory SQLite is for unit test fixtures only
    if str(eng.url).startswith("sqlite:///:memory:"):
        Base.metadata.create_all(bind=eng)
        return

    # All persistent environments must apply migrations
    if not check_migrations_applied(eng):
        run_migrations(str(eng.url))


def drop_db(target_engine=None):
    """Drop all tables in the configured database."""
    eng = target_engine or engine
    Base.metadata.drop_all(bind=eng)


def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session in FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
