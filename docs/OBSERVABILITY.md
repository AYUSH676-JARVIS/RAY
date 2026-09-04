# RAY Control Plane — Production Observability & Telemetry

**Document Version**: 2.0.0-PROD  
**Classification**: Observability, Telemetry & Monitoring Architecture  

---

## 1. Structured Logging & Correlation ID Propagation

All incoming requests and downstream workflows emit structured JSON logs formatted with ISO-8601 UTC timestamps, log levels, request IDs, correlation IDs, and latency metrics.

### Correlation ID Header
- Header: `X-Correlation-ID`
- Behavior: Extracted from inbound headers if present; generated as a cryptographically random UUIDv4 if missing.
- Context Propagation: Attached to log events, database transactions, outbox records, and outgoing gateway HTTP calls.

### Secret Value Redaction
Sensitive financial information is automatically masked in memory and on output:
- API Keys (`RAZORPAY_KEY_SECRET`, `JWT_SECRET_KEY`)
- Webhook HMAC Signatures (`X-Razorpay-Signature`)
- Customer Card / UPI Payment Identifiers

---

## 2. Prometheus Metrics (`/metrics` & `/api/v1/metrics`)

The application exposes standard Prometheus text metrics at `/metrics` and `/api/v1/metrics`.

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `ray_up` | Gauge | Platform API process availability status (1 = UP, 0 = DOWN). |
| `ray_database_up` | Gauge | Authoritative PostgreSQL database health probe. |
| `ray_database_query_latency_seconds` | Gauge | Latency (seconds) of the database readiness check. |
| `ray_payments_total` | Gauge | Total volume of tracked payments across all states. |
| `ray_outbox_backlog_total` | Gauge | Count of pending outbox tasks awaiting dispatch. |
| `ray_webhooks_received_total` | Gauge | Total cumulative inbound webhooks received. |
| `ray_stage_1_safety_lock` | Gauge | Safety lock indicator (1 = Failsafe Active, 0 = Stage 2 Live). |

---

## 3. Operational Health Endpoints

- `GET /health`: Liveness probe for Kubernetes / orchestrators. Returns JSON status and database connection state.
- `GET /ready`: Readiness probe verifying PostgreSQL write readiness and outbox queue availability.
- `GET /api/v1/operations/status`: Detailed platform telemetry consumable by the web control plane.

---

## 4. Alert Thresholds & Severity Levels

| Component | Condition | Severity | Action / Runbook |
| :--- | :--- | :--- | :--- |
| **Database Connectivity** | `ray_database_up == 0` for 1m | `P1 - CRITICAL` | Engage DB failover; check connection pool exhaustion. |
| **Outbox Backlog** | `ray_outbox_backlog_total > 500` for 5m | `P2 - HIGH` | Scale worker pods; verify queue dispatch consumers. |
| **Webhook Ingestion Drop** | `ray_webhooks_received_total` stalls | `P2 - HIGH` | Verify reverse proxy TLS certificates and gateway webhooks. |
| **Stage 2 Unauthorized Shift** | `ray_stage_1_safety_lock == 0` unapproved | `P0 - EMERGENCY` | Automatic kill-switch triggers; alert security team. |
