# RAY — Merchant Revenue Recovery Control Plane

[![CI/CD Pipeline](https://img.shields.io/badge/CI%2FCD-passing-emerald?style=flat-square&logo=githubactions)](https://github.com/AYUSH676-JARVIS/RAY/actions)
[![Pytest Suite](https://img.shields.io/badge/pytest-334%20passed-emerald?style=flat-square&logo=pytest)](file:///Users/ayushtripathi/Ray/tests)
[![Financial Invariants](https://img.shields.io/badge/invariants-54%20passed-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/tests/invariants)
[![Concurrency & Races](https://img.shields.io/badge/concurrency-31%20passed-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/tests/concurrency)
[![Playwright E2E](https://img.shields.io/badge/Playwright%20E2E-10%20passed-emerald?style=flat-square&logo=playwright)](file:///Users/ayushtripathi/Ray/apps/web/e2e)
[![Security Scanners](https://img.shields.io/badge/gitleaks%20%7C%20pip--audit%20%7C%20npm%20audit-0%20vulnerabilities-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/docs/security.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](file:///Users/ayushtripathi/Ray/LICENSE)

> **RAY is a merchant revenue recovery control plane that automates retry and remediation workflows for failed payment transactions while enforcing deterministic financial safety boundaries.**

---

## 1. Problem Statement & Failure Modes

In merchant acquiring and e-commerce billing, **15% to 20% of payment attempts fail**. While some are permanent hard declines (e.g. `CARD_STOLEN`, `ACCOUNT_CLOSED`), a significant proportion stem from transient conditions: bank connection timeouts, 3DS authentication handshake drops, processor downtime, and temporary balance insufficiency.

### Common Engineering Failures in Naive Recovery Systems:

1. **The Read Timeout Ambiguity Trap**: When an HTTP gateway request times out after 10 seconds, naive systems assume the payment failed and retry. If the acquiring processor actually settled the initial charge, the customer gets double-billed.
2. **Uncontrolled Retries & Scheme Fines**: Indiscriminate retries on hard declines trigger card network penalties (e.g., Visa Excessive Retry Merchant Assessment fees) and increase merchant dispute rates.
3. **Split-Brain Distributed Locks**: Disjoint Redis locks (`Redlock`) can expire prematurely under worker GC pauses or network partitions, allowing concurrent threads to execute duplicate debits.
4. **Unconstrained AI Agents**: Granting generative AI models direct authority to debit accounts creates risks of prompt injection, hallucinated transactions, and compliance violations.

RAY addresses these failure modes by decoupling advisory intelligence from execution authority and enforcing strict transaction-level invariants.

---

## 2. Architectural Boundary & Execution Model

RAY enforces a five-stage operational boundary for every recovery decision:

```
[ 1. Telemetry Ingestion ]
       │  Webhook or API notifies payment failure with raw gateway decline code
       ▼
[ 2. Advisory Recommendation ]
       │  Heuristic / AI models score recovery probability and suggest strategy (e.g. SMART_RETRY_WINDOW)
       │  CRITICAL: The advisory layer has ZERO database write access and ZERO gateway credentials
       ▼
[ 3. Deterministic Policy Evaluation ]
       │  Policy engine evaluates hard rules: max retry limits, velocity caps, tenant isolation, fraud blacklist
       ▼
[ 4. Action Execution & Locking ]
       │  PostgreSQL transaction-scoped advisory lock acquired for (merchant_id, idempotency_key)
       │  Transactional outbox records state transition atomically
       │  Fail-closed Stage 1 safety lock and kill switch verified before dispatch
       ▼
[ 5. Cryptographic Audit Chaining ]
          Continuous SHA-256 parent-hash chain verifies append-only integrity
```

---

## 3. High-Level Architecture

```mermaid
graph TD
    User([Merchant Operations User / Browser]) <-->|HTTP :3000| WebApp[Next.js 16 Web Console]
    Acquirer([Acquiring Gateway / Webhooks]) <-->|HMAC Signed Webhook| API[FastAPI Control Plane :8000]
    WebApp <-->|REST API + Bearer Auth| API

    subgraph CORE_INFRASTRUCTURE["Core Services"]
        API -->|ACID Transactions & Advisory Locks| DB[(PostgreSQL 16 Engine :5432)]
        Worker[Transactional Outbox Worker] -->|Poll FOR UPDATE SKIP LOCKED| DB
        Worker -->|Idempotent Dispatch| GatewayAdapter[Gateway Adapter Boundary]
    end

    GatewayAdapter -.->|Mode: SIMULATION| SimEngine[Internal Simulation Engine]
    GatewayAdapter -.->|Mode: SANDBOX| RzpSandbox[Razorpay Sandbox API]
    GatewayAdapter -.->|Mode: LIVE (Strict Opt-In)| RzpLive[Razorpay Live API]
```

---

## 4. Core Engineering Invariants & Verified Mechanisms

| Invariant Property | Implementation Mechanism | Failure Mode Prevented | Verified Test Suite |
| :--- | :--- | :--- | :--- |
| **`UNKNOWN ≠ FAILED`** | Gateway read timeouts (`TimeoutException`, HTTP 504) record `PaymentStatus.UNKNOWN`. The engine raises `AmbiguousOutcomeBlockedError` if retry is attempted before authoritative reconciliation. | Customer double-charge after dropped connection. | `tests/invariants/test_financial_execution_invariants.py`<br>`tests/failure/test_financial_execution_failures.py` |
| **Distributed Idempotency** | PostgreSQL transaction-scoped advisory locks: `pg_try_advisory_xact_lock(hash(merchant_id, idempotency_key))`. In-flight duplicate requests fail fast with **HTTP 409 Conflict**. | Multi-pod race condition executing duplicate debits. | `tests/concurrency/test_100_thread_adversarial.py`<br>`tests/concurrency/test_distributed_idempotency_postgres.py` |
| **Transactional Outbox** | State transitions and outbound recovery events are committed to PostgreSQL within the *exact same ACID transaction*. The worker drains the outbox with `SELECT ... FOR UPDATE SKIP LOCKED`. | Inconsistent state when process crashes after DB commit but before network call. | `tests/concurrency/test_transactional_outbox.py` |
| **Advisory AI Isolation** | Machine learning and heuristic components only output structured recommendation scores. Hard policy rules (retry limits, cooldowns, merchant permissions) are evaluated deterministically. Models have **zero database write access** and **zero gateway credentials**. | Prompt injection or model hallucination moving money. | `tests/security/test_ai_boundary_security.py`<br>`tests/security/test_adversarial_trust_boundaries.py` |
| **Fail-Closed Kill Switch** | Emergency kill switch state is authoritative in PostgreSQL (`system_settings.kill_switch_engaged`). If the database connection is lost, the engine immediately fails closed. | Uncontrolled execution during database or infrastructure outages. | `tests/concurrency/test_distributed_kill_switch.py` |
| **Cryptographic Audit Chain** | Each `AuditEvent` computes `event_hash = SHA256(previous_hash + payload + sequence + timestamp)`. Tampering with any historical row breaks downstream validation. | Undetected database manipulation or rogue log modification. | `tests/invariants/test_batch4_audit_and_replay.py`<br>`tests/integration/test_audit_verification_api.py` |
| **Zero Float Arithmetic** | Financial amounts are stored as `NUMERIC(18, 4)` and handled exclusively as Python `decimal.Decimal` with explicit `ROUND_HALF_UP` quantization. AST checks ban `float` arithmetic. | IEEE 754 floating-point rounding errors and precision leakage. | `tests/invariants/test_financial_decimal_audit.py` |
| **Stage 1 Safety Lock** | `STAGE_1_SAFETY_LOCK=true` is the default setting. Live debit execution is blocked even if live gateway credentials are present. | Accidental live charges during onboarding or staging. | `tests/invariants/test_financial_execution_invariants.py` |

---

## 5. Technology Stack

* **Control Plane API**: Python 3.12/3.13, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
* **Relational Storage**: PostgreSQL 16 (transaction-scoped advisory locks, transactional outbox, row-level locking)
* **Web Console**: Next.js 16.3.4 (App Router, Turbopack, standalone output), React 19, TypeScript, Tailwind CSS v4
* **Testing & Verification**: Pytest (asyncio, concurrency, security suites), Playwright (Chromium browser journeys)
* **Security & Containerization**: Docker, Docker Compose, Gitleaks, pip-audit, npm audit, Prometheus metrics

---

## 6. Known Limitations & Engineering Tradeoffs

An honest evaluation requires documenting where the system's design makes deliberate tradeoffs:

1. **Single-Primary Database Scaling**:
   - Transaction-scoped advisory locks and the transactional outbox rely on PostgreSQL ACID guarantees on a single primary database.
   - For architectures exceeding 25,000 writes/second, the outbox would need to be migrated to a partitioned distributed event log (e.g. Apache Kafka or AWS Kinesis).
2. **Worker Polling Interval**:
   - The outbox worker runs on an asynchronous polling loop (default 3 seconds).
   - In production environments requiring sub-second recovery dispatch, this should be complemented with PostgreSQL `LISTEN/NOTIFY` or an event-driven queue consumer.
3. **Gateway Protocol Scope**:
   - Current gateway integration focuses on the Razorpay lifecycle (Order -> Payment -> Refund -> Webhook) and an internal Simulation engine.
   - Acquirers with separate Authorization and Capture phases (e.g. Stripe, Adyen) require mapping to their distinct two-phase capture state models.
4. **Card Scheme Velocity Rules**:
   - Visa limits retries on Category 1 (fraud) to 0 and Categories 2/3 to 15 attempts over 30 days.
   - RAY enforces merchant-level `max_retries` (default 3) and cooldown periods (default 3600s). Global card-network BIN-level velocity tracking across multiple merchants would require a shared tokenization vault.
5. **Clock Drift in Audit Chains**:
   - Audit hash chaining uses database-generated timestamps and serial IDs. In multi-region active-active databases, monotonic logical clocks (or hybrid logical clocks) would be necessary to prevent parent-hash ordering conflicts.

---

## 7. Verification & Quality Matrix

| Suite / Check | Test Count | Target Invariants | Status |
| :--- | :--- | :--- | :--- |
| **Full Pytest Suite** | 334 tests | Entire backend: models, APIs, state machines, outbox | **334 / 334 PASS** |
| **Financial Invariants** (`tests/invariants/`) | 54 tests | Zero float math, monotonic scoring, UNKNOWN state, advisory locks | **54 / 54 PASS** |
| **Security & Trust** (`tests/security/`) | 61 tests | Prompt injection, PII sanitization, HMAC replay defense, RBAC | **61 / 61 PASS** |
| **Concurrency & Outbox** (`tests/concurrency/`) | 15 tests | 100-thread races, transactional outbox atomicity, kill switch | **15 / 15 PASS** |
| **Failure & Resilience** (`tests/failure/`) | 16 tests | Socket timeouts, UNKNOWN != FAILED, process crash recovery | **16 / 16 PASS** |
| **Integration & Gateways** (`tests/integration/`) | 45 tests | Real API flows, SIMULATION, SANDBOX, LIVE boundary isolation | **45 / 45 PASS** |
| **Browser E2E Suite** (`apps/web/e2e/`) | 10 journeys | Authenticated console, pipeline execution, audit verification | **10 / 10 PASS** |
| **Secrets Scan** | Repository-wide | Zero credentials, private keys, or API tokens | **0 Leaks (Gitleaks)** |
| **Dependency Scans** | Python & Node | Zero known vulnerabilities | **0 CVEs (pip & npm)** |
| **Frontend Quality** | Next.js 16 | Zero lint warnings, zero TypeScript errors | **0 Errors / 0 Warnings** |

---

## 8. Quick Start (Running Locally)

### Option 1: Docker Compose (Recommended)

```bash
# Build and start PostgreSQL, Migrations, API, Worker, and Web Console
docker compose up --build
```
* Web Console: `http://localhost:3000`
* FastAPI Swagger UI: `http://localhost:8000/docs`
* Prometheus Metrics: `http://localhost:8000/metrics`
* Health Check: `http://localhost:8000/health`
* Readiness Check: `http://localhost:8000/ready`

### Option 2: Local Native Setup

```bash
# 1. Virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Database migrations (requires running PostgreSQL)
alembic upgrade head

# 3. Start backend services
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 &
python apps/worker/main.py --interval 3 &

# 4. Start frontend console
npm run dev --prefix apps/web
# Open: http://127.0.0.1:3000
```

---

## 9. Verification Commands

Run the full verification suite directly from a clean checkout:

```bash
# 1. Python dependency check & vulnerability audit
pip check
pip-audit -r requirements.txt

# 2. Database migration check (ensures zero drift)
alembic check

# 3. Full Pytest suite
pytest -q

# 4. Individual critical test suites
pytest tests/invariants/ -v
pytest tests/concurrency/ -v
pytest tests/failure/ -v
pytest tests/security/ -v

# 5. Frontend lint, production build, and security audit
npm run lint --prefix apps/web
npm run build:web
npm audit --prefix apps/web

# 6. Playwright browser E2E test suite
npm run e2e

# 7. Repository secrets scan
gitleaks dir . --redact --verbose

# 8. Production Docker compose fail-closed validation
# (Must exit with code 1 when required secrets are missing)
docker compose -f docker-compose.prod.yml config
```

---

## 10. Gateway Modes: Simulation vs. Sandbox vs. Live

RAY separates payment execution environments through explicit configuration:

* **SIMULATION**: Internal deterministic mock engine. Simulates gateway network timeouts, bank decline codes (`INSUFFICIENT_FUNDS`, `DO_NOT_HONOR`), and webhook events without external network dependencies.
* **SANDBOX**: Connects to the Razorpay test API (`rzp_test_...`). Validates payload serialization, webhook signature verification, and acquirer error formatting.
* **LIVE**: Real monetary execution against live card networks. Requires explicit dual-key authorization (`LIVE_EXECUTION_ENABLED=true` and `STAGE_1_SAFETY_LOCK=false`), valid production API keys, and production webhook signing secrets. Fails closed by default.

---

## 11. Architectural Documentation Sitemap

* **[Architecture Reference](file:///Users/ayushtripathi/Ray/docs/architecture.md)**: Request flows, database schema, and entity relationships.
* **[Interview & Architecture Defense Guide](file:///Users/ayushtripathi/Ray/docs/INTERVIEW_GUIDE.md)**: Pitch scripts, concurrency deep-dive, trade-offs, and technical defense Q&A.
* **[STRIDE Threat Model](file:///Users/ayushtripathi/Ray/docs/THREAT_MODEL.md)**: Threat evaluation across 23 primary financial attack surfaces.
* **[Security Architecture](file:///Users/ayushtripathi/Ray/docs/security.md)**: Cryptographic audit chaining, RBAC, and secret management.
* **[Interactive Demo Runbook](file:///Users/ayushtripathi/Ray/docs/DEMO.md)**: Step-by-step deterministic demo script for technical reviewers.
* **[Developer Guide](file:///Users/ayushtripathi/Ray/docs/DEVELOPMENT.md)**: Local setup, testing conventions, and contribution standards.
* **[Production Deployment](file:///Users/ayushtripathi/Ray/docs/DEPLOYMENT.md)**: Docker multi-stage builds, non-root users, and secret injection.
* **[Disaster Recovery](file:///Users/ayushtripathi/Ray/docs/DISASTER_RECOVERY.md)**: Backup procedures, emergency SQL kill switch, and reconciliation.
