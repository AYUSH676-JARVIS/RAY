# RAY Control Plane — Demonstration & Scenarios Runbook

**Document Version**: 2.0.0-PROD  
**Classification**: Operational Demonstration & Engineering Review Guide  

---

## 1. Prerequisites & Startup

To run the complete system locally:

```bash
# Terminal 1: Backend API Daemon
source .venv/bin/activate
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Background Task Worker
source .venv/bin/activate
python -m apps.worker.main

# Terminal 3: Next.js Fintech Control Plane
cd apps/web && npm run dev
```

Navigate to: `http://localhost:3000`

---

## 2. Interactive Scenarios Guide

The control plane includes 6 deterministic scenarios executed against real backend workflows. Access them under the **Scenarios** navigation view:

### Scenario 1: Normal Recovery (Smart Routing)
- **Problem**: Inbound webhook reports payment decline due to issuing bank congestion.
- **Workflow Path**:
  - `EVENT`: Ingests payment failure ($199.99).
  - `MONEY_GRAPH`: Loads connected customer history (risk score: 0.07).
  - `OPPORTUNITY`: Generates recovery opportunity score (35.0).
  - `DECISION`: AI recommends `SMART_ROUTING` cascade.
  - `POLICY`: Policy engine clears velocity limits and daily attempt limits.
  - `ACTION`: Dispatches recovery action. Safely halts at Stage 1 lock with `BLOCKED_STAGE1_SAFETY`.
  - `AUDIT`: Hashes and chains event cryptographically.
  - `RECEIPT`: Issues Decision Receipt UUIDv5.

### Scenario 2: Fraud Block (Risk Violation)
- **Problem**: Cardholder risk score exceeds merchant safety threshold (risk score = 0.85).
- **Workflow Path**:
  - `OPPORTUNITY`: AI proposes recovery retry.
  - `POLICY`: Policy Engine blocks action under Rule 2 (`SUSPICIOUS_RISK_RULE`).
  - `ACTION`: Status set to `BLOCKED_POLICY`. Zero gateway calls executed.

### Scenario 3: Gateway Timeout & Reconciliation
- **Problem**: Gateway times out after 10 seconds mid-transaction.
- **Workflow Path**:
  - `ACTION`: Enters explicit `UNKNOWN` state. Blind retry is prohibited.
  - `RECONCILIATION`: Switch to the **Reconciliation** tab and click *Reconcile Authoritatively*.
  - `RESULT`: Authoritative gateway proof confirms settlement, transitioning payment to `SETTLED`.

### Scenario 4: Terminal Decline
- **Problem**: Issuing bank reports `INVALID_CARD_NUMBER` or `STOLEN_CARD`.
- **Workflow Path**:
  - `OPPORTUNITY`: Engine classifies error as terminal/non-retryable.
  - `RESULT`: Opportunity score is 0.0; no action is scheduled, avoiding wasted interchange fees.

### Scenario 5: Concurrent Duplicate Delivery
- **Problem**: Gateway delivers the same webhook twice within 20 milliseconds.
- **Workflow Path**:
  - PostgreSQL advisory lock and `webhook_deliveries` unique constraints absorb the duplicate.
  - Exactly one decision loop executes. Duplicate returns cached HTTP 200.

---

## 3. Control Plane Navigation (10 Views)

1. **Overview**: Executive recovery metrics, failure distribution, and active alerts.
2. **Payments**: Search payment UUIDs and view graph history.
3. **Opportunities**: Ledger of AI-derived recovery opportunities and score breakdowns.
4. **Decisions**: Live 13-stage decision pipeline visualizer with millisecond telemetry.
5. **Actions**: Execution ledger with idempotency keys and replay safety.
6. **Audit**: Cryptographically verified append-only SHA-256 hash chain console.
7. **Reconciliation**: Authoritative proof resolver for UNKNOWN ambiguous transactions.
8. **Operations**: Real-time database latency, background worker heartbeat, and outbox depth.
9. **Scenarios**: Chaos lab runner for testing all failure modes.
10. **Administration**: Stage 1/2 activation gates, emergency kill-switch, and merchant policies.
