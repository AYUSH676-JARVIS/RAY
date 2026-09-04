# RAY Control Plane — Disaster Recovery & Business Continuity Runbook

> **System Recovery Tier:** Tier-1 Financial Mission Critical  
> **Target Compliance:** PCI-DSS Level 1 / SOC 2 Type II / ISO 27001

---

## 1. Executive Summary & Recovery Objectives

| Metric | Target Objective | Strategy & Mechanism |
| :--- | :--- | :--- |
| **RPO (Recovery Point Objective)** | **< 5 Minutes** | Continuous PostgreSQL Write-Ahead Log (WAL) archiving to cloud object storage with 60-second flushes. |
| **RTO (Recovery Time Objective)** | **< 15 Minutes** | Automated containerized image bootstrapping, schema auto-migration, and verified dump hydration. |
| **Data Integrity Assurance** | **Zero Loss / 100% Cryptographic Continuity** | Unbroken SHA-256 hash chains across `audit_events` and transactional outbox verified post-restore. |

---

## 2. Backup Architecture & Retention Lifecycle

```
[ PostgreSQL Master ]
       │
       ├─► (Every 6 Hours) ──► pg_dump (gzip-9) ──► SHA-256 Checksum ──► Encrypted Hot Storage
       │
       └─► (Continuous)    ──► WAL Streaming   ──► Cloud Object Storage (PITR)
```

### Retention Policy
- **Hourly Backups**: Retained for 48 hours.
- **Daily Snapshots**: Retained for 30 days.
- **Weekly Archives**: Retained for 52 weeks in immutable WORM (Write Once, Read Many) cold storage.
- **Monthly Audited Snapshots**: Retained for 7 years for financial compliance.

---

## 3. Automated Operational Runbook

### Taking a Database Backup
Run the automated backup script:
```bash
./scripts/backup_db.sh
```
Produces:
- `backups/ray_db_<TIMESTAMP>.sql.gz`
- `backups/ray_db_<TIMESTAMP>.sql.gz.sha256`

### Restoring from a Backup
To restore and cryptographically verify a snapshot:
```bash
./scripts/restore_db.sh backups/ray_db_<TIMESTAMP>.sql.gz
```
The automated script executes:
1. Verifies SHA-256 checksum against `.sha256` manifest.
2. Hydrates PostgreSQL relations.
3. Validates Alembic migration level against model declarations.
4. Executes `AuditLogger.verify_audit_chain()` across every merchant tenant to guarantee zero cryptographic corruption.

---

## 4. Disaster Scenarios & Incident Runbooks

### Scenario A: Primary Database Node Unreachable
1. **Health Detection**: Orchestrator (Docker/Kubernetes) detects `/health` failures.
2. **Promote Standby**: If multi-region replica active, execute `pg_ctl promote`.
3. **Re-route DNS**: Update database connection pool URI to secondary node.
4. **Run Verification**:
   ```bash
   python -c "from services.money_graph.database import check_migrations_applied; assert check_migrations_applied()"
   ```

### Scenario B: Cryptographic Audit Tamper Alert
1. **Trigger Alert**: `POST /api/v1/audit/verify` returns status `TAMPER_ALERT`.
2. **Isolate Node**: Automatically engage administrative kill switch (`POST /api/v1/admin/kill-switch`) to freeze all automated money recovery.
3. **Identify Break Point**: Check the specific event where `event_hash != computed_hash`.
4. **Restore to Pre-Tamper State**: Hydrate last verified snapshot and replay transactions from authenticated outbox.

### Scenario C: Emergency Kill Switch Manual SQL Activation
If the API control plane is partitioned or unresponsive, engage the kill switch directly in PostgreSQL:
```sql
-- Engage emergency kill switch cluster-wide for all merchants:
UPDATE merchants 
SET kill_switch_engaged = true,
    kill_switch_reason = 'EMERGENCY_MANUAL_DB_OVERRIDE',
    kill_switch_engaged_at = NOW();

-- Verify state:
SELECT id, name, kill_switch_engaged, kill_switch_reason FROM merchants;
```
All running workers immediately detect `kill_switch_engaged = true` inside their transaction loop and halt money movement.

### Scenario D: Gateway Outage & UNKNOWN Queue Recovery
When an upstream acquirer experiences an extended outage (e.g. 504 Gateway Timeouts):
1. Transactions transition safely to `UNKNOWN` (preventing double charges).
2. Once acquirer health is restored, run the Authoritative Reconciliation Job:
   ```bash
   python -c "
   from services.action_layer.reconciliation import reconcile_ambiguous_payments
   from services.money_graph.database import get_db_session
   with get_db_session() as session:
       reconcile_ambiguous_payments(session)
   "
   ```
3. Verified captured payments transition to `SETTLED`; confirmed failures are scheduled for next-window retry.

### Scenario E: Outbox Backlog & Worker Recovery
If the background worker container crashes or restarts:
1. Unprocessed outbox items remain in `outbox_events` table (`status = PENDING`).
2. New worker container starts and selects events with `SELECT ... FOR UPDATE SKIP LOCKED`.
3. Processing resumes with at-least-once delivery; idempotency keys prevent duplicate gateway calls.
