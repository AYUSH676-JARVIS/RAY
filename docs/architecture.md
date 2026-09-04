# RAY Merchant Revenue Recovery Control Plane — Technical Architecture & Engineering Specifications

> **System Overview:** Institutional-grade revenue recovery and transaction intelligence platform designed for high-volume merchant acquiring environments.

---

## 1. High-Level System Architecture

```mermaid
graph TD
    Client([Merchant Web Console / Browser]) <-->|HTTPS / TLS 1.3| NGINX[NGINX Reverse Proxy :80/:443]
    ExternalGW([Acquiring Gateways / Razorpay]) <-->|HMAC Webhooks & REST| NGINX

    subgraph DOCKER_COMPOSE["Ray Isolated Network (ray_network)"]
        NGINX -->|Route / | WebApp[Next.js 16 Web Console :3000]
        NGINX -->|Route /api/ | API[FastAPI Control Plane Node :8000]

        subgraph BACKEND_SERVICES["Backend Domain Layer"]
            API -->|ACID Transactions| DB[(PostgreSQL 16 Engine :5432)]
            Worker[Outbox Worker Daemon] -->|Poll Outbox SKIP LOCKED| DB
            Worker -->|Idempotent Gateway Dispatch| GatewayAdapter[Gateway Adapter Boundary]
        end
    end

    GatewayAdapter -.->|Mode: SIMULATION| SimEngine[Internal Simulation Engine]
    GatewayAdapter -.->|Mode: SANDBOX| RzpSandbox[Razorpay Sandbox API]
    GatewayAdapter -.->|Mode: LIVE (Strict Opt-In)| RzpLive[Razorpay Live API]
```

---

## 2. Financial Execution Sequence & 5-Layer Boundary

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as Merchant System / Declining Event
    participant API as FastAPI Ingestion
    participant MG as Money Graph & Analytics
    participant AI as AI Advisory Model
    participant Policy as Deterministic Policy Engine
    participant Lock as PostgreSQL Advisory Lock
    participant DB as PostgreSQL (ACID Outbox)
    participant Worker as Background Worker
    participant GW as Gateway Boundary

    Merchant->>API: Submit Failed Payment / Ingest Decline Event
    API->>MG: Hydrate Payment Entity & Decline Taxonomy
    MG->>AI: Propose Remediating Strategy (e.g. SMART_RETRY)
    Note over AI: AI has ZERO execution authority.<br/>Advisory proposal only.
    AI-->>Policy: Propose (strategy_name, risk_score, win_rate)
    
    rect rgb(20, 30, 40)
        Note over Policy: Deterministic Safety Evaluation
        Policy->>Policy: Validate Merchant Velocity Limits
        Policy->>Policy: Validate Maximum Retry Attempts (<= 3)
        Policy->>Policy: Validate Fraud Tolerance (Zero-Tolerance)
    end

    alt Policy REJECTS
        Policy-->>API: BLOCKED_POLICY (Exit without Execution)
        API->>DB: Record Chained Audit Event
    else Policy APPROVES
        Policy->>Lock: Acquire pg_try_advisory_xact_lock(merchant_id, idempotency_key)
        alt Lock Failed (Concurrent Race)
            Lock-->>API: HTTP 409 Conflict (ConcurrentExecutionBlockedError)
        else Lock Acquired
            Policy->>DB: Atomic Write: Action Record + Transactional Outbox Event
            DB-->>API: Commit Transaction (Advisory Lock Released)
            API-->>Merchant: Action Queued (Structured HTTP 200/202)
        end
    end

    Worker->>DB: SELECT * FROM outbox_events FOR UPDATE SKIP LOCKED
    Worker->>GW: Execute Gateway Call with Idempotency Key
    GW-->>Worker: Outcome: SUCCEEDED | UNKNOWN | FAILED
    Worker->>DB: Update State Machine & Record SHA-256 Chained Audit
```

---

## 3. Webhook Ingestion & Replay Protection Flow

```mermaid
flowchart TD
    Inbound[Inbound POST /api/v1/webhooks/razorpay] --> SignatureCheck{Verify HMAC-SHA256<br/>Signature?}
    
    SignatureCheck -- Invalid --> RejectSig[Return HTTP 400 Bad Request<br/>Metric: ray_webhook_failures_total]
    SignatureCheck -- Valid --> DriftCheck{Timestamp Age<br/>> 300 Seconds?}

    DriftCheck -- Expired --> RejectDrift[Return HTTP 400 Replay Detected<br/>Metric: ray_webhook_failures_total]
    DriftCheck -- Fresh --> DedupCheck{Event UUID exists in<br/>webhook_events table?}

    DedupCheck -- Duplicate --> DedupSuccess[Return HTTP 200 OK<br/>status: ALREADY_PROCESSED<br/>Metric: ray_webhook_duplicates_total]
    DedupCheck -- New Event --> DBInsert[Insert into webhook_events<br/>Write Transactional Outbox Event]

    DBInsert --> StateMachine[Process Payment State Machine]
    StateMachine --> AuditLog[Append Continuous SHA-256 Audit Event]
```

---

## 4. Concurrency & Idempotency Flow

```mermaid
flowchart TD
    ReqA[Request A: Idempotency Key X] --> LockA{pg_try_advisory_xact_lock<br/>Hash(merchant_id, X)}
    ReqB[Request B: Idempotency Key X<br/>Concurrent In-Flight] --> LockB{pg_try_advisory_xact_lock<br/>Hash(merchant_id, X)}

    LockA -- Lock Acquired --> ExecA[Execute Business Logic & Outbox Write]
    ExecA --> CommitA[DB Transaction Commit<br/>Lock Automatically Released]

    LockB -- Lock Busy --> BlockB[Raise ConcurrentExecutionBlockedError]
    BlockB --> Resp409[Return Structured HTTP 409 Conflict<br/>Metric: ray_idempotency_collisions_total]

    ReqC[Request C: Idempotency Key X<br/>Subsequent Replay] --> IdemQuery{idempotency_key<br/>already in actions table?}
    IdemQuery -- Found --> ReplayCached[Return Cached Execution Result<br/>idempotent_replay: true]
```

---

## 5. Authoritative Reconciliation Flow (UNKNOWN ≠ FAILED)

```mermaid
flowchart TD
    CallGW[Action Dispatch to Acquiring Gateway] --> Outcome{Gateway HTTP Response}

    Outcome -- HTTP 200 OK --> Captured[State: CAPTURED / SUCCEEDED<br/>Settlement Reality Verified]
    Outcome -- HTTP 4xx Terminal --> Declined[State: TERMINAL_DECLINE<br/>Do Not Retry]
    Outcome -- Socket Timeout / 5xx --> UnknownState[State: UNKNOWN<br/>The Golden Invariant]

    subgraph RECONCILIATION_DEFENSE["Authoritative Reconciliation Queue"]
        UnknownState --> LockRetry[Lock Action: Blind Retry FORBIDDEN]
        LockRetry --> Polling[Scheduled Authoritative Query: query_status]
        LockRetry --> WebhookWait[Await Signed Webhook: payment.captured]
    end

    Polling --> AuthoritativeEvidence{Authoritative Reality<br/>Confirmed?}
    WebhookWait --> AuthoritativeEvidence

    AuthoritativeEvidence -- Confirmed Captured --> UpdateSuccess[State: SETTLED<br/>Recovered Revenue Accredited]
    AuthoritativeEvidence -- Confirmed Failed --> UpdateFailed[State: FAILED<br/>Next Policy Step Evaluated]
```

---

## 6. Security Boundary & Authority Separation

```mermaid
graph TD
    subgraph ZONE_UNTRUSTED["Untrusted Zone"]
        ExtNet[Public Internet / Client Browser]
    end

    subgraph ZONE_DMZ["Ingress & Perimeter Security"]
        TLS[TLS 1.3 Termination]
        WAF[Rate Limiting & Request Sanitization]
        JWT[Bearer Token Auth & Tenant Resolver]
    end

    subgraph ZONE_BUSINESS["Business Logic & Intelligence"]
        AI_MODEL[AI Advisory Service<br/><b>Zero Direct Execution Authority</b>]
        POLICY[Deterministic Policy Engine<br/><b>Rigid Numeric Bounds & Velocity Caps</b>]
    end

    subgraph ZONE_CRITICAL["Critical Financial Execution Zone"]
        IDEM_LOCK[PostgreSQL Advisory Locks]
        STAGE1_LOCK[Stage 1 Financial Safety Lock<br/><b>Fail-Closed Active</b>]
        KILL_SWITCH[PostgreSQL Distributed Kill Switch<br/><b>Fail-Closed on Outage</b>]
        AUDIT[Immutable Continuous SHA-256 Audit Chain]
    end

    ExtNet --> TLS --> WAF --> JWT
    JWT --> AI_MODEL --> POLICY
    POLICY --> IDEM_LOCK --> STAGE1_LOCK --> KILL_SWITCH --> AUDIT
```

---

## 7. CI/CD Automated Verification Pipeline

```mermaid
flowchart LR
    subgraph CI["GitHub Actions Pipeline (.github/workflows/ci.yml)"]
        direction TB
        Lint["1. Lint & Format<br/>(flake8, black)"] --> Unit["2. Unit & Invariant Tests<br/>(pytest 326 tests)"]
        Unit --> Concurrency["3. Concurrency Tests<br/>(100-thread adversarial)"]
        Concurrency --> Security["4. Security Scans<br/>(Gitleaks, pip-audit)"]
        Security --> Frontend["5. Frontend Verification<br/>(npm lint, build, audit)"]
        Frontend --> E2E["6. Playwright E2E<br/>(10 Critical Journeys)"]
        E2E --> Docker["7. Container Builds<br/>(API, Worker, Web)"]
    end
```

---

## 8. Production Deployment Architecture (Docker Compose)

```mermaid
graph TD
    subgraph HOST["Production Host / Cloud VM"]
        subgraph PORTS["Exposed Host Ports"]
            P80[Port 80 HTTP]
            P443[Port 443 HTTPS]
        end

        subgraph CONTAINER_NGINX["Reverse Proxy Container"]
            NGINX_SVC[NGINX Reverse Proxy]
        end

        subgraph CONTAINER_NET["Internal Docker Network (ray_network)"]
            WEB_SVC[Next.js Frontend :3000]
            API_SVC[FastAPI Application :8000]
            WORKER_SVC[Background Daemon Worker]
            MIGRATE_SVC[Alembic Migration Runner]
            DB_SVC[(PostgreSQL 16 Database :5432)]
        end
    end

    P80 --> NGINX_SVC
    P443 --> NGINX_SVC
    NGINX_SVC -->|Proxy / | WEB_SVC
    NGINX_SVC -->|Proxy /api/ | API_SVC
    API_SVC --> DB_SVC
    WORKER_SVC --> DB_SVC
    MIGRATE_SVC -.->|One-Time Startup| DB_SVC
```
