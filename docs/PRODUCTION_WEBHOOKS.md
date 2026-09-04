# RAY — Production Webhook Architecture
*Production Webhook Ingestion & Security Manual*

---

## 1. Webhook Pipeline Architecture

Inbound external payment gateway events enter through authenticated, cryptographically signed HTTP webhooks. The webhook pipeline guarantees idempotency, replay defense, multi-tenant isolation, state-machine validation, and durable transactional outbox delivery.

```
Incoming Webhook POST /api/v1/webhooks/razorpay
                      |
                      v
       [1. Read Raw Binary Request Body]
                      |
                      v
       [2. Constant-Time HMAC-SHA256 Check] ---> [Invalid / Missing / Malformed] ---> HTTP 401
                      |
                      v
       [3. Timestamp Replay Window (<=300s)] ---> [Expired / Stale Header]        ---> HTTP 400
                      |
                      v
       [4. Parse JSON & Compute Payload Hash] ---> [Malformed JSON Payload]       ---> HTTP 400
                      |
                      v
       [5. Event Deduplication (Idempotency)]
                      |
           [Event ID Already Exists?]
                 /         \
               YES          NO
               /             \
    [Harmless HTTP 200]    [6. Create WebhookDelivery Record (RECEIVED)]
    (is_duplicate=True)       |
                              v
                   [7. Resolve Merchant Tenant] ---> [Tenant Boundary Mismatch]   ---> HTTP 400
                              |
                              v
                   [8. Normalize Domain Event]
                              |
                              v
                   [9. Validate State Machine Transition] ---> [Out of Order]     ---> HTTP 400
                              |
                              v
                   [10. Atomic Domain Update & Outbox Record]
                              |
                   [11. Trigger Decision Workflow (if failure)]
                              |
                   [12. Cryptographic Audit Log (SHA-256 Chain)]
                              |
                              v
                   HTTP 200 { success: true, event_id, status: "PROCESSED" }
```

---

## 2. Inbound Endpoints

- Production v1 Endpoint: `POST /api/v1/webhooks/razorpay`
- Generic Gateway v1 Endpoint: `POST /api/v1/webhooks/{gateway_name}`
- Legacy Compatibility Endpoint: `POST /api/webhooks/{gateway_name}`

Query Parameters:
- `merchant_id` (optional `UUID`): Enforces merchant tenant boundary validation.

Headers Required:
- Signature: `X-Razorpay-Signature` (or `X-Webhook-Signature`, `X-Hub-Signature-256`)
- Timestamp: `X-Webhook-Timestamp` (or `X-Razorpay-Timestamp`)

---

## 3. Cryptographic Security Standards

1. **HMAC-SHA256 Verification**:
   - Signature computed as `HMAC-SHA256(secret, raw_body_bytes)`.
   - Constant-time comparison via `hmac.compare_digest(expected, received)` prevents side-channel timing attacks.
   - Format enforced: 64-character lowercase hex string (`^[a-fA-F0-9]{64}$`).
2. **Replay Window Tolerance**:
   - Clock skew and freshness tolerance is bounded to 300 seconds (5 minutes). Stale or future-drifted timestamps trigger `ExpiredTimestampError` (HTTP 400).
3. **Event Deduplication**:
   - `WebhookDelivery` enforces a unique constraint on `(gateway_name, event_id)`.
   - Subsequent deliveries of identical events are recognized atomically, returning HTTP 200 with `{"is_duplicate": true}` without re-executing state mutations or workflows.
4. **Secret Redaction**:
   - Webhook logging redacts sensitive card numbers, security tokens, CVVs, and merchant secrets (`[REDACTED]`).
