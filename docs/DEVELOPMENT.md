# RAY Merchant Revenue Recovery Control Plane — Developer Guide & Local Setup

> **Target Audience:** Core engineers, contributors, and technical evaluators setting up local development, running automated test suites, and debugging components.

---

## 1. Prerequisites
* **Python**: 3.11+ (recommended 3.12 or 3.13)
* **Node.js**: 20+ (LTS)
* **PostgreSQL**: 15+ (local instance on port 5432 or Docker container)
* **Docker & Docker Compose**: v2.20+

---

## 2. Environment Setup

### 1. Clone & Create Virtual Environment
```bash
git clone https://github.com/ayushtripathi/Ray.git
cd Ray

# Python Virtual Environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy the template environment file:
```bash
cp .env.example .env
```

Key environment configurations:
```ini
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ray_db
API_HOST=127.0.0.1
API_PORT=8000
LIVE_EXECUTION_ENABLED=false
STAGE_1_SAFETY_LOCK=true
RAZORPAY_KEY_ID=rzp_test_sampleKey123
RAZORPAY_KEY_SECRET=sample_secret_for_development
RAZORPAY_WEBHOOK_SECRET=sample_webhook_secret_dev
```

### 3. Initialize Database Migrations
Ensure PostgreSQL is running, then run Alembic:
```bash
alembic upgrade head
```

To verify migration status:
```bash
alembic current
alembic check
```

---

## 3. Running Services Locally

### Terminal 1: FastAPI API Server
```bash
source .venv/bin/activate
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload
```
* Interactive API Documentation (Swagger): `http://127.0.0.1:8000/docs`
* Health Check: `http://127.0.0.1:8000/health`
* Live Prometheus Metrics: `http://127.0.0.1:8000/metrics`

### Terminal 2: Transactional Outbox Worker Daemon
```bash
source .venv/bin/activate
python apps/worker/main.py --interval 3
```

### Terminal 3: Next.js 16 Web Control Console
```bash
cd apps/web
npm install
npm run dev
```
* Web Console: `http://127.0.0.1:3000`

---

## 4. Running the Verification Suites

### 1. Full Pytest Suite (326 Tests)
```bash
pytest -q
```

### 2. Financial Invariants Suite (54 Tests)
Validates zero float math, monotonicity, tenant isolation, and state machine transitions:
```bash
pytest tests/invariants/ -v
```

### 3. Failure & Concurrency Suite (31 Tests)
Validates 100-thread adversarial races, advisory locks, and kill-switch fail-closed behavior:
```bash
pytest tests/failure/ tests/concurrency/ -v
```

### 4. Gateway Integration Suite
Validates SIMULATION, SANDBOX, and LIVE boundaries:
```bash
pytest tests/integration/test_gateway_modes.py -v
```

### 5. Playwright Browser E2E Suite (10 Critical Journeys)
Runs headless Chromium testing real API integration:
```bash
npm run e2e
```
* Interactive UI Mode: `npm run e2e:ui`
* HTML Report: `npm run e2e:report`

### 6. Code Quality & Security Audits
```bash
# Python dependencies
.venv/bin/pip check
.venv/bin/pip-audit

# Frontend lint & build
npm run lint --prefix apps/web
npm run build:web
npm audit --prefix apps/web

# Secrets scan
gitleaks dir . --redact --verbose
```
