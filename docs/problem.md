# The Payment Failure Problem in Modern Commerce

## 1. Executive Summary

Global e-commerce merchants lose between **2% and 5% of their total gross merchandise value (GMV)** to false-positive payment declines and unrecovered transaction failures. For a $100M GMV merchant, this represents $2M to $5M in unearned annual revenue leaking directly out of the checkout funnel.

The core challenge is not that payments fail — failures are inevitable in distributed financial networks. The failure is that **modern payment stacks handle declines with crude, uncoordinated heuristics** that either leak revenue or trigger severe card network penalties.

---

## 2. Anatomy of Payment Declines

In RAY, declines are structured into eight concrete failure categories:

| Category | Typical Cause | Recoverability | Optimal Remediation |
|---|---|---|---|
| `BANK_TIMEOUT` | Acquirer/Issuing bank latency spike (>5000ms) | **High (85-95%)** | Smart Retry Window (10-30 min delay) |
| `INSUFFICIENT_FUNDS` | Cardholder balance depletion (mid-month) | **Medium-High (70-80%)** | Payday-Optimized Retry (1st/15th of month or morning hours) |
| `ISSUER_DECLINED` | Generic "Do Not Honor" (Code 05) | **Medium (55-65%)** | Routing Cascade (fallback to secondary acquirer/MID) |
| `AUTHENTICATION_FAILED` | 3D-Secure challenge abandon or SMS delay | **High (75-85%)** | Auth Repair / Soft-decline friction-free step-up |
| `NETWORK_ERROR` | Gateway socket reset, connection drop | **Very High (90%+)** | Instant Network Retry with exponential backoff |
| `CARD_EXPIRED` | Expired card validity date (Code 54) | **Zero (Automated)** | Automated Customer Outreach / Account Updater |
| `LIMIT_EXCEEDED` | Daily cardholder spend velocity cap | **Medium (65-75%)** | Next-Day Retry (00:01 UTC post-reset) |
| `FRAUD_SUSPECTED` | High heuristic risk score / fraud alert | **Zero (Forbidden)** | Permanent Block / Step-up manual verification |

---

## 3. The Cost of "Dumb" Retries

Traditional merchant billing systems rely on naive retry rules (e.g., retry every failed transaction 3 times every 24 hours). This creates severe collateral damage:

1. **Issuer Downgrades & Penalties**: Visa and Mastercard penalize merchants that retry terminal decline codes (e.g., retrying expired cards or closed accounts incur category 2 non-compliance fees).
2. **Authorization Degradation**: Issuing banks' fraud ML models penalize merchants with low authorization-to-attempt ratios, causing healthy transactions to be flagged as fraud.
3. **Customer Churn**: Involuntary churn occurs when subscriptions are cancelled after consecutive blind retry failures.

---

## 4. How RAY Solves the Problem

RAY replaces blind retries with an **AI Revenue Recovery Control Plane**:
- **Diagnostic Triage**: Instantly maps decline codes into recoverable vs terminal buckets.
- **Expected Value Optimization**: ML models predict the optimal retry window and routing pathway to maximize authorization probability.
- **Deterministic Guardrails**: Hard limits ensure no transaction is retried beyond merchant velocity rules or against fraud-suspected cards.
- **Auditable Control**: Full transparency into every recovery decision with deterministic confirmation of settled funds.
