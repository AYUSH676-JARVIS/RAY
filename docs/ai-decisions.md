# AI Decision Boundaries & Agent Invariants

## 1. The Core Principle

> **AI Recommends. Deterministic Policy Authorizes. Deterministic Action Layer Executes.**

In RAY, autonomous agents and machine learning models are treated as **untrusted, high-intelligence advisors**. Under no circumstance is an LLM or statistical model permitted to execute a financial action, move money, or mutate merchant balances directly.

---

## 2. Why Direct LLM Financial Execution is Strictly Prohibited

Allowing an LLM to directly invoke payment gateway APIs introduces existential financial and operational vulnerabilities:

1. **Hallucination & Parameter Drift**: Probabilistic token generation can subtly corrupt decimal amounts, currency codes, or customer identifiers (e.g. attempting to charge $1,500.00 instead of $15.00).
2. **Prompt Injection & Adversarial Payloads**: Malicious actors can embed jailbreak payloads into customer names, billing addresses, or failure messages to manipulate an agent's logic.
3. **Absence of Idempotency Invariants**: LLM retries without cryptographic idempotency keys inevitably cause double-billing and duplicate capture attempts.
4. **Regulatory & Compliance Violations**: PCI-DSS and financial audit standards require strict determinism for all money movement.

---

## 3. Strict Layered Separation

```
┌─────────────────────────────────────────────────────────────┐
│                      AI RECOVERY AGENT                      │
│ - Reads payment failure graph                               │
│ - Analyzes card issuer patterns & decline codes             │
│ - Recommends strategy: SMART_RETRY, ROUTING_FALLBACK        │
│ - Output: Pure structured suggestion (RecoveryOpportunity)  │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 DETERMINISTIC POLICY ENGINE                 │
│ - Evaluates hard mathematical rules                         │
│ - Checks daily velocity limits (e.g. max 3 retries/day)     │
│ - Verifies customer risk score < threshold                  │
│ - Blocks fraud, expired cards, and closed accounts          │
│ - Output: PolicyDecision (APPROVED | REJECTED | FLAGGED)    │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 DETERMINISTIC ACTION LAYER                  │
│ - Checks Stage 1 Execution Safety Lock                      │
│ - Validates and enforces idempotency key uniqueness         │
│ - Calls gateway execution SDKs with strict timeouts         │
│ - Output: Execution receipt or BLOCKED_STAGE1_SAFETY record │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Agent Tool Call Sandboxing

All agent tool calls are executed through the `ToolCall` registry with:
- **Explicit Input/Output Schemas**: Validated with Pydantic v2 schemas before execution.
- **Latency & Cost Tracking**: Every tool invocation records duration in milliseconds and dollar cost.
- **Immutable Persistence**: Recorded into the `tool_calls` table and linked to the parent `AgentRun`.

---

## 5. Phase 1 Safety Invariant

During Phase 1 (current buildathon stage), the deterministic action layer contains an explicit architectural lock (`BLOCKED_STAGE1_SAFETY`). All proposed recovery actions remain in a safe, non-executed state while all analytical, policy, and audit pipelines operate with complete real-world fidelity.

---

## 6. Pluggable Decision Provider Architecture

To ensure unyielding determinism while enabling future LLM integrations, the Financial Opportunity Engine separates decision evaluation behind a pluggable interface:

```
                  ┌────────────────────────┐
                  │    DecisionProvider    │  (Abstract Base Class)
                  └───────────┬────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
  ┌───────────────────────┐       ┌───────────────────────┐
  │ DeterministicProvider │       │   AIDecisionProvider  │
  │ (Active Default)      │       │   (Extension Hook)    │
  │ - Strict heuristics   │       │ - Future prompt logic │
  │ - Exact math formulas │       │ - Natural language    │
  │ - Zero hallucination  │       │ - Sandboxed advice    │
  └───────────────────────┘       └───────────────────────┘
```

### Strict Non-Negotiable AI Boundaries
Under no circumstances may an AI or LLM component:
1. Perform expected financial value calculations.
2. Determine merchant velocity eligibility.
3. Grant authorization for financial movements.
4. Modify transaction states or ledger balances.
5. Bypass retry attempt counters or idempotency keys.
6. Trigger direct API calls to payment gateways.

