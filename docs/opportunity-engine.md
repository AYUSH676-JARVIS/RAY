# Financial Opportunity Engine Architecture

## 1. Overview & Core Mission

The **Financial Opportunity Engine** (`services/opportunities`) is the autonomous analytical core of RAY. Unlike traditional billing systems that rely on static retry schedules or pre-baked database rows, RAY **dynamically derives recovery opportunities in real time from connected merchant data**.

```
   ┌────────────────────────────────────────┐
   │            Failed Payment              │
   └───────────────────┬────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────┐
   │           Money Graph Context          │
   │  (Payment + Customer + Attempts + Hist)│
   └───────────────────┬────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────┐
   │          Failure Intelligence          │
   │ (Recoverability + Allowed Strategies)  │
   └───────────────────┬────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────┐
   │      Deterministic Decision Provider   │
   │ (Probability + EV + Costs + Score)     │
   └───────────────────┬────────────────────┘
                       │
                       ▼
   ┌────────────────────────────────────────┐
   │     Explainable Opportunity Result     │
   │  (Decision + Evidence + Inspectable)   │
   └────────────────────────────────────────┘
```

---

## 2. Failure Intelligence & Candidate Strategies

Payment declines are classified across the eight mandatory categories:

| Category | Recoverable? | Recommended Strategies | Blocked Strategies | Base Probability* | Urgency |
|---|---|---|---|---|---|
| `BANK_TIMEOUT` | **Yes** | `WAIT_AND_RETRY`, `RETRY_NOW`, `SEND_PAYMENT_LINK` | `UPDATE_PAYMENT_METHOD` | 0.85 | HIGH |
| `INSUFFICIENT_FUNDS` | **Yes** | `WAIT_AND_RETRY`, `SEND_PAYMENT_LINK`, `UPDATE_PAYMENT_METHOD` | `RETRY_NOW` (Burns attempts) | 0.72 | MEDIUM |
| `ISSUER_DECLINED` | **Yes** | `WAIT_AND_RETRY`, `UPDATE_PAYMENT_METHOD`, `SEND_PAYMENT_LINK` | `RETRY_NOW` | 0.60 | MEDIUM |
| `AUTHENTICATION_FAILED` | **Yes** | `SEND_PAYMENT_LINK`, `WAIT_AND_RETRY`, `UPDATE_PAYMENT_METHOD` | `RETRY_NOW` (Requires customer) | 0.75 | HIGH |
| `NETWORK_ERROR` | **Yes** | `RETRY_NOW`, `WAIT_AND_RETRY` | `UPDATE_PAYMENT_METHOD` | 0.90 | HIGH |
| `CARD_EXPIRED` | **No (Direct)** | `UPDATE_PAYMENT_METHOD`, `SEND_PAYMENT_LINK` | `RETRY_NOW`, `WAIT_AND_RETRY` | 0.65 (Outreach) | LOW |
| `LIMIT_EXCEEDED` | **Yes** | `WAIT_AND_RETRY`, `SEND_PAYMENT_LINK` | `RETRY_NOW` | 0.70 | MEDIUM |
| `FRAUD_SUSPECTED` | **No (Auto)** | `HUMAN_REVIEW`, `NO_ACTION` | `RETRY_NOW`, `WAIT_AND_RETRY`, all automated | 0.05 | HIGH |

> [!NOTE]
> **Prototype Calibration Notice**: Base recovery probabilities are calibrated baseline heuristics for prototype evaluation and synthetic verification. They are not claimed to be empirical production banking figures.

---

## 3. The Inspectable Opportunity Score

To eliminate opaque "magic numbers", the opportunity score is fully decomposable and inspectable:

$$\text{Opportunity Score} = (\text{EV} \times P_{\text{success}} \times \text{Confidence} \times U_{\text{urgency}}) - \text{Cost}_{\text{risk}} - \text{Cost}_{\text{action}}$$

- **Expected Financial Value ($\text{EV}$)**: $\text{Gross Recovery} - \text{Action Cost} - \text{Risk Cost}$
- **Success Probability ($P_{\text{success}}$)**: Derived from base category probability + customer loyalty bonus (up to +12%) - attempt decay (-15% per attempt) - customer risk penalty.
- **Confidence**: 0.75 - 0.95 depending on customer transaction history volume.
- **Urgency Multiplier ($U_{\text{urgency}}$)**: HIGH = 1.25, MEDIUM = 1.00, LOW = 0.75.
- **Risk Cost**: Deductions based on customer credit/fraud risk score tier.
- **Action Cost**: Network and gateway attempt costs ($0.30 gateway retry, $0.05 payment link, $4.50 human review).
- **Normalized Score**: Mapped deterministically to the $[0.00, 100.00]$ scale.

---

## 4. Explainable Output & Evidence

Every opportunity derived by the engine contains explicit empirical evidence originating from the Money Graph:

```json
{
  "decision": "WAIT_AND_RETRY",
  "confidence": 0.85,
  "expected_value": "420.92",
  "risk": "LOW",
  "urgency": "HIGH",
  "evidence": [
    "failure_category=BANK_TIMEOUT",
    "retry_count=1",
    "payment_amount=473.29 USD",
    "customer_risk_score=10.0",
    "previous_successful_payments=1",
    "previous_failed_payments=1",
    "customer_ltv=691.01",
    "customer_historical_retry_success_rate=0.5",
    "last_gateway_used=Stripe Direct"
  ],
  "alternatives_considered": [
    "RETRY_NOW",
    "SEND_PAYMENT_LINK"
  ],
  "reason": "Transient network/acquirer timeout for an established customer. Scheduled retry provides high probability of authorization."
}
```

---

## 5. Pluggable Decision Provider Architecture

To support future AI and LLM enhancements without compromising deterministic invariants, the engine implements a provider pattern:

```python
class DecisionProvider(abc.ABC):
    @abc.abstractmethod
    def evaluate(self, context: FullMoneyContext) -> OpportunityResult:
        pass

class DeterministicDecisionProvider(DecisionProvider):
    # Pure mathematical and heuristic rule engine (Default)
    ...

class AIDecisionProvider(DecisionProvider):
    # Pluggable extension hook for natural language reasoning
    ...
```

**AI Boundary Guarantee**: The LLM is never responsible for money calculations, velocity checks, policy enforcement, or execution. The deterministic provider remains the bedrock authority.
