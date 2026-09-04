# RAY Control Plane — Production Deployment Guide

> **Deployment Architecture:** Multi-Container Docker Architecture with NGINX Reverse Proxy, Non-Root Containers, and Internal Isolated Network.

---

## 1. Production Architecture Overview

```
                     Internet (TLS 1.3 :443)
                                │
                                ▼
                       [ NGINX Proxy :443 ]
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
 [ Web Console :3000 ]                       [ FastAPI Node :8000 ]
  (Next.js 16 Standalone)                     (Uvicorn ASGI Workers)
                                                      │
                                                      ▼
                                            [ PostgreSQL 16 DB ]
                                             (Isolated Network)
                                                      ▲
                                                      │
                                             [ Worker Daemon ]
                                            (Outbox Dispatcher)
```

---

## 2. One-Command Production Startup

To launch the complete stack with database, migrations, API, background worker, and web console:

```bash
# Build and start all services in detached mode
docker compose -f docker-compose.prod.yml up --build -d
```

### Checking Stack Health
```bash
# Check running containers
docker compose -f docker-compose.prod.yml ps

# Check API health endpoint
curl -s http://localhost:8000/health | jq .

# Check Prometheus metrics
curl -s http://localhost:8000/metrics | head -n 25
```

---

## 3. Production Hardening Features

### 1. Minimal Multi-Stage Docker Builds
* **API & Worker (`Dockerfile.api`, `Dockerfile.worker`)**: Multi-stage Python 3.12-slim build with non-root user `rayapp` (UID 1001), isolated dependency layer, and zero cache artifacts.
* **Frontend (`apps/web/Dockerfile`)**: Multi-stage Node 20-alpine build with Next.js standalone output, reducing container image size to under 120MB.

### 2. Network Isolation & Database Protection
* All application containers (`api`, `worker`, `web`, `migration`, `postgres`) communicate over an isolated Docker bridge network `ray_network`.
* In `docker-compose.prod.yml`, the PostgreSQL port `5432` is **NOT exposed to the host**, preventing external network probing.
* API and Web traffic route through the reverse proxy.

### 3. Migration Sequencing
* The `migration` service runs `alembic upgrade head` upon startup.
* Both `api` and `worker` services declare `depends_on: { migration: { condition: service_completed_successfully } }`.
* Application nodes never start against an unmigrated or stale database schema.

### 4. Health Checks & Automatic Recovery
* PostgreSQL: `pg_isready -U postgres -d ray_db` every 5 seconds.
* FastAPI: `curl -f http://localhost:8000/health` every 10 seconds.
* Automatic restart policy `unless-stopped` ensures resilience against transient crashes.

---

## 4. Environment Configuration & Secret Injection

Never commit production credentials to source control. Production environments must inject variables via secure orchestration (AWS Secrets Manager, HashiCorp Vault, Kubernetes Secrets):

```ini
# Production Environment Configuration
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://ray_user:<STRONG_PASSWORD>@postgres:5432/ray_db

# Host & Ports
API_HOST=0.0.0.0
API_PORT=8000

# Strict Financial Safety Controls (DO NOT MODIFY IN UNVERIFIED DEPLOYMENTS)
LIVE_EXECUTION_ENABLED=false
STAGE_1_SAFETY_LOCK=true

# Gateway Credentials (Injected at Runtime)
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
```

---

## 5. Graceful Shutdown & Container Lifecycle

* FastAPI handles `SIGTERM` / `SIGINT` by finishing active in-flight HTTP requests and closing database connection pools cleanly.
* Background outbox workers intercept `SIGTERM` to complete the currently executing transaction before terminating, leaving no orphaned outbox claims.
* PostgreSQL advisory locks are session/transaction scoped: in the event of an abrupt host termination, the database engine automatically releases all held advisory locks upon connection drop.
