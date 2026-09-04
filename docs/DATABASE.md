# RAY — Database & Migration Architecture

## 1. Canonical Database URL Contract

RAY strictly adheres to a single, canonical database connection specification.

### URL Specification
```
postgresql+psycopg://<username>:<password>@<host>:<port>/<database_name>
```

- **Driver:** `psycopg` (Version 3.2+) via the SQLAlchemy dialect `postgresql+psycopg`.
- **Precedence Order:**
  1. `DATABASE_URL` environment variable.
  2. In development/testing: Defaults to local user PostgreSQL socket `postgresql+psycopg://<USER>@localhost:5432/ray_db`.
  3. In production: **Fails closed.** If `DATABASE_URL` is unset when `ENVIRONMENT=production`, the application immediately raises `RuntimeError` without attempting connections or falling back to defaults.
- **Placeholder Prohibition:** Static placeholders (such as `driver://user:pass@localhost/dbname`) are prohibited and rejected by validation logic.

---

## 2. Schema Management & Production Invariants

1. **Alembic as Sole Authority:** In production, database schemas are created, altered, and versioned **exclusively** via Alembic migration scripts.
2. **`create_all()` Prohibited in Production:** Calling `Base.metadata.create_all()` in production environments is forbidden. The application startup probe checks migration status and aborts if migrations are unapplied.
3. **Multi-Tenant Scoping:** All 12 tables contain an indexed `merchant_id` foreign key referencing `merchants.id` with `ON DELETE CASCADE`.
4. **Exact Decimal Precision:** Monetary amounts in `orders`, `payments`, and `recovery_opportunities` use `sa.Numeric(12, 2)`. Floating-point money arithmetic is prohibited.
5. **Canonical Risk Score Contract:** All risk scores in `customers` and `policy_decisions` are normalized strictly to $[0.0, 1.0]$ and enforced by database check constraints (`chk_customer_risk_score`, `chk_policy_decision_risk_score`).

---

## 3. Migration CLI Commands & Operational Runbook

All commands must be run from the repository root:

### Check Current Migration Revision
```bash
alembic current
```
*Expected output:* `0001_initial_schema (head)`

### Check Migration Heads
```bash
alembic heads
```
*Expected output:* Exactly one head: `0001_initial_schema (head)`

### View Migration History
```bash
alembic history
```
*Expected output:* Linear history from `<base> -> 0001_initial_schema (head)`

### Apply Pending Migrations to Head
```bash
alembic upgrade head
```

### Rollback / Downgrade (Disaster Recovery)
```bash
alembic downgrade -1        # Roll back one revision
alembic downgrade base      # Roll back all revisions
```
*Note:* Full downgrade is verified and supported for `0001_initial_schema`.

---

## 4. Application Health & Readiness Verification

After applying migrations, verify operational readiness via HTTP:

```bash
# Process Liveness
curl -s http://127.0.0.1:8000/health
# {"status":"healthy","version":"1.0.0","database":"connected","environment":"production"}

# Deep Readiness (Database + Migrations)
curl -s http://127.0.0.1:8000/ready
# {"status":"ready","database":"connected","migrations":"current","version":"1.0.0"}
```

If migrations are unapplied or behind in production, `/ready` responds with `HTTP 503 Service Unavailable`, preventing Kubernetes or load balancers from routing merchant traffic to outdated nodes.

---

## 5. Production Deployment Procedure

1. **Pre-Deployment Gate:**
   Run Alembic in migration job container or CI/CD runner:
   ```bash
   alembic upgrade head
   ```
2. **Verification Gate:**
   Verify the migration succeeded:
   ```bash
   alembic current
   ```
3. **Application Rollout:**
   Start API containers with `ENVIRONMENT=production` and `DATABASE_URL` injected securely via secret manager.
4. **Traffic Gate:**
   Ingress monitors `/ready` until `migrations: current` is returned before marking the pod healthy.
