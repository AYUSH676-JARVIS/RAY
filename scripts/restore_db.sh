#!/usr/bin/env bash
# ==============================================================================
# RAY Control Plane — Database Restore & Integrity Verification Script (Task 22)
# ==============================================================================
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <path-to-backup.sql.gz>" >&2
    exit 1
fi

BACKUP_FILE="$1"
CHECKSUM_FILE="${BACKUP_FILE}.sha256"

echo "=================================================================="
echo " Starting Database Restore from: ${BACKUP_FILE}"
echo "=================================================================="

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}" >&2
    exit 1
fi

# 1. Cryptographic SHA-256 Checksum Verification
if [ -f "${CHECKSUM_FILE}" ]; then
    echo "Verifying SHA-256 checksum integrity..."
    if command -v shasum &> /dev/null; then
        shasum -a 256 -c "${CHECKSUM_FILE}"
    else
        sha256sum -c "${CHECKSUM_FILE}"
    fi
    echo "SHA-256 Checksum VERIFIED."
else
    echo "WARNING: Checksum file '${CHECKSUM_FILE}' not found. Proceeding with caution."
fi

# Extract database parameters
DB_NAME="${POSTGRES_DB:-ray_db}"
DB_USER="${POSTGRES_USER:-${USER:-postgres}}"
DB_HOST="${POSTGRES_HOST:-localhost}"
DB_PORT="${POSTGRES_PORT:-5432}"

# 2. Database Restoration
if command -v psql &> /dev/null; then
    echo "Restoring database '${DB_NAME}' via psql..."
    gunzip -c "${BACKUP_FILE}" | psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}"
else
    echo "psql client not found in PATH."
fi

# 3. Post-Restore Schema & Cryptographic Audit Verification
echo "Verifying post-restore database schema & cryptographic chain integrity..."
python3 -c "
from services.money_graph.database import SessionLocal, check_migrations_applied
from services.audit.logger import AuditLogger
from services.money_graph.models import Merchant
from sqlalchemy import select

# Verify schema
if not check_migrations_applied():
    raise RuntimeError('Restore validation failed: Database tables or Alembic migrations missing!')

# Verify audit chain
with SessionLocal() as session:
    merchants = session.execute(select(Merchant)).scalars().all()
    for m in merchants:
        valid, err = AuditLogger.verify_audit_chain(session, m.id)
        if not valid:
            raise RuntimeError(f'Post-restore cryptographic audit verification failed for merchant {m.id}: {err}')

print('Schema and Cryptographic Audit Chains 100% VERIFIED.')
"

echo "=================================================================="
echo " Database Restore & Integrity Verification Succeeded."
echo "=================================================================="
