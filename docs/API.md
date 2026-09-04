# RAY Control Plane — Developer API Specification

**Document Version**: 2.0.0-PROD  
**Specification**: OpenAPI 3.0.3 Compatible  
**Base URL**: `/api/v1`  
**OpenAPI Full JSON**: [`docs/openapi.json`](file:///Users/ayushtripathi/Ray/docs/openapi.json)

---

## 1. Authentication & Security Headers

RAY APIs enforce Bearer Token authentication and RBAC:

```http
Authorization: Bearer <MERCHANT_ADMIN_OR_API_TOKEN>
X-Correlation-ID: <UUIDv4>
Content-Type: application/json
```

### Roles & Permissions:
- `Role.MERCHANT_ADMIN`: Full access to configuration, decision triggers, audit, and admin controls.
- `Role.OPERATOR`: Operational status, telemetry, metrics, and reconciliation triggers.
- `Role.ANALYST`: Read-only access to dashboard, payments, receipts, and audit history.

---

## 2. Core Endpoint Index

### 1. Ingestion & Webhooks
- `POST /api/v1/webhooks/razorpay`: Ingest inbound gateway events. Requires `X-Razorpay-Signature` and `X-Razorpay-Timestamp`.

### 2. Money Graph & Opportunities
- `GET /api/v1/dashboard`: High-level recovery metrics, failure breakdowns, and recent events.
- `GET /api/v1/opportunities`: Paginated list of derived recovery opportunities.
- `GET /api/v1/opportunities/{id}`: Detailed opportunity explanation, score factors, and confidence intervals.

### 3. Decisions & Actions
- `POST /api/v1/decisions/trigger`: Trigger canonical decision workflow for a failed payment.
- `POST /api/v1/actions/execute`: Execute a recovery action idempotently. Requires `idempotency_key`.

### 4. Reconciliation & Payments
- `GET /api/v1/payments/{id}`: Retrieve authoritative payment details, customer graph, and attempts.
- `POST /api/v1/payments/{id}/reconcile`: Trigger authoritative outcome reconciliation for ambiguous states.

### 5. Audit & Receipts
- `GET /api/v1/audit/events`: Retrieve cryptographically chained audit records.
- `GET /api/v1/audit/verify`: Validate integrity of the SHA-256 hash chain for the current merchant.
- `GET /api/v1/receipts/{id}`: Retrieve immutable Decision Receipt.

### 6. Administration & Governance
- `GET /api/v1/admin/merchants`: List all registered merchants and gateway bindings.
- `GET /api/v1/admin/stage2/status`: Inspect Stage 1 vs Stage 2 live money movement authorization.
- `POST /api/v1/admin/stage2/activate`: Dual-key administrative activation of Stage 2.
- `POST /api/v1/admin/kill-switch`: Emergency fail-closed shutdown forcing Stage 1 lock.
- `GET /api/v1/merchants/config`: Read active hierarchical merchant policy configuration.
- `PUT /api/v1/merchants/config`: Update merchant velocity limits and enabled strategies.

### 7. Observability & System Probes
- `GET /health`: Platform liveness probe.
- `GET /ready`: Database and worker readiness probe.
- `GET /metrics`: Standard Prometheus-formatted text metrics.
- `GET /api/v1/operations/status`: Real-time telemetry for operations dashboard.
