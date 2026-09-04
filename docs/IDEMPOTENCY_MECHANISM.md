# RAY Control Plane — Idempotency Architecture & Chain of Custody

**Document Version**: 2.0.0-PROD  
**Classification**: Financial Concurrency & Idempotency Specification  

---

## 1. Idempotency Chain of Custody

To guarantee that duplicate network packets, retries, or malicious replay attacks never result in duplicate financial charges or state corruptions, RAY enforces an end-to-end, multi-tier idempotency chain:

```
[Client / Upstream Webhook]
             │
             ▼
(1) Webhook Event Ingestion Tier
    - Deduplicates on gateway event_id in `webhook_deliveries`
    - Acquires PostgreSQL advisory lock on `crc32(event_id)`
    - Replayed event IDs return cached HTTP 200 without executing workflow
             │
             ▼
(2) Workflow Orchestrator Tier
    - Generates deterministic action idempotency key:
      `idem_wf_{payment_id}_{attempt_number}`
    - Checks `recovery_actions` table for active or terminal execution records
             │
             ▼
(3) Action Executor Tier
    - Queries durable `idempotency_records` table:
      `SELECT ... WHERE merchant_id = :mid AND idempotency_key = :key FOR UPDATE`
    - If slot is already resolved: returns cached execution receipt (`idempotent_replay: true`)
    - If slot is new: claims slot atomically in database transaction
             │
             ▼
(4) Gateway Adapter Tier
    - Forwards idempotency key in HTTP headers to Razorpay:
      `X-Payout-Idempotency: <idempotency_key>`
    - In TEST mode: checks internal `observed_idempotency_keys`
             │
             ▼
[Razorpay Payment Infrastructure]
    - Razorpay API enforces server-side idempotency
```

---

## 2. Invariants Under Fault Injection

| Failure Condition | System Behavior | Safety Outcome |
| :--- | :--- | :--- |
| **Concurrent Duplicate Requests** | Advisory locks serialize callers; first caller executes, remaining 19 receive cached receipt. | Exactly 1 gateway invocation. |
| **Gateway Timeout (Ambiguous)** | Marks action `UNKNOWN`. Idempotency slot remains locked against blind retry. | Zero double-charges. Requires authoritative reconciliation. |
| **Worker Process Crash** | Transaction rolls back. Outbox task remains in `task_queue` to be picked up by another worker. | No orphan states. |
| **Webhook Out-of-Order Delivery** | Timestamp validation rejects stale timestamps (> 300s). State machine rejects illegal transitions. | Out-of-order state mutations blocked. |
| **Replayed Webhook** | Delivery record match returns idempotent HTTP 200 OK immediately. | Webhook processing skipped. |
