#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "RAY Database Migration Rollback Utility"
echo "=================================================="

TARGET="${1:--1}"

echo "[WARNING] Rolling back database migrations to target: $TARGET"
read -p "Are you sure you want to proceed with database rollback? (y/N): " -r
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Rollback cancelled by operator."
    exit 0
fi

echo "Executing: alembic downgrade $TARGET"
alembic downgrade "$TARGET"

echo "[✓] Database downgrade complete. Current revision:"
alembic current
