# RAY Production Baseline Report

**Execution Timestamp:** 2026-09-02T06:26:00Z  
**Phase:** Phase 0 — Baseline Before Hardening  
**Target:** Clean Environment Audit of RAY Merchant Money Intelligence Engine  

---

## 1. Environment & Runtime Baseline

| Component | Version / Specification | Status |
|---|---|---|
| **1. Python Version** | Python 3.13.9 (`/Users/ayushtripathi/Ray/.venv`) | **PASS** |
| **2. Node Version** | Node v22.23.2, npm 10.9.2 | **PASS** |
| **3. PostgreSQL Version** | PostgreSQL 16.14 (Homebrew / Docker Compose 16-alpine) | **PASS** |
| **4. Backend Dependencies** | `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `psycopg`, `alembic`, `pandas`, `numpy`, `scikit-learn`, `xgboost`, `httpx` (`pip-audit`: 0 known vulnerabilities) | **PASS** |
| **5. Frontend Dependencies** | `next@16.3.4`, `react@19.2.8`, `lucide-react`, `tailwindcss@4` (`npm audit`: 0 known vulnerabilities) | **PASS** |
| **6. Test Framework** | `pytest 8.3.0` with `pytest-asyncio 0.24.0`, `anyio 4.14.2` | **PASS** |

---

## 2. Test, Build & Static Analysis Status

| Dimension | Measured State | Classification |
|---|---|---|
| **7. Current Test Count** | 36 test cases across 4 integration and 5 unit test suites | **PASS** |
| **8. Current Test Failures** | 0 test failures on current suite (`36 passed in 0.68s`) | **PASS** |
| **9. Current Lint/Type Status** | Python: Ruff detected 31 issues (unused imports, module-level imports, missing typing names in legacy modules). Frontend: ESLint detected 1 error (`react-hooks/set-state-in-effect` in `page.tsx:167`) and 4 warnings (unused Lucide icons). | **FAIL** |
| **10. Current Build Status** | Next.js production build (`npm run build`) succeeded in 370ms; Docker builds build successfully. | **PASS** |

---

## 3. Production Architecture & Security Audit

| Subsystem | Audit Finding | Status |
|---|---|---|
| **11. Database Initialization** | Uses direct `Base.metadata.create_all(bind=engine)` at startup in `init_db()`. Fails production requirement for versioned zero-downtime schema evolution. | **FAIL** |
| **12. Migration Status** | Alembic is installed in requirements, but `alembic.ini` and `migrations/` directory are completely absent. No versioned migration history exists. | **NOT IMPLEMENTED** |
| **13. Authentication Status** | No authentication middleware or token dependency on API routes. All endpoints (`/api/payments`, `/api/opportunities`, etc.) accept anonymous traffic. | **NOT IMPLEMENTED** |
| **14. Authorization Status** | No Role-Based Access Control (RBAC). Roles (`MERCHANT_ADMIN`, `OPERATOR`, `FINANCE`, `RISK_ANALYST`, `READ_ONLY`) and action permissions are absent. | **NOT IMPLEMENTED** |
| **15. Tenant Isolation Status** | Multi-tenancy is not enforced at the route handler level. Endpoints accept global IDs (`/api/payments/{id}`) without scoping against an authenticated principal's `merchant_id`. | **FAIL** |
| **16. Secrets Handling** | `.env` contains database connection string and credentials. Secrets are read via `os.getenv` without typed validation on startup; no check that fails fast if critical secrets are missing in production mode. | **PARTIAL** |
| **17. CORS Configuration** | `main.py` configures `CORSMiddleware` with `allow_origins=["*"]` combined with `allow_credentials=True`. This is an insecure CORS configuration for financial platforms. | **FAIL** |
| **18. Rate Limiting** | No rate limiting middleware on auth, detection, or execution endpoints. System is vulnerable to resource exhaustion or AI budget depletion. | **NOT IMPLEMENTED** |
| **19. Observability** | Basic Python `logging` used without structured JSON output, correlation IDs (`request_id`, `merchant_id`), or metrics counters. | **PARTIAL** |
| **20. Audit Integrity** | `AuditEvent` records exist in database, but no cryptographic hash chaining (`event_hash = H(prev_hash + payload)`) or `verify_audit_chain()` validation. | **PARTIAL** |
| **21. Idempotency** | Database enforces `UNIQUE` constraint on `idempotency_key`, but lacks atomic transaction cache/store that returns the cached receipt on duplicate requests. | **PARTIAL** |
| **22. Action Execution** | Governed by hardcoded Stage 1 lock (`BLOCKED_STAGE1_SAFETY`). Lacks formal `PaymentGateway` adapter interface (`SimulationGateway`, `RazorpayGateway`). | **PARTIAL** |
| **23. Outcome Verification** | `OutcomeReconciler` exists as a rudimentary string comparator stub; lacks evidence-based verification against gateway webhooks or settlement logs. | **PARTIAL** |
| **24. AI Integration** | `AIDecisionProvider` falls back directly to `DeterministicDecisionProvider`. No structured failure causality and recovery reasoning schema or prompt injection defense. | **PARTIAL** |
| **25. ML Integration** | Mock models with hardcoded weights and heuristic outputs. No formal train/validation/test split, calibration reporting, or reproducibility pipeline. | **PARTIAL** |
| **26. Deployment Readiness** | Root container user, wildcard CORS, unauthenticated endpoints, lack of migrations, and un-unified risk scale block production deployment. | **FAIL** |

---

## 4. Key Known Defects Identified

1. **Risk Score Scale Contradiction (Phase 1)**:
   - Synthetic generator outputs `Customer.risk_score ∈ [0, 100]`.
   - Policy engine compares `customer_risk_score > 0.65` (Scale 0.0 - 1.0).
   - Opportunity engine compares `customer.risk_score > 50.0` and `80.0`.
   - Result: Inconsistent contract across components.

2. **Database Migration Gap (Phase 2)**:
   - Reliance on `create_all()` prevents safe zero-downtime schema evolution and rollbacks.

3. **Tenant Boundary Leakage (Phase 3)**:
   - Any client can query any payment, order, or customer by UUID without tenant verification.

4. **Authentication & Authorization Vacuum (Phases 4 & 5)**:
   - Financial endpoints are completely public.

5. **Legacy vs Canonical Opportunity Engine (Phase 6)**:
   - `services/opportunities/detector.py` is duplicate/legacy compared to `services/opportunities/engine.py`.

---

## 5. Summary Baseline Scorecard

- **PASS**: 7 items (Python, Node, Postgres, Backend deps, Frontend deps, Pytest framework, Test pass rate)
- **PARTIAL**: 10 items (Secrets, Observability, Audit, Idempotency, Action execution, Outcome verification, AI integration, ML integration, Build status)
- **NOT IMPLEMENTED**: 4 items (Alembic migrations, Authentication, RBAC, Rate limiting)
- **FAIL**: 5 items (Linting status, Database initialization, Tenant isolation, CORS configuration, Deployment readiness)
