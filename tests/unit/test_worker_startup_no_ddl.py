"""Regression Test for Phase 3: No Runtime DDL in Worker Startup.

Verifies:
1. apps/worker/main.py does NOT invoke Base.metadata.create_all().
2. Worker startup validates schema through check_migrations_applied().
3. Worker fails closed with exit code 1 if required migrations are missing.
"""

from __future__ import annotations

import sys
from unittest.mock import patch
import pytest

import apps.worker.main


def test_worker_source_does_not_contain_create_all():
    """Assert that apps/worker/main.py does not invoke create_all."""
    with open(apps.worker.main.__file__, "r", encoding="utf-8") as f:
        content = f.read()
    assert "create_all" not in content, "Found forbidden Base.metadata.create_all() call in worker code!"


def test_worker_fails_closed_when_migrations_missing(monkeypatch):
    """Assert that worker exits with sys.exit(1) if check_migrations_applied returns False."""
    with patch("apps.worker.main.check_migrations_applied", return_value=False):
        with patch.object(sys, "argv", ["worker", "--once"]):
            with pytest.raises(SystemExit) as exc_info:
                apps.worker.main.main()
            assert exc_info.value.code == 1


def test_worker_starts_cleanly_when_migrations_applied(monkeypatch):
    """Assert that worker completes single cycle cleanly when migrations are verified."""
    with patch("apps.worker.main.check_migrations_applied", return_value=True):
        with patch.object(sys, "argv", ["worker", "--once"]):
            # Should run single cycle and exit 0 without calling create_all
            apps.worker.main.main()
