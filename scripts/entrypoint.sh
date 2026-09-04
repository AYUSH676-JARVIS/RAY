#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "RAY Production Startup & Migration Runner"
echo "=================================================="

# 1. Wait for database readiness
echo "[1/3] Checking PostgreSQL connectivity..."
python3 -c "
import sys, time
from sqlalchemy import create_engine, text
from services.common.config import get_settings

engine = create_engine(get_settings().DATABASE_URL)
for i in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
            print('PostgreSQL is ready.')
            sys.exit(0)
    except Exception as e:
        time.sleep(1)
sys.exit(1)
" || { echo "[ERROR] PostgreSQL not reachable"; exit 1; }

# 2. Run Alembic migrations forward
echo "[2/3] Executing database migrations (alembic upgrade head)..."
alembic upgrade head
echo "[✓] Database schema verified at head."

# 3. Exec passed command
echo "[3/3] Starting service process: $@"
exec "$@"
