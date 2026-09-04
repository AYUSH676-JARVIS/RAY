# RAY — Production Razorpay Gateway Integration
*Production Gateway Engineering Specification & Operational Guide*

---

## 1. Overview & Architecture

RAY connects with the Razorpay payment infrastructure via a hardened production adapter implementing the `PaymentGateway` abstraction. The integration strictly isolates live financial transactions, classifies retries, redacts secrets from logs and payloads, and guarantees that `UNKNOWN != FAILED` during network timeouts.

```
+-------------------------------------------------------------------------+
| RAY Action Layer (Deterministic Execution Boundary)                      |
+-------------------------------------------------------------------------+
                                    |
                    [Stage 1 Safety Lock Active?]
                       /                         \
                     YES                          NO
                     /                             \
        +-----------------------+       +------------------------------+
        | Sandbox Simulation    |       | Live Execution Enabled?      |
        | (Zero Money Movement) |       +------------------------------+
        +-----------------------+               /              \
                                              YES               NO
                                              /                  \
                             +------------------------+  +-------------------+
                             | Valid Credentials?     |  | Fail Closed       |
                             +------------------------+  | Block Execution   |
                                     /          \        +-------------------+
                                   YES           NO
                                   /              \
        +----------------------------------+  +------------------------------+
        | Authenticated HTTPS REST Client  |  | ConfigurationError Raised    |
        | (Connect: 5.0s, Read: 10.0s)     |  | Fail Closed Immediately     |
        | HTTP Basic Auth (Key:Secret)     |  +------------------------------+
        | Idempotency: X-Payout-Idempotency|
        +----------------------------------+
```

---

## 2. Environment Configuration

Production credentials must be provided via environment variables. **Credentials must never be hardcoded into source code or Git history.**

| Variable Name | Required in Live Mode | Description | Example / Format |
|---|---|---|---|
| `RAZORPAY_KEY_ID` | Yes | Merchant Razorpay Key ID | `rzp_live_xxxxxxxxxxxxxx` |
| `RAZORPAY_KEY_SECRET` | Yes | Merchant Razorpay Key Secret | `xxxxxxxxxxxxxxxxxxxxxxxx` |
| `RAZORPAY_WEBHOOK_SECRET` | Yes | Webhook verification HMAC secret | `whsec_xxxxxxxxxxxxxxxxx` |
| `RAZORPAY_LIVE_MODE` | No (default: false) | Explicit toggle for live HTTP calls | `true` / `false` |

---

## 3. Strict Fail-Closed Safety Rules

1. **Development & Test Safety**: By default, `live_execution_enabled=False` or `stage_1_safety_lock=True`. The adapter produces sandbox simulation responses and performs zero external network calls.
2. **Fail-Closed on Missing Credentials**: If `live_execution_enabled=True` and `stage_1_safety_lock=False`, the gateway verifies that `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are present and non-placeholder. If missing, `ConfigurationError` is raised immediately. It **never** silently falls back to simulation in live mode.
3. **Stage 1 Lock Enforcement**: Even if live credentials are provided, if `stage_1_safety_lock=True` is configured, calling live execution raises `Stage1ExecutionBlockedError`.

---

## 4. Operational Invariants & Error Handling

### Retry Classification
- **Retryable Errors** (`is_retryable=True`):
  - `GATEWAY_ERROR`, `SERVER_ERROR`, `NETWORK_ERROR`, `INTERNAL_SERVER_ERROR`, `SERVICE_UNAVAILABLE`, `ACQUIRER_TIMEOUT`.
- **Non-Retryable Errors** (`is_retryable=False`):
  - `BAD_REQUEST_ERROR`, `CARD_EXPIRED`, `INSUFFICIENT_FUNDS`, `INVALID_CARD`, `DO_NOT_HONOR`, `FRAUD_SUSPECTED`, `AUTHENTICATION_FAILED`.

### Ambiguous Timeout Invariant: UNKNOWN != FAILED
If a network socket drops or exceeds the read timeout (10.0s), the adapter catches `httpx.TimeoutException` and returns:
- `status`: `UNKNOWN`
- `raw_code`: `GATEWAY_TIMEOUT`
- `is_retryable`: `False` (Blind retries are blocked until authoritative status check or webhook reconciliation resolves the transaction).

### Secret Redaction
All returned payloads and logged attributes automatically sanitize:
`key_secret`, `card_number`, `cvv`, `token`, `password` $\rightarrow$ `[REDACTED]`.

---

## 5. Supported Operations

1. `execute_retry`: Dispatches authenticated recurring/subsequent authorization requests with `X-Payout-Idempotency` header.
2. `create_payment_link`: Generates hosted checkout links via `POST /payment_links` for customer payment updates.
3. `update_payment_method`: Initiates payment method mandate update workflows.
4. `query_status`: Dispatches authoritative status inquiry `GET /payments/{id}` to reconcile UNKNOWN transactions.
5. `verify_webhook_signature`: Performs constant-time HMAC-SHA256 signature verification (`hmac.compare_digest`).
6. `normalize_webhook_event`: Normalizes webhook payloads into canonical domain structures.
