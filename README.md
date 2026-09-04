# RAY — Merchant Revenue Recovery Control Plane

[![Build & Verification Status](https://img.shields.io/badge/CI%2FCD-passing-emerald?style=flat-square&logo=githubactions)](https://github.com/ayushtripathi/Ray/actions)
[![Pytest Suite](https://img.shields.io/badge/pytest-334%20passed-emerald?style=flat-square&logo=pytest)](file:///Users/ayushtripathi/Ray/tests)
[![Financial Invariants](https://img.shields.io/badge/invariants-54%20passed-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/tests/invariants)
[![Failure & Concurrency](https://img.shields.io/badge/concurrency-31%20passed-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/tests/concurrency)
[![Playwright E2E](https://img.shields.io/badge/Playwright%20E2E-10%20passed-emerald?style=flat-square&logo=playwright)](file:///Users/ayushtripathi/Ray/apps/web/e2e)
[![Security Audits](https://img.shields.io/badge/gitleaks%20%7C%20pip--audit%20%7C%20npm%20audit-0%20vulnerabilities-emerald?style=flat-square)](file:///Users/ayushtripathi/Ray/docs/SECURITY.md)
[![License: Institutional FinTech](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](file:///Users/ayushtripathi/Ray/LICENSE)

> **Institutional-Grade Financial Control Plane for Autonomous Merchant Revenue Recovery.**  
> Built with FastAPI, PostgreSQL 16, SQLAlchemy 2.0, Next.js 16, TypeScript, Tailwind CSS, PostgreSQL Advisory Locks, Transactional Outbox, Razorpay Integration Boundary, and Continuous SHA-256 Hash Chaining.

---

## 1. Executive Summary & Problem Statement

In high-volume e-commerce and merchant acquiring, **15% to 22% of all payment transactions decline**. While a portion are hard declines (e.g. `CARD_EXPIRED`, `FRAUD_SUSPECTED`), the majority stem from transient failure modes: acquiring bank connection timeouts, 3DS authentication dropped sockets, network congestion, and momentary balance issues.

### The Critical Industry Flaws in Existing Solutions:
1. **Naive Automated Retries**: Blindly retrying failed transactions creates catastrophic duplicate debit liabilities, disputes, and card-scheme fines (e.g. Visa Excessive Retry Merchant Assessment).
2. **The Socket Timeout Ambiguity Trap**: When an HTTP gateway connection drops mid-flight after 10 seconds, naive systems assume the payment `FAILED` and recharge the customer. In reality, the charge often settled at the card network, creating an unauthorized double-charge.
3. **Opaque "AI Agents" Moving Real Money**: Granting generative AI models direct debit authority invites prompt injection, non-deterministic hallucination, and regulatory non-compliance.
4. **Fragile In-Memory Locking**: Using in-memory maps or disjoint Redis locks (`Redlock`) causes split-brain execution when worker pods restart or experience network partitions.

**RAY eliminates revenue leakage while enforcing mathematical, non-negotiable financial safety invariants.**

---

## 2. The 5-Layer Operational Boundary

RAY guarantees that every dollar recovered adheres to a rigid five-layer architectural boundary:

```
[ 1. AI Recommends ]
       │  (Analyzes decline telemetry & proposes remediating strategy e.g. SMART_RETRY_WINDOW)
       ▼
[ 2. Deterministic Policy Authorizes ]
       │  (Rigid velocity limits, retry caps, fraud zero-tolerance rules; AI HAS ZERO EXECUTION RIGHTS)
       ▼
[ 3. Deterministic Action Layer Executes ]
       │  (PostgreSQL advisory transaction locks, transactional outbox, fail-closed Stage 1 safety lock)
       ▼
[ 4. Outcome Verification Confirms Reality ]
       │  (Authoritative reconciliation: UNKNOWN != FAILED, HMAC webhook settlement verification)
       ▼
[ 5. Immutable Audit Records Everything ]
          (Continuous append-only SHA-256 cryptographic parent-hash chain)
```

---

## 3. High-Level Architecture & Request Flow

```mermaid
graph TD
    Client([Merchant Web Console / Browser]) <-->|HTTPS / TLS 1.3| NGINX[NGINX Reverse Proxy :80/:443]
    Acquirer([Acquiring Gateway / Razorpay]) <-->|HMAC Webhooks & REST| NGINX

    subgraph DOCKER_COMPOSE["Ray Isolated Network (ray_network)"]
        NGINX -->|Route / | WebApp[Next.js 16 Web Console :3000]
        NGINX -->|Route /api/ | API[FastAPI Control Plane :8000]

        subgraph DOMAIN_SERVICES["Core Financial Services"]
            API -->|ACID Transactions| DB[(PostgreSQL 16 Engine :5432)]
            Worker[Transactional Outbox Worker] -->|Poll Outbox FOR UPDATE SKIP LOCKED| DB
            Worker -->|Idempotent Gateway Dispatch| GatewayAdapter[Gateway Adapter Boundary]
        end
    end

    GatewayAdapter -.->|Mode: SIMULATION| SimEngine[Internal Simulation Engine]
    GatewayAdapter -.->|Mode: SANDBOX| RzpSandbox[Razorpay Sandbox API]
    GatewayAdapter -.->|Mode: LIVE (Strict Opt-In)| RzpLive[Razorpay Live API]
```

---

## 4. Non-Negotiable Core Engineering Invariants

| Architectural Principle | Implementation & Defense Mechanism |
| :--- | :--- |
| **UNKNOWN ≠ FAILED** | Network socket drops transition transactions to `UNKNOWN`. Automated retries are strictly prohibited until authoritative reconciliation verifies reality. |
| **Distributed Idempotency** | PostgreSQL transaction-scoped advisory locks (`pg_try_advisory_xact_lock(hash(merchant_id, idempotency_key))`). Concurrent in-flight races return structured **HTTP 409 Conflict**. |
| **Transactional Outbox** | State changes and outbound events are written to PostgreSQL in the *exact same ACID transaction*. Background workers drain the queue using `SELECT ... FOR UPDATE SKIP LOCKED`. |
| **Zero Float Arithmetic** | Financial balances and amounts are stored and calculated strictly using `Decimal` / `NUMERIC(18, 4)`. Floating point arithmetic is banned by automated AST inspection tests. |
| **AI Has Zero Execution Authority** | Machine models provide advisory strategy scores. All execution rights belong strictly to the deterministic policy engine. |
| **Fail-Closed Kill Switch** | The emergency kill switch is authoritative in PostgreSQL and checked on every execution step. If the database drops, the executor fails closed. |
| **Cryptographic Audit Chain** | Every event contains `event_hash = SHA256(previous_hash + payload + sequence + timestamp)`. Tampering with any historical record breaks the chain. |
| **Safe Gateway Defaults** | `LIVE_EXECUTION_ENABLED=false` is the hardcoded default. Accidental live money movement is physically impossible without dual-key configuration. |

---

## 5. Technology Stack

* **Control Plane API**: Python 3.12/3.13, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
* **Relational Database**: PostgreSQL 16 (Foreign keys, transaction-scoped advisory locks, unique constraints)
* **Frontend Operations Console**: Next.js 16 (Turbopack, App Router), React 19, TypeScript, Tailwind CSS v4, Lucide Icons
* **Testing Frameworks**: Pytest (Unit, Invariant, Concurrency), Playwright (Browser E2E)
* **Infrastructure & Security**: Docker, Docker Compose, NGINX, Gitleaks, pip-audit, npm audit, Prometheus metrics

---

## 6. Verification & Quality Matrix

| Test Suite | Scope | Target Invariant | Result |
| :--- | :--- | :--- | :--- |
| **Full Pytest Suite** | 334 tests | Models, APIs, state machines, outbox dispatch | **334 / 334 PASS** |
| **Financial Invariants** | 54 tests | Zero float math, monotonic scoring, tenant isolation | **54 / 54 PASS** |
| **Concurrency & Resilience** | 31 tests | 100-thread races, advisory locks, fail-closed kill switch | **31 / 31 PASS** |
| **Gateway Modes Suite** | 11 tests | SIMULATION, SANDBOX, LIVE boundary isolation | **11 / 11 PASS** |
| **Browser E2E Suite** | 10 journeys (25 criteria) | Real API integration, telemetry, empty states, zero 500s | **10 / 10 PASS** |
| **Secrets Audit** | Whole tree | Zero leaked credentials or private keys | **0 Leaks (Gitleaks)** |
| **Dependency Audits** | Python & Node | Zero known vulnerabilities | **0 CVEs (pip & npm)** |
| **Frontend Lint & Build** | Next.js 16 | Zero lint warnings, zero TypeScript errors | **0 Errors / 0 Warnings** |

---

## 7. Quick Start (Run Locally in 2 Minutes)

### Option 1: Docker Compose (Recommended)
```bash
# Build and start all 5 services: PostgreSQL, Migrations, API, Worker, Web
docker compose up --build
```
* Control Console: `http://localhost:3000`
* FastAPI Swagger Docs: `http://localhost:8000/docs`
* Prometheus Telemetry: `http://localhost:8000/metrics`

### Option 2: Local Native Setup
```bash
# 1. Virtual Environment & Dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Database Migrations
alembic upgrade head

# 3. Start Backend Services
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 &
python apps/worker/main.py --interval 3 &

# 4. Start Frontend Console
npm run dev --prefix apps/web
# Open: http://127.0.0.1:3000
```

---

## 8. Verification Commands

Run the full verification suite directly from a clean checkout:

```bash
# 1. Full Pytest Suite
pytest -q

# 2. Financial Invariants Suite
pytest tests/invariants/ -v

# 3. Failure & Concurrency Races (100-thread adversarial)
pytest tests/failure/ tests/concurrency/ -v

# 4. Gateway Modes Integration
pytest tests/integration/test_gateway_modes.py -v

# 5. Playwright Browser E2E Test Suite
npm run e2e

# 6. Security Scans
gitleaks dir . --redact --verbose
.venv/bin/pip check
.venv/bin/pip-audit
npm audit --prefix apps/web

# 7. Frontend Lint & Production Build
npm run lint --prefix apps/web
npm run build:web
```

---

## 9. Comprehensive Documentation Sitemap

* **[Interview & Architecture Defense Guide](file:///Users/ayushtripathi/Ray/docs/INTERVIEW_GUIDE.md)**: 30-second, 2-minute, and 5-minute pitches; concurrency deep-dive; database design; tradeoffs; and interview Q&A.
* **[STRIDE Threat Model](file:///Users/ayushtripathi/Ray/docs/THREAT_MODEL.md)**: Exhaustive threat evaluation across all 23 primary financial attack surfaces.
* **[Technical Architecture & Mermaid Diagrams](file:///Users/ayushtripathi/Ray/docs/ARCHITECTURE.md)**: 8 detailed sequence, flow, and structural diagrams.
* **[Interactive Demonstration Runbook](file:///Users/ayushtripathi/Ray/docs/DEMO.md)**: 18-step deterministic live presentation script.
* **[Developer Setup & Testing Guide](file:///Users/ayushtripathi/Ray/docs/DEVELOPMENT.md)**: Complete guide for contributors and local development.
* **[Production Deployment Guide](file:///Users/ayushtripathi/Ray/docs/DEPLOYMENT.md)**: Docker multi-stage builds, non-root users, and secret injection.
* **[Disaster Recovery Runbook](file:///Users/ayushtripathi/Ray/docs/DISASTER_RECOVERY.md)**: Automated backup scripts, manual SQL kill-switch procedures, and reconciliation.

---

## 10. Gateway Modes: Simulation vs. Sandbox vs. Live

RAY explicitly separates payment execution environments:
* **SIMULATION**: Internal deterministic mock engine. Generates realistic payloads, configurable timeouts, decline codes, and synthetic settlement telemetry without external network dependencies.
* **SANDBOX**: Communicates with Razorpay test APIs (`rzp_test_...`). Stage 1 safety lock defaults active to isolate test transactions.
* **LIVE**: Real monetary execution against live card networks (`rzp_live_...`). Requires explicit administrative dual-key authorization, `LIVE_EXECUTION_ENABLED=true`, and `STAGE_1_SAFETY_LOCK=false`.
