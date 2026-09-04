# Security & Financial Safety Architecture

## 1. Threat Model & Security Posture

As a merchant financial control plane, RAY operates in a high-consequence environment. Security in RAY is anchored on defense-in-depth across three tiers:
1. **Financial Action Safety (Idempotency & Stage Gates)**
2. **Secret & Credential Management (Zero Hardcoding)**
3. **Audit Immutability & Data Integrity**

---

## 2. Idempotency Key Architecture

Double billing and duplicate capture requests represent severe financial and regulatory liability. RAY enforces strict idempotency at both the application and database layers:

```
                  POST /api/actions/execute
                             │
                             ▼
         ┌───────────────────────────────────────┐
         │ Check idempotency_key format (>7 chars)│
         └───────────────────┬───────────────────┘
                             │
                             ▼
         ┌───────────────────────────────────────┐
         │ Check Unique Constraint in Database   │
         │ (payment_attempts.idempotency_key,   │
         │  recovery_actions.idempotency_key)    │
         └───────────────────┬───────────────────┘
               Duplicate ┌───┴───┐ New Key
                         │       │
                         ▼       ▼
               Return Cached   Stage 1 Safety Lock
                  Receipt      (BLOCKED_STAGE1_SAFETY)
```

- **Unique Database Constraints**: `payment_attempts.idempotency_key` and `recovery_actions.idempotency_key` are indexed with unique database constraints. Any collision triggers an `IntegrityError` and immediate rollback.
- **Client Key Format Validation**: The `ActionExecutor` validates that keys are non-empty and conform to unique format strings (`idem_pay_<uuid>_<attempt>` or `idem_act_<opp>_<action>`).

---

## 3. Zero Hardcoded Secrets Policy

In strict accordance with the RAY specification:
- **No plaintext secrets** exist anywhere in source code, configuration files, test suites, or Docker definitions.
- All secrets and connection strings are injected dynamically via environment variables (`DATABASE_URL`, `POSTGRES_PASSWORD`, `NEXT_PUBLIC_API_URL`).
- `.env.example` provides non-sensitive template placeholders.
- Real `.env` files are excluded from git tracking via `.gitignore`.

---

## 4. Execution Stage Gates

To prevent unauthorized or accidental money movement:
- The deterministic action layer contains a default hardcoded safety switch: `stage_1_safety_lock = True`.
- Attempting to invoke `execute_recovery_action` raises `FinancialExecutionBlockedError`.
- All `RecoveryAction` records default to `execution_status = 'BLOCKED_STAGE1_SAFETY'`.
- Only when Phase 2 live execution contracts are implemented with cryptographically signed merchant approval will this gate be operable.

---

## 5. Audit Immutability

- Every state change, policy decision, agent execution, and tool call produces an immutable row in `audit_events`.
- Events cannot be updated or deleted through the API (`DELETE` / `PUT` routes are deliberately not exposed).
- Audit timestamps are enforced with timezone-aware UTC (`UTCDateTime`).

---

## 6. Read-Only Intelligence & Non-Mutating Detection

The Money Graph and Financial Opportunity subsystems guarantee strict data boundary protections:
1. **MoneyGraphService Read-Only Guarantee**: Money Graph query endpoints (`GET /api/payments/{id}/context`) perform pure relational projections. No transaction status, attempt counter, or customer state is ever mutated.
2. **Dynamic Opportunity Detection Invariant**: Calling `POST /api/opportunities/detect` evaluates contextual intelligence dynamically in-memory. It does not insert or execute `RecoveryAction` records.
3. **Stage 1 Safety Lock Preservation**: The execution lock remains fully armed and unaltered. `is_financial_action_executed` is perpetually `False`.

