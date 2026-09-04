# Build Log — RAY Merchant Money Intelligence Engine

## Project Context
- **Date**: September 2, 2026
- **Track**: AI Revenue Recovery
- **Principle**: AI recommends. Deterministic policy authorizes. Deterministic action layer executes. Outcome verification confirms reality. Audit records everything.

---

## Chronological Implementation Milestones

### Milestone 1: Environment & Repository Foundation
- Initialized complete monorepo directory tree:
  `apps/web`, `apps/api`, `services/money_graph`, `services/opportunities`, `services/decision_engine`, `services/policy_engine`, `services/action_layer`, `services/outcome_engine`, `services/audit`, `agents/*`, `ml/*`, `data/*`, `tests/*`, `docs/*`, `scripts`.
- Configured root project files:
  - `requirements.txt`: Python 3.13, FastAPI, SQLAlchemy 2.0, psycopg 3, Pydantic v2, pandas, scikit-learn, XGBoost, pytest, httpx.
  - `.gitignore`: Comprehensive ignore rules for Python, Node/Next.js, SQLite, and secrets.
  - `.env.example`: Non-sensitive environment variable templates.
  - `docker-compose.yml`: Multi-container orchestration for PostgreSQL 16 Alpine, FastAPI API, and Next.js Web.
  - `README.md`: Architecture overview, quick start, and operational invariants.

### Milestone 2: Database Foundation
- Built 12 SQLAlchemy 2.0 models with UUID primary keys and timezone-aware UTC timestamps:
  1. `Merchant`
  2. `Customer`
  3. `Order`
  4. `Payment`
  5. `PaymentAttempt` (with unique `idempotency_key`)
  6. `PaymentFailure` (with 8 standardized failure categories)
  7. `RecoveryOpportunity`
  8. `RecoveryAction` (with unique `idempotency_key` and `BLOCKED_STAGE1_SAFETY` status)
  9. `PolicyDecision`
  10. `AgentRun`
  11. `ToolCall`
  12. `AuditEvent`
- Solved dialect-agnostic UTC preservation via `UTCDateTime` TypeDecorator (guaranteeing `tzinfo=timezone.utc` across SQLite in-memory tests and native PostgreSQL).
- Executed initial database schema initialization on local PostgreSQL 16 (`ray_db`). All 12 tables and relational indexes created without errors.

### Milestone 3: Deterministic Synthetic Data Generation
- Implemented `SyntheticDataGenerator` in `data/synthetic/generator.py` and CLI in `scripts/generate_data.py`.
- Seed: fixed seed `42` for exact reproducibility.
- Benchmark: Generated full dataset and committed to PostgreSQL in **2.58 seconds**.
- Verified database record counts in PostgreSQL:
  - Customers: 10,000
  - Orders: 20,000
  - Payments: 25,000
  - Failed Payments: 5,000 (Success: 20,000)
  - Payment Attempts: 25,000 (all with unique `idempotency_key`)
  - Payment Failures: 5,000
  - Recovery Opportunities: 5,000
  - Recovery Actions: 5,000
  - Policy Decisions: 5,000
  - Agent Runs: 25 (with linked ToolCalls and AuditEvents)
- Failure Category Distribution verified:
  - `INSUFFICIENT_FUNDS`: 1,285
  - `BANK_TIMEOUT`: 893
  - `ISSUER_DECLINED`: 724
  - `AUTHENTICATION_FAILED`: 597
  - `NETWORK_ERROR`: 498
  - `CARD_EXPIRED`: 388
  - `LIMIT_EXCEEDED`: 358
  - `FRAUD_SUSPECTED`: 257
  - Total: 5,000

### Milestone 4: FastAPI Control Plane API
- Implemented `apps/api/main.py` with typed Pydantic v2 schemas (`apps/api/schemas.py`):
  - `GET /health`: System status, DB connectivity, UTC timestamp.
  - `GET /api/dashboard`: Aggregates total payment volume ($9.52M), failed volume ($1.66M recoverable), failure rate (20.0%), failure distribution, and policy authorization stats.
  - `GET /api/payments/{payment_id}`: Full payment graph detail, attempt history, failure diagnosis, and recovery hypotheses.
  - `GET /api/opportunities`: Paginated list of recovery opportunities with strategy confidence scores.
  - `GET /api/audit/runs/{run_id}`: Detailed trace of agent runs, tool invocations, and audit timeline.
- Smoke tested endpoints via test client: all returned HTTP 200 with complete payloads.

### Milestone 5: Testing Suite Execution
- Configured `pytest.ini` with `pythonpath = .`.
- Built unit test suites:
  - `tests/unit/test_models.py`: Validated UUID keys, UTC timestamps, and all 12 entities.
  - `tests/unit/test_idempotency.py`: Validated unique constraints on `PaymentAttempt` and `RecoveryAction` idempotency keys, empty key rejection in `ActionExecutor`, and Stage 1 execution lock.
  - `tests/unit/test_payment_relationships.py`: Validated bidirectional graph traversal.
  - `tests/unit/test_synthetic_data.py`: Validated seed reproducibility and constraint adherence.
- Built integration test suite:
  - `tests/integration/test_api.py`: Validated API startup, `/health`, `/api/dashboard`, `/api/payments/{payment_id}` retrieval of synthetic payments, 404 handling, `/api/opportunities`, and `/api/audit/runs/{run_id}`.
- Test run results: **17 passed, 0 failed in 0.42s**.

### Milestone 6: Money Graph & Financial Opportunity Engine
- Implemented read-only `MoneyGraphService` (`services/money_graph/service.py`) and schemas (`schemas.py`) covering:
  - `get_payment_context`, `get_customer_context`, `get_order_context`, `get_failure_context`, `get_customer_payment_history`, `get_related_transactions`, `get_payment_attempt_history`, `get_full_money_context`.
  - Enforced strict non-fabrication guarantee (null/unknown values preserved without placeholders).
- Implemented `Failure Intelligence` (`services/opportunities/failure_intelligence.py`) for all 8 decline categories with calibrated prototype recovery probabilities and candidate/blocked strategy matrices.
- Implemented `DecisionProvider` architecture (`services/opportunities/providers.py`) with `DeterministicDecisionProvider` and `AIDecisionProvider` extension hook.
- Implemented `FinancialOpportunityEngine` (`services/opportunities/engine.py`) with explainable opportunity score formula:
  `opportunity_score = (EV * prob * confidence * urgency) - risk_cost - action_cost`.
- Added API endpoints in `apps/api/main.py`:
  - `GET /api/payments/{payment_id}/context`: Returns complete `FullMoneyContext`.
  - `POST /api/opportunities/detect`: Dynamically derives recovery opportunity from live Money Graph data.
  - `GET /api/opportunities/{opportunity_id}`: Retrieves opportunity record by ID.
- Built unit and integration tests:
  - `tests/unit/test_money_graph.py`: 3 tests.
  - `tests/unit/test_opportunity_engine.py`: 11 tests covering all prompt scenarios.
  - `tests/integration/test_opportunity_api.py`: 5 tests.
- Total Test Results: **36 passed, 0 failed in 0.61s**.
- Enhanced Next.js frontend control plane (`apps/web/src/app/page.tsx`) with dynamic opportunity evaluation and inspectable score breakdown.

---

## Actual Failures Encountered & Remediations
1. **ModuleNotFoundError on CLI Execution**:
   - *Failure*: `scripts/generate_data.py` failed with `No module named 'services'` when invoked directly without PYTHONPATH.
   - *Remediation*: Added explicit repository root insertion into `sys.path` in `generate_data.py`.
2. **SQLite Naive Datetime on In-Memory Tests**:
   - *Failure*: `test_uuid_and_utc_timestamps_default` failed on `assert merchant.created_at.tzinfo is not None` when reloading from SQLite.
   - *Remediation*: Implemented `UTCDateTime(TypeDecorator)` to transparently ensure UTC timezone awareness across all database dialects.
3. **Pytest Import Path Discovery**:
   - *Failure*: Pytest collection failed with `ModuleNotFoundError` when running `pytest tests/`.
   - *Remediation*: Created `pytest.ini` specifying `pythonpath = .` and `testpaths = tests`.
4. **Unknown Failure Reason Synthesis**:
   - *Failure*: `test_unknown_failure_handled_without_fabrication` failed assert `"Unrecognized failure code" in opp.explanation.reason` because fallback template did not explicitly note the unmapped category code.
   - *Remediation*: Added explicit check `if failure_code not in FAILURE_PROFILES` to return `"Unrecognized failure code '{failure_code}'. Manual investigation required before any automated action."` in `_synthesize_reason`.

