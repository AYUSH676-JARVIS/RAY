# RAY Control Plane — Financial Execution Path Audit

**Document Classification:** Tier-1 Fintech Platform Security & Financial Safety Audit  
**Target System:** RAY Merchant Revenue Recovery Control Plane  
**Review Standard:** Stripe / Razorpay / Adyen / Google / Microsoft Fintech Production Standards  
**Status:** COMPLETE & VERIFIED  

---

## 1. Executive Summary & Audit Scope

The RAY Control Plane is designed to prevent lost merchant revenue through intelligent diagnosis and automated or administrative recovery. However, in financial technology systems, automated money movement represents the highest-risk capability.

This audit traces and proves every code path in the repository that can trigger external gateway financial actions (retries, payment links, customer outreach, or routing fallbacks).

### Core Financial Invariant
> **"AI RECOMMENDS. DETERMINISTIC POLICY AUTHORIZES. DETERMINISTIC ACTION LAYER EXECUTES. OUTCOME VERIFICATION CONFIRMS REALITY. AUDIT RECORDS EVERYTHING."**

This invariant is formally enforced in code. AI reasoning layers have **ZERO** execution authority, cannot bypass deterministic policy checks, cannot alter transaction amounts, and cannot select payment gateways.

---

## 2. Complete Financial Execution Path Inventory

| Entry Point | Trigger Mechanism | Target Layer | Default Safety Mode | Kill-Switch Protection |
| :--- | :--- | :--- | :--- | :--- |
| `POST /api/actions/execute` | Authenticated API Call (`ACTION_EXECUTE`) | `ActionExecutor` | `stage_1_safety_lock=True` (HTTP 423) | Enforced via `Stage2ActivationManager` |
| `POST /api/decision-loop/run` | Manual/Automated Pipeline Run | `run_decision_workflow` | `stage_1_safety_lock=True` (BLOCKED) | Enforced via `Stage2ActivationManager` |
| `POST /api/demo/scenario` | Scenario Runner | `DemoScenarioEngine` | Scoped Simulation | Isolated |
| `RetryScheduler.process_due_actions` | Worker Background Poller | `ActionExecutor` | `stage_1_safety_lock=True` (BLOCKED) | Enforced via `Stage2ActivationManager` |
| `ReconciliationWorker` | Worker Poller | `RazorpayGateway.query_status` | Read-Only Status Inquiry | Non-Mutating Inquiry Only |

---

## 3. Defense-in-Depth Gate Architecture

Every financial execution path must pass sequentially through five deterministic gate barriers before any money can move:

```
[ Inbound Request / Due Action ]
              │
              ▼
    [ Gate 1: Authentication & Tenant Ownership ]
       - HS256 JWT / API Key verification (Test keys rejected in production)
       - Strict tenant binding (caller merchant_id == entity merchant_id)
              │
              ▼
    [ Gate 2: Deterministic Policy Clearance ]
       - Dynamic policy verification (PolicyDecisionType.APPROVED required)
       - Cannot be overridden by AI reasoning or recommendation payload
              │
              ▼
    [ Gate 3: Distributed Idempotency Lock ]
       - PostgreSQL transaction-level advisory lock (pg_try_advisory_xact_lock)
       - Row-level lock on RecoveryAction (SELECT FOR UPDATE)
       - Replays cached receipts on duplicates; blocks concurrent in-flight executions
              │
              ▼
    [ Gate 4: Stage 1 Safety Guard Lock ]
       - Default system configuration: stage_1_safety_lock = True
       - Transitions to BLOCKED_STAGE1_SAFETY; raises FinancialExecutionBlockedError
              │
              ▼
    [ Gate 5: Distributed Kill Switch & Stage 2 Activation ]
       - Checked in PostgreSQL table system_safety_controls
       - If kill_switch_engaged == True -> Execution Blocked across all pods
       - If single_transaction_cap or daily_volume_cap breached -> Kill Switch Auto-Engages
       - Fails closed if database is unreachable
              │
              ▼
   [ Authoritative Gateway Dispatch ] (RazorpayGateway / SimulationGateway)
              │
              ▼
   [ Outcome Verification & State Machine Update ]
       - Truthful reconciliation against gateway status inquiry
       - Timeouts captured as UNKNOWN; blind retries strictly prohibited
```

---

## 4. Verification of Core Invariants

### 4.1 Deterministic Policy Approval Non-Bypassability
- In `services/action_layer/executor.py` (`ActionExecutor.execute_recovery_action`):
  ```python
  if policy_decision != PolicyDecisionType.APPROVED.value:
      raise PolicyAuthorizationBlockedError(...)
  ```
- If a caller attempts execution without policy clearance, or with `FLAGGED` or `REJECTED`, the action is blocked before any gateway instantiation.

### 4.2 Stage 1 Safety Guard Non-Bypassability
- In `ActionExecutor`:
  ```python
  if self.stage_1_safety_lock:
      validate_action_transition(ActionExecutionStatus.REQUESTED, ActionExecutionStatus.BLOCKED_STAGE1_SAFETY)
      raise FinancialExecutionBlockedError(...)
  ```
- All production API routes instantiate `ActionExecutor(stage_1_safety_lock=True)`.

### 4.3 Distributed Kill Switch Non-Bypassability
- In `services/action_layer/stage2_activation.py` and `services/action_layer/executor.py`:
  - Ephemeral in-memory check AND authoritative PostgreSQL query against `system_safety_controls`.
  - Engagement on Pod A immediately propagates to Pod B via shared database state.
  - If the database is unreachable or throws a connection error, `is_live_execution_authorized()` **fails closed** (returns `False`).
  - Active kill switch halts **ALL** financial execution attempts immediately.

### 4.4 Monetary Amount Validation
- `Payment.amount` is validated as positive decimal (`ge=Decimal("0.01")`).
- Single transaction limit cap (`single_transaction_cap`, default ₹5,000.00) is enforced.
- Aggregate daily volume cap (`daily_volume_cap`, default ₹25,000.00) is atomically accumulated with row-level locks (`SELECT FOR UPDATE`).

### 4.5 Distributed Idempotency
- Uses PostgreSQL transaction advisory locks (`SELECT pg_try_advisory_xact_lock(:key)`).
- Concurrent execution of the same idempotency key raises `ConcurrentExecutionBlockedError`.
- Duplicate executions after completion replay the immutable `StoredReceipt` from PostgreSQL ledger.

### 4.6 Outcome Verification & UNKNOWN State Handling
- When gateway responses time out or encounter acquirer uncertainty:
  - Status is marked `UNKNOWN` on both `RecoveryAction` and `Payment`.
  - State machine strictly blocks any retry attempt on an `UNKNOWN` payment until authoritative reconciliation queries the upstream gateway.

---

## 5. Empirical Verification & Test Evidence

| Safety Invariant | Automated Test | Result |
| :--- | :--- | :--- |
| Stage 1 Safety Lock Blocks Execution | `tests/unit/test_stage2_money_movement.py::test_action_executor_respects_stage1_safety_lock` | **PASSED** |
| Dual-Key Authorization Required | `tests/unit/test_stage2_money_movement.py::test_stage2_activation_requires_valid_reason_and_token` | **PASSED** |
| Limit Breach Triggers Kill Switch | `tests/unit/test_stage2_money_movement.py::test_stage2_limit_breach_triggers_kill_switch` | **PASSED** |
| Multi-Pod Kill Switch Propagation | `tests/concurrency/test_distributed_kill_switch.py::test_kill_switch_propagates_across_pods` | **PASSED** |
| Kill Switch Blocks Financial Executor | `tests/concurrency/test_distributed_kill_switch.py::test_kill_switch_blocks_financial_executor` | **PASSED** |
| DB Outage Fails Closed | `tests/concurrency/test_distributed_kill_switch.py::test_kill_switch_fails_closed_on_db_outage` | **PASSED** |
| Distributed Idempotency Ledger Recovery | `tests/concurrency/test_distributed_idempotency_postgres.py::test_distributed_idempotency_postgres_ledger_recovery` | **PASSED** |
| In-Flight Concurrent Action Blocking | `tests/concurrency/test_distributed_idempotency_postgres.py::test_concurrent_in_flight_action_blocked` | **PASSED** |
| Gateway Config Fail-Closed in Prod | `tests/unit/test_workflow_gateway_configuration.py::test_production_strictly_forbids_simulation_gateway` | **PASSED** |
| Missing Live Credentials Fails Closed | `tests/unit/test_workflow_gateway_configuration.py::test_production_fails_closed_when_live_credentials_missing` | **PASSED** |

---

## 6. Residual Risks & Mitigations

1. **Database Lock Contention under Extreme Concurrency:**  
   - *Risk:* Multiple workers retrying the same key simultaneously wait on PostgreSQL advisory locks.  
   - *Mitigation:* `pg_try_advisory_xact_lock` is non-blocking (`try`), immediately returning `False` and rejecting concurrent in-flight execution without holding connection resources.
2. **Gateway Webhook Secret Rotation:**  
   - *Risk:* Zero-downtime secret rotation requires dual-secret support.  
   - *Mitigation:* Webhook verification layer supports timing-attack safe HMAC verification with immediate audit logging of signature discrepancies.
