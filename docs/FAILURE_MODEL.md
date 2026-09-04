# RAY — Failure Model & Resilience Invariants

## 1. Database & Infrastructure Failure Modes

| Failure Scenario | Mitigation / Architectural Defense | Invariant Maintained |
| :--- | :--- | :--- |
| **Missing `DATABASE_URL` in Production** | `get_canonical_url()` and `init_db()` raise explicit `RuntimeError` on startup. | Zero silent fallback to in-memory SQLite or dummy default in production. |
| **Unapplied Database Migrations** | `/ready` probe returns `HTTP 503`. Application refuses traffic. | Prohibits unmigrated schema drift from corrupting merchant data. |
| **Database Connection Exhaustion** | SQLAlchemy connection pooling configured with `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`. | Dropped stale connections recycled cleanly without crashing workers. |
| **Silent `create_all()` Execution** | Prohibited in persistent environments. Removed from all production execution paths. | Only Alembic versioned migrations mutate persistent database schema. |

---

## 2. Financial Execution & Gateway Failure Modes

| Failure Scenario | Mitigation / Architectural Defense | Invariant Maintained |
| :--- | :--- | :--- |
| **Gateway Network Timeout / HTTP 504** | Transaction enters `PaymentStatus.UNKNOWN`. Blind retry is blocked. | Prevents double-charging cardholders during ambiguous upstream states. |
| **Concurrent Duplicate Requests** | `IdempotencyManager` serializes threads with atomic mutex. Exactly 1 execution occurs; subsequent callers receive cached receipt with `idempotent_replay: true`. | Strictly enforces exact-once financial execution. |
| **Unapproved Financial Action** | `BLOCKED_STAGE1_SAFETY` hard lock active in `ActionExecutor` by default. | Autonomous agents cannot trigger real money movements without merchant authorization. |
| **Invalid State Transition** | `PaymentStateMachine` and `ActionStateMachine` validate all state hops. | Illegal state jumps (e.g. `FAILED` -> `SETTLED`) raise `InvalidStateTransitionError`. |

---

## 3. AI Safety & Adversarial Failure Modes

| Failure Scenario | Mitigation / Architectural Defense | Invariant Maintained |
| :--- | :--- | :--- |
| **Prompt Injection Attack** | `PromptInjectionSanitizer` scans and neutralizes override patterns (`ignore previous instructions`, `bypass policy`, `override limits`). | Untrusted customer input cannot manipulate agent instructions. |
| **Hallucinated Evidence** | `AIReasoningValidator` checks all cited evidence IDs against empirical Money Graph entities. | Recommendations citing nonexistent entities are rejected. |
| **LLM Schema Deviation** | Strict Pydantic parsing (`AIStrategyRecommendation`). Validation errors trigger immediate deterministic fallback. | Malformed AI outputs cannot corrupt decision pipeline. |
| **Audit Log Tampering** | SHA-256 cryptographic hash chaining (`verify_audit_chain`). Modifying any payload or deleting a row breaks the chain. | Non-repudiation and immutable financial audit trail. |
