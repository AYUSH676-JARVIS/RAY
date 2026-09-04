# Money Graph Service Architecture

## 1. Overview

The **Money Graph** (`services/money_graph`) is the foundational contextual intelligence service in RAY. It unifies relational financial entities into a queryable, contextual knowledge graph representing the complete lifecycle of merchant commerce.

In accordance with RAY's core principle, **all intelligence and recovery derivation must originate from connected merchant data**. RAY does not rely on synthetic fixtures or hallucinated assumptions.

---

## 2. Relational Graph Structure

```
                  ┌──────────────┐
                  │   Merchant   │
                  └──────┬───────┘
                         │ 1:N
            ┌────────────┴────────────┐
            ▼                         ▼
     ┌──────────────┐          ┌──────────────┐
     │   Customer   │◄─────────┤    Order     │
     └──────┬───────┘   1:N    └──────┬───────┘
            │                         │
            │ 1:N                     │ 1:N
            └────────────┬────────────┘
                         ▼
                  ┌──────────────┐
                  │   Payment    │
                  └──────┬───────┘
                         │ 1:N
            ┌────────────┴────────────┐
            ▼                         ▼
 ┌─────────────────────┐    ┌─────────────────────┐
 │   PaymentAttempt    │    │   PaymentFailure    │
 │ (idempotency_key)   │    │  (8 decline codes)  │
 └──────────┬──────────┘    └──────────┬──────────┘
            │                          │
            └────────────┬─────────────┘
                         ▼
             ┌───────────────────────┐
             │  RecoveryOpportunity  │
             │   (dynamic context)   │
             └───────────────────────┘
```

---

## 3. Read-Only Graph Traversal API

The `MoneyGraphService` provides eight strictly read-only functions returning typed Pydantic v2 objects:

| Method | Return Type | Description |
|---|---|---|
| `get_payment_context(payment_id)` | `PaymentContext` | Core transaction details (amount, currency, status, timestamps). |
| `get_customer_context(customer_id)` | `CustomerContext` | Customer risk profile, lifetime value (LTV), and transaction volumes. |
| `get_order_context(order_id)` | `OrderContext` | Commercial order intent and purchase details. |
| `get_failure_context(payment_id)` | `FailureContext` | Diagnostic decline code, raw gateway message, and retryability flag. |
| `get_customer_payment_history(customer_id)` | `CustomerPaymentHistoryContext` | Historical retry success rate, total volume, and average ticket size. |
| `get_related_transactions(customer_id)` | `List[TransactionSummary]` | Chronological sequence of recent transactions across merchant orders. |
| `get_payment_attempt_history(payment_id)` | `List[PaymentAttemptContext]` | Gateway attempt timeline, latencies, and unique idempotency keys. |
| `get_full_money_context(payment_id)` | `FullMoneyContext` | Unified composite representation of all above entities for a transaction. |

---

## 4. The Non-Fabrication Invariant

The Money Graph enforces a strict non-fabrication rule:
- **No Hallucinated Data**: If a transaction succeeded and has no failure record, `failure` returns `None`. It is never populated with placeholder strings.
- **Explicit Unknown States**: When customer history or attempt metrics are unavailable, the system reports explicit zero/null states rather than synthetic defaults.
- **Read-Only Safety**: The service executes pure `SELECT` queries. It cannot insert, update, or delete transaction states or ledger balances.

---

## 5. API Exposure

The complete graph for any payment can be inspected via:
```http
GET /api/payments/{payment_id}/context
```

Example JSON response:
```json
{
  "payment": {
    "id": "15bbc557-75bd-44cd-8b42-d71d41ba565c",
    "amount": "473.29",
    "currency": "USD",
    "status": "FAILED"
  },
  "customer": {
    "id": "60b08a2a-5c69-4f09-9a9e-5d7994fb8f10",
    "lifetime_value": "691.01",
    "successful_payments": 1,
    "failed_payments": 1,
    "risk_score": 10.0
  },
  "failure": {
    "failure_code": "BANK_TIMEOUT",
    "raw_message": "Gateway connection timed out awaiting issuing bank ACQ-504 response.",
    "is_retryable": true
  },
  "history": {
    "retry_success_rate": 0.50,
    "average_ticket_size": "382.15"
  },
  "attempts": [
    {
      "attempt_number": 1,
      "idempotency_key": "idem_doc_example_001",
      "gateway_name": "Stripe Direct",
      "status": "FAILED",
      "latency_ms": 1820
    }
  ]
}
```
