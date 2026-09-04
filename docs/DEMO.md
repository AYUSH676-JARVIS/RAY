# RAY Merchant Revenue Recovery Control Plane — Live Demonstration Runbook

> **Demonstration Goal:** Provide an interviewer or engineering leader with a deterministic, repeatable, 10-minute walkthrough of RAY's core financial controls, AI safety boundary, and distributed systems resilience.

---

## 1. Quick Startup (One-Command Developer Launch)

### Option A: Local Native Mode (Fastest for inspection)
```bash
# Terminal 1: Start PostgreSQL (ensure local postgres is on port 5432)
# Apply migrations:
alembic upgrade head

# Terminal 2: Start FastAPI Backend
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 3: Start Outbox Worker Daemon
python apps/worker/main.py --interval 3

# Terminal 4: Start Frontend Console
npm run dev --prefix apps/web
# Open: http://127.0.0.1:3000
```

### Option B: Docker Production Mode
```bash
docker compose up --build
# Services automatically initialize: PostgreSQL -> Migrations -> API -> Worker -> Web
# Open: http://127.0.0.1:3000
```

---

## 2. 18-Step Deterministic Interview Walkthrough

### Step 1: Invariant Status Ribbon & Gateways (Top Navigation Bar)
* **Action:** Open `http://127.0.0.1:3000`.
* **Inspect:** The top telemetry ribbon highlights:
  - `GATEWAY: SIMULATION` (Safety boundary active)
  - `STAGE 1 SAFETY LOCK ACTIVE` (Real money movement locked)
  - `CONTINUOUS SHA-256 AUDIT CHAIN`
  - `MULTI-TENANT ISOLATION`
* **Talking Point:** *"Every node in the cluster enforces fail-closed defaults. Even if an engineer misconfigures an environment variable, Stage 1 lock actively prevents live gateway calls."*

---

### Step 2: Overview Dashboard & Recovery Telemetry
* **Action:** Click the **Overview** tab.
* **Inspect:** 
  - **Processed Volume & Decline Taxonomy**: Real-time breakdown of declines across 8 categories (e.g. `BANK_TIMEOUT`, `CARD_EXPIRED`, `FRAUD_SUSPECTED`).
  - **5-Layer Operational Boundary Card**: AI Recommends → Policy Authorizes → Action Executes → Verification → Audit.
* **Talking Point:** *"Notice that currency values are strictly formatted from fixed-precision decimals. No floating point arithmetic is used in the database or backend."*

---

### Step 3: Triggering the Autonomous Decision Pipeline
* **Action:** Click the **Decisions** tab.
* **Inspect:** Autonomous Decision Pipeline with scenario selector:
  - Select: `Scenario A — Healthy Recovery (₹2,500 Bank Timeout)`
  - Click: **Execute Pipeline**
* **Result:** The 9-stage pipeline visualizer renders the live execution flow:
  `EVENT → MONEY GRAPH → OPPORTUNITY → DECISION → POLICY → ACTION → VERIFICATION → AUDIT → RECEIPT`
* **Talking Point:** *"The pipeline generates a tamper-evident Decision Receipt linking the decline code directly to the mathematical recovery rationale."*

---

### Step 4: Demonstrating the AI Safety Boundary (No Direct Authority)
* **Action:** In the Decisions visualizer, expand the **Decision & Strategy** stage.
* **Inspect:** 
  - The AI model proposed `WAIT_AND_RETRY` with a 90% win-rate confidence.
  - The deterministic policy engine independently verified velocity limits and granted clearance.
* **Talking Point:** *"If the AI model had hallucinated an invalid amount or attempted to retry a hard fraud decline, the deterministic policy engine would immediately intercept and abort the action before any gateway call."*

---

### Step 5: Fraud Zero-Tolerance Policy Block
* **Action:** Select `Scenario B — Fraud Zero-Tolerance Block (Policy Blocked)`.
* **Action:** Click **Execute Pipeline**.
* **Result:** Stage 4 (Policy Gate) evaluates to `BLOCKED_POLICY`. The Action stage is safely skipped.
* **Talking Point:** *"Zero gateway calls are made when policy denies an action. This is verified by our financial invariant tests."*

---

### Step 6: Attempting Stage 1 Financial Execution (Safety Block)
* **Action:** Navigate to the **Actions** tab.
* **Inspect:** Click **Refresh Ledger**. Locate any action and inspect its status: `BLOCKED_STAGE1_SAFETY`.
* **Talking Point:** *"In Stage 1 mode, actions simulate execution or halt at the gateway boundary with HTTP 423 Locked / BLOCKED_STAGE1_SAFETY. Moving to Stage 2 requires dual-key cryptographic administrator authorization."*

---

### Step 7: Demonstrating Distributed Idempotency & Concurrency Protection
* **Action:** Execute the concurrent idempotency test in your terminal:
  ```bash
  pytest tests/concurrency/test_concurrency_races.py -v
  ```
* **Result:** Two concurrent threads submitting identical idempotency keys execute exactly once. The second thread receives a structured **HTTP 409 Conflict** (`ConcurrentExecutionBlockedError`).
* **Talking Point:** *"We use PostgreSQL advisory transaction locks (`pg_try_advisory_xact_lock`). Unlike Redis Redlock, advisory locks are natively bound to the database transaction lifecycle, eliminating orphaned locks."*

---

### Step 8: Demonstrating Webhook Replay Protection
* **Action:** Run the webhook replay test:
  ```bash
  pytest tests/invariants/test_financial_execution_invariants.py -k "invariant_4" -v
  ```
* **Talking Point:** *"Inbound webhooks require constant-time HMAC-SHA256 signatures, reject payloads older than 300 seconds, and enforce database deduplication on gateway event IDs."*

---

### Step 9: Demonstrating UNKNOWN ≠ FAILED (Reconciliation Queue)
* **Action:** Click the **Reconciliation** tab in the web console.
* **Inspect:** The **Authoritative Payment Reconciliation** queue displays payments marked `UNKNOWN`.
* **Inspect Badge:** `UNKNOWN ≠ FAILED`.
* **Talking Point:** *"When an acquiring bank socket drops mid-flight, naive systems mark it as FAILED and retry, causing duplicate charges. RAY freezes the payment in UNKNOWN until authoritative settlement telemetry arrives."*

---

### Step 10: Cryptographic Audit Chain Verification
* **Action:** Click the **Audit** tab.
* **Action:** Click **Verify Hash Chain**.
* **Result:** A green banner displays: `CRYPTOGRAPHIC CHAIN VERIFIED — All sequential audit events cryptographically linked with unbroken SHA-256 hash signatures.`
* **Talking Point:** *"Each audit record links to its predecessor via SHA-256 parent hashing. If a database admin attempts to tamper with a single byte in any historical record, verification fails immediately."*

---

### Step 11: Operations Telemetry & Prometheus Metrics
* **Action:** Click the **Operations** tab.
* **Inspect:** Real runtime metrics: Outbox backlog, idempotency collisions, webhook success rate, database ping latency.
* **Action:** In your terminal, curl the live Prometheus endpoint:
  ```bash
  curl -s http://127.0.0.1:8000/metrics | head -n 30
  ```
* **Talking Point:** *"The `/metrics` endpoint exports genuine runtime telemetry for Prometheus and Datadog, with zero hardcoded values."*

---

### Step 12: Distributed Emergency Kill Switch
* **Action:** Click the **Administration** tab.
* **Action:** Click **Engage Emergency Kill Switch**.
* **Result:** A modal prompts for mandatory justification (e.g., *"Upstream acquirer outage detected"*).
* **Talking Point:** *"The kill switch is authoritative in PostgreSQL and fails closed. If database connectivity drops, all workers immediately halt money movement cluster-wide."*

---

### Step 13: Tenant Isolation Verification
* **Action:** In terminal, run:
  ```bash
  pytest tests/invariants/test_financial_execution_invariants.py -k "cross_tenant" -v
  ```
* **Talking Point:** *"Every API request and worker job is strictly scoped to the tenant's `merchant_id`. Cross-tenant queries are structurally prevented by tenant-bound foreign keys."*

---

### Step 14: Chaos Lab Failure Modes
* **Action:** Click the **Scenarios** tab.
* **Inspect:** The Chaos Lab interface showcasing all 12 failure modes across Network, Security, Integrity, and AI Safety.

---

### Step 15: Executing Browser E2E Test Suite
* **Action:** In terminal, execute:
  ```bash
  npm run e2e
  ```
* **Result:** All 10 Playwright tests pass in under 3 seconds, validating the entire critical user journey with zero browser console errors and zero HTTP 500s.
