# RAY Control Plane — Security & Hardening Audit

**Author**: Independent Principal Security Engineer & Fintech Platform Architect  
**Audit Standard**: Stripe / Razorpay / Adyen / Tier-1 Production Standards  
**Scope**: Full Codebase Security & Reliability Forensic Audit  
**Status**: Pre-Remediation Baseline  

---

## Executive Summary

A comprehensive, zero-trust forensic audit of the entire RAY Merchant Revenue Recovery Control Plane was performed across:
- `apps/api` (FastAPI endpoints, middleware, routing, dependencies)
- `apps/worker` (Background processing daemon, reconciliation, outbox)
- `services/auth` (Authentication, tenant binding, RBAC)
- `services/action_layer` (Execution primitives, gateway adapters, idempotency, Stage 2 safety manager)
- `services/worker` (Reconciliation worker, outbox dispatcher, retry scheduler)
- `services/orchestrator` (Canonical decision workflow loop)
- `services/webhook` (Inbound payload processing, HMAC verification, replay defense)
- `services/config` (Settings, environment management, secrets provider)

### Invariant Under Review:
> **"AI RECOMMENDS. DETERMINISTIC POLICY AUTHORIZES. DETERMINISTIC ACTION LAYER EXECUTES. OUTCOME VERIFICATION CONFIRMS REALITY. AUDIT RECORDS EVERYTHING."**

---

## Finding Register

### Finding 1 [P0 — Financial / Security Vulnerability]
- **File**: `services/auth/service.py`
- **Line / Function**: Lines 49–87 (`get_current_principal`)
- **Vulnerability**: Test Token Tenant Hijack & Production Auth Bypass
- **Attack / Failure Scenario**: An unauthorized attacker in a production environment sends `Authorization: Bearer ray_test_operator` or `Bearer test_merchant_admin`. Because `get_current_principal` does not check `settings.is_production`, it accepts the token. Furthermore, when no merchant slug is specified, line 82 executes:
  ```python
  m = db.query(Merchant).first()
  if m: target_merchant_id = m.id
  ```
  The caller is assigned `Role.OPERATOR` or `Role.MERCHANT_ADMIN` and bound to the first merchant record in the database.
- **Impact**: Full authentication bypass, horizontal privilege escalation, unauthorized cross-tenant data access (BOLA/IDOR) to customer records, payment histories, and recovery opportunities.
- **Root Cause**: Development test-credential parsing code was deployed without environment guards (`is_production`) and included an implicit fallback to the first database entity.
- **Recommended Fix**:
  1. If `get_settings().is_production`, immediately raise `HTTP 401 Unauthorized` for any token starting with `ray_test_` or `test_`.
  2. Completely eliminate `db.query(Merchant).first()`. Tenant binding must be explicit from the token. If tenant cannot be explicitly resolved, fail closed with `HTTP 401`.
- **Test Required**: Negative tests asserting that `ray_test_*` and `test_*` tokens produce HTTP 401 when `ENVIRONMENT=production`, and that omitting tenant credentials produces HTTP 401.
- **Residual Risk**: Low once strictly gated behind `settings.is_production`.

---

### Finding 2 [P0 — Financial / Security Vulnerability]
- **File**: `services/worker/reconciliation_worker.py`
- **Line / Function**: Lines 45–50 (`reconcile_pending_unknowns`)
- **Vulnerability**: Blind Unconditional Reconciliation to SUCCESS for UNKNOWN Payments
- **Attack / Failure Scenario**: When a payment attempt times out or encounters network ambiguity, it enters the `UNKNOWN` state. The background reconciliation worker runs periodically. Instead of querying the payment gateway's status API (`gateway_adapter.query_status`), line 45 executes:
  ```python
  target_status = PaymentStatus.SUCCESS
  prev_status = p.status
  validate_payment_transition(p.status, target_status, evidence_id=evidence_id)
  p.status = target_status.value
  ```
  Every payment trapped in `UNKNOWN` is automatically marked `SUCCESS`. If the acquiring bank actually rejected the payment or the user cancelled it, RAY registers a phantom recovery.
- **Impact**: Severe financial ledger corruption, phantom revenue reporting, incorrect merchant settlement claims, violation of the Core Financial Invariant (*"Outcome verification confirms reality"*).
- **Root Cause**: Simulation stub left in the reconciliation loop instead of querying the gateway adapter.
- **Recommended Fix**:
  1. Reconciliation worker must query `self.gateway_adapter.query_status(p.attempts[-1].gateway_transaction_id)`.
  2. Transition to `SUCCESS` only if the authoritative gateway response proves payment is `CAPTURED` or `SETTLED`.
  3. Validate amount, currency, and merchant ownership before transition.
  4. If gateway returns `FAILED` or declined, transition to `FAILED`.
  5. If gateway cannot determine state or times out, keep status as `UNKNOWN`.
- **Test Required**: Unit tests verifying:
  - `UNKNOWN` + gateway confirmation $\to$ `SUCCESS`.
  - `UNKNOWN` + gateway decline $\to$ `FAILED`.
  - `UNKNOWN` + gateway timeout / missing record $\to$ remains `UNKNOWN`.
  - `UNKNOWN` + amount mismatch $\to$ reject with alert.
- **Residual Risk**: Zero once gated by authoritative gateway inquiry.

---

### Finding 3 [P1 — Production Reliability / Concurrency Issue]
- **File**: `apps/worker/main.py`
- **Line / Function**: Line 69 (`main`)
- **Vulnerability**: Runtime Database DDL in Production Worker Container
- **Attack / Failure Scenario**: Worker startup executes:
  ```python
  Base.metadata.create_all(bind=engine)
  ```
  In a multi-container Kubernetes or Docker Swarm environment, multiple worker replicas starting concurrently attempt to create database tables simultaneously, risking lock collisions, catalog deadlocks, or overriding Alembic migration version state. Furthermore, in hardened production setups, worker database roles do not have `CREATE TABLE` DDL privileges.
- **Impact**: Worker container crash loops, schema migration drift, potential catalog corruption.
- **Root Cause**: Convenience developer script pattern carried over to production worker entrypoint.
- **Recommended Fix**: Remove `Base.metadata.create_all(bind=engine)`. Schema changes must be exclusively applied via `alembic upgrade head`. Worker startup should verify that migrations are applied via `check_migrations_applied()`.
- **Test Required**: Automated test asserting worker initialization runs cleanly without invoking `create_all()`.
- **Residual Risk**: None.

---

### Finding 4 [P1 — Production Reliability / Concurrency Issue]
- **File**: `services/action_layer/idempotency.py`
- **Line / Function**: Lines 54–56, 122–128 (`IdempotencyManager`)
- **Vulnerability**: In-Memory Concurrency Locks Failing Distributed Multi-Pod Serialization
- **Attack / Failure Scenario**: `IdempotencyManager` stores `self._locks: Dict[str, threading.Lock]` and in-flight tokens in local process memory. In a multi-worker setup (e.g. Uvicorn `--workers 4` or 3 API pods behind a load balancer), two identical concurrent requests with key `idem_123` hit Pod 1 and Pod 2 at the same millisecond. Pod 1 and Pod 2 cannot see each other's in-memory locks, leading to concurrent gateway execution attempts.
- **Impact**: Potential duplicate payment execution against the payment gateway if DB unique constraint check is delayed.
- **Root Cause**: Relied on process-local Python mutexes rather than PostgreSQL distributed synchronization.
- **Recommended Fix**:
  1. Use PostgreSQL advisory locks (`pg_try_advisory_lock` with deterministic integer hash of `(merchant_id, idempotency_key)`) or atomic database transactions.
  2. Treat the `recovery_actions` table and PostgreSQL database as the sole source of distributed idempotency truth.
  3. Retain in-memory `threading.Lock` purely as a local thread-level optimization.
- **Test Required**: Concurrency test asserting multi-process serialization and duplicate rejection.
- **Residual Risk**: Low; bounded by database network latency.

---

### Finding 5 [P1 — Production Reliability / Concurrency Issue]
- **File**: `services/action_layer/stage2_activation.py`
- **Line / Function**: Lines 73–79 (`Stage2ActivationManager`)
- **Vulnerability**: Process-Local In-Memory Kill Switch & Volume Caps
- **Attack / Failure Scenario**: An operator detects abnormal activity and triggers the emergency kill-switch via `/api/v1/admin/stage2/kill-switch`. The request lands on Pod 1, setting `Pod1._state.kill_switch_engaged = True`. Pod 2 and Pod 3, running in distinct OS processes or pods, never receive the signal because state is stored in Python RAM. Pod 2 continues executing live transactions.
- **Impact**: Failure of emergency containment; financial loss during operational incidents.
- **Root Cause**: Singleton pattern stored state in memory rather than centralized PostgreSQL database.
- **Recommended Fix**:
  1. Persist system safety state (kill switch status, daily volume cap, active mode) in PostgreSQL or check the database authoritative state before every financial execution.
  2. Fail closed: if the database cannot be queried to verify kill switch state, block execution.
- **Test Required**: Multi-instance test: activating kill switch on Instance A must immediately block execution on Instance B.
- **Residual Risk**: Low.

---

### Finding 6 [P1 — Production Reliability / Concurrency Issue]
- **File**: `services/orchestrator/workflow.py`
- **Line / Function**: Line 591 (`execute_canonical_decision_workflow`)
- **Vulnerability**: Hardcoded SimulationGateway in Production Decision Workflow
- **Attack / Failure Scenario**: Line 591 instantiates `SimulationGateway(simulate_timeout=simulate_timeout, simulate_decline_code=simulate_decline)`. Even when the system is operating in `razorpay_test` mode or with live credentials, the master decision pipeline always routes execution through `SimulationGateway`.
- **Impact**: Inability to execute real payment recoveries via configured payment gateways.
- **Root Cause**: Master decision loop was authored with simulation defaults and lacked dynamic gateway resolution.
- **Recommended Fix**:
  1. Inject or resolve the appropriate gateway adapter based on server configuration (`settings.GATEWAY_MODE` or merchant configuration).
  2. In production, fail closed if live mode is requested but credentials are absent. Never silently fall back to `SimulationGateway`.
- **Test Required**: Unit tests demonstrating gateway resolution for `simulation`, `razorpay_test`, and fail-closed behavior in `production`.
- **Residual Risk**: None.

---

### Finding 7 [P2 — Engineering Correctness Issue]
- **File**: `apps/api/main.py`
- **Line / Function**: Line 1283 (`execute_action_route`)
- **Vulnerability**: Hardcoded `stage_1_safety_lock=True` in Action REST Endpoint
- **Attack / Failure Scenario**: Line 1283 creates `executor = ActionExecutor(stage_1_safety_lock=True)`. Even if an authorized admin has activated Stage 2 via dual-key approval, the REST API route always passes `stage_1_safety_lock=True`, resulting in unconditional `HTTP 423 Locked`.
- **Impact**: Disconnect between administrative Stage 2 activation state and endpoint execution.
- **Root Cause**: Hardcoded safety flag preventing dynamic evaluation of `Stage2ActivationManager.is_live_execution_authorized()`.
- **Recommended Fix**: Pass `stage_1_safety_lock = not stage2_mgr.is_live_execution_authorized()`.
- **Test Required**: Test showing endpoint respects Stage 2 authorization when properly unlocked and blocks when locked.
- **Residual Risk**: Stage 1 remains locked by default; Stage 2 requires dual-key activation.

---

## Action Plan & Remediation Matrix

| Priority | Finding / Area | Target File | Status |
| :---: | :--- | :--- | :---: |
| **P0** | Reject test tokens in production; eliminate `Merchant.first()` | `services/auth/service.py` | Queued (Phase 1) |
| **P0** | Eliminate blind SUCCESS; query gateway status for UNKNOWN | `services/worker/reconciliation_worker.py` | Queued (Phase 2) |
| **P1** | Remove `Base.metadata.create_all()` from worker daemon | `apps/worker/main.py` | Queued (Phase 3) |
| **P1** | Add database-backed distributed idempotency & advisory locks | `services/action_layer/idempotency.py` | Queued (Phase 4) |
| **P1** | Add database-backed distributed kill-switch state | `services/action_layer/stage2_activation.py` | Queued (Phase 5) |
| **P1** | Remove hardcoded `SimulationGateway` in workflow | `services/orchestrator/workflow.py` | Queued (Phase 6) |
| **P2** | Dynamic Stage 1 safety check in action execution endpoint | `apps/api/main.py` | Queued (Phase 7) |
