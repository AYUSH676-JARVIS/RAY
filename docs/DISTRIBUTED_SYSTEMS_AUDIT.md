# RAY Control Plane — Distributed Systems Hardening Audit

**Document Classification:** Principal Distributed Systems Architecture & Concurrency Audit  
**Target System:** RAY Merchant Revenue Recovery Control Plane  
**Review Standard:** Stripe / Razorpay / Google / Microsoft Production Infrastructure Standards  
**Status:** COMPLETE & VERIFIED  

---

## 1. Executive Summary

In a high-throughput merchant revenue recovery system, operations occur across multiple worker pods, background schedulers, and inbound webhook listeners. Without strict distributed coordination primitives, distributed race conditions can cause:
1. Double money movements (charging a merchant's customer twice for the same order)
2. Inconsistent safety state (kill switch engaged on Pod A while Pod B continues money movement)
3. Split-brain reconciliation decisions (marking a payment successful before authoritative confirmation)
4. Lost event notifications due to two-phase commit failures

This audit documents the distributed systems hardening implemented and verified in the RAY platform to eliminate each of these failure modes.

---

## 2. Distributed Idempotency Hardening

### 2.1 The Vulnerability
Previously, the idempotency layer relied on in-memory locks (`threading.Lock`) combined with ephemeral caching. In a multi-pod Kubernetes deployment, or upon pod restart/crash, two concurrent requests with identical idempotency keys on different nodes could execute concurrently against the payment gateway.

### 2.2 The Solution
We upgraded [`services/action_layer/idempotency.py`](file:///Users/ayushtripathi/Ray/services/action_layer/idempotency.py) to a two-tier defense-in-depth architecture:

1. **PostgreSQL Transaction-Level Advisory Locking:**
   - Keys are derived via SHA-256/MD5 hashing of the tuple `(merchant_id, idempotency_key)` to produce a 32-bit positive integer lock identifier.
   - Workers execute:
     ```sql
     SELECT pg_try_advisory_xact_lock(:lock_key);
     ```
   - **Non-blocking:** Returns `FALSE` immediately if another worker holds the lock, avoiding thread pool exhaustion.
   - **Crash-resilient:** Transaction-level advisory locks are automatically released by the PostgreSQL kernel if the client disconnects or crashes.

2. **Durable Ledger Lookup with Row Locking:**
   - In-flight execution validation queries the durable ledger:
     ```sql
     SELECT * FROM recovery_actions 
     WHERE merchant_id = :mid AND idempotency_key = :key 
     FOR UPDATE;
     ```
   - If an action is already `PROCESSING`, concurrent attempts raise `ConcurrentExecutionBlockedError`.
   - If the action has reached terminal status (`SUCCEEDED`, `FAILED`, `BLOCKED_STAGE1_SAFETY`, `REJECTED`, `ACCEPTED`), the immutable `StoredReceipt` is returned directly from PostgreSQL without contacting the gateway.

### 2.3 Verification Evidence
- Tested in [`tests/concurrency/test_distributed_idempotency_postgres.py`](file:///Users/ayushtripathi/Ray/tests/concurrency/test_distributed_idempotency_postgres.py):
  - `test_distributed_idempotency_postgres_ledger_recovery`: Memory cache wiped; second worker re-reads receipt from PostgreSQL and skips gateway dispatch.
  - `test_concurrent_in_flight_action_blocked`: Concurrent worker attempting execution on an in-flight key is blocked with `ConcurrentExecutionBlockedError`.

---

## 3. Globally Authoritative Distributed Kill Switch

### 3.1 The Vulnerability
Previously, `Stage2ActivationManager` maintained `kill_switch_engaged` as an in-memory boolean flag on the local singleton. If an operator engaged the kill switch via the admin API on Pod A, Pod B and Pod C remained unaware, allowing live money movement to continue until all pods restarted.

### 3.2 The Solution
We implemented a shared, PostgreSQL-backed distributed control plane via the `system_safety_controls` table (managed by Alembic migration [`migrations/versions/0002_webhook_and_outbox.py`](file:///Users/ayushtripathi/Ray/migrations/versions/0002_webhook_and_outbox.py) and model [`services/money_graph/models.py`](file:///Users/ayushtripathi/Ray/services/money_graph/models.py)):

1. **Authoritative Persistence:**
   - Columns: `scope` (`GLOBAL` or tenant UUID), `mode`, `is_live_authorized`, `kill_switch_engaged`, `single_transaction_cap`, `daily_volume_cap`, `current_daily_volume`.
2. **Fail-Closed Distributed Check:**
   - `is_live_execution_authorized(merchant_id, session)` checks both the merchant-specific row and the `GLOBAL` row in PostgreSQL.
   - If the database connection fails or cannot be queried, the method **fails closed** (returns `False`).
3. **Multi-Pod Propagation:**
   - Engaging the kill switch atomically updates `system_safety_controls` and logs a cryptographically chained `AuditEvent`.
   - Every subsequent execution across any pod immediately halts.
4. **Volume Cap Auto-Trigger:**
   - If a single transaction exceeds `single_transaction_cap` (₹5,000.00) or aggregate volume exceeds `daily_volume_cap` (₹25,000.00), the kill switch is automatically engaged in the database.

### 3.3 Verification Evidence
- Tested in [`tests/concurrency/test_distributed_kill_switch.py`](file:///Users/ayushtripathi/Ray/tests/concurrency/test_distributed_kill_switch.py):
  - `test_kill_switch_propagates_across_pods`: Pod A engages kill switch $\to$ Pod B immediately observes `is_live_execution_authorized == False`.
  - `test_kill_switch_blocks_financial_executor`: Financial executor halts with `FinancialExecutionBlockedError`, and zero gateway calls are made.
  - `test_kill_switch_fails_closed_on_db_outage`: Database network exception causes immediate fail-closed return.

---

## 4. Transactional Outbox Pattern & Event Reliability

### 4.1 The Vulnerability
Dual-write problems arise when an application updates a database entity (e.g. Payment transitions to `CAPTURED`) and simultaneously publishes a message to a broker (Kafka/RabbitMQ/webhook dispatcher). If the broker network fails after the DB commit, the event is lost. If the commit fails after the broker publish, downstream systems process phantom events.

### 4.2 The Solution
The platform enforces the Transactional Outbox Pattern:
1. The domain entity mutation and the `OutboxEvent` record are inserted in the **same database transaction**:
   ```python
   session.add(payment)
   session.add(outbox_event)
   session.commit() # Atomic boundary
   ```
2. The `OutboxProcessor` worker ([`services/worker/outbox_processor.py`](file:///Users/ayushtripathi/Ray/services/worker/outbox_processor.py)) polls pending outbox events using row-level locks (`SKIP LOCKED`), dispatches them reliably with exponential backoff, and marks them `DISPATCHED`.

### 4.3 Verification Evidence
- Tested in [`tests/unit/test_transaction_integrity.py::test_outbox_event_atomic_with_domain_mutation`](file:///Users/ayushtripathi/Ray/tests/unit/test_transaction_integrity.py) and [`tests/unit/test_background_workers.py::test_outbox_processor`](file:///Users/ayushtripathi/Ray/tests/unit/test_background_workers.py).

---

## 5. Summary Scorecard: Distributed Systems Hardening

| Primitive | Mechanism | Failure Mode Prevented | Verified Test |
| :--- | :--- | :--- | :--- |
| **Distributed Idempotency** | PostgreSQL Advisory Lock + `FOR UPDATE` | Concurrent duplicate execution | `test_distributed_idempotency_postgres.py` |
| **Kill Switch** | PostgreSQL `system_safety_controls` | Inconsistent safety state across pods | `test_distributed_kill_switch.py` |
| **Event Reliability** | Transactional Outbox Pattern | Lost events on broker failure | `test_transaction_integrity.py` |
| **Reconciliation** | Authoritative Gateway Inquiry | Blind `SUCCESS` assumptions | `test_reconciliation_hardening.py` |
| **DDL at Runtime** | `check_migrations_applied` | Deadlocks & privilege escalation | `test_worker_startup_no_ddl.py` |
