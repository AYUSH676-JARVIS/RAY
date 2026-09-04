# RAY Control Plane — Horizontal Scalability & Concurrency Model

**Document Version**: 2.0.0-PROD  
**Classification**: Performance, Concurrency & Infrastructure Architecture  
**Target Throughput**: 100+ Requests/sec per Node with Sub-Second p95 Latency

---

## 1. Stateless Tier & Multi-Instance Scaling

The RAY API and Decision Loop components are designed as completely stateless containers. Any number of application instances can run behind a standard load balancer (e.g., Nginx, AWS ALB, Cloudflare).

### Scalability Principles:
1. **No In-Memory Singletons**: State is strictly maintained in PostgreSQL. In-memory maps or global locks are prohibited.
2. **Distributed Advisory Locking**: Cluster-wide synchronization is managed through PostgreSQL session-level advisory locks (`services/concurrency/distributed_lock.py`).
3. **Optimistic & Row-Level Locking**: High-throughput queues and outbox tables use `SELECT ... FOR UPDATE SKIP LOCKED` to allow $N$ background workers to poll concurrently without contention.

---

## 2. PostgreSQL Connection Pooling Architecture

Database connections are managed via SQLAlchemy 2.0 engine pooling with tuned parameters:

| Configuration Parameter | Environment Variable | Default Value | Production Impact |
| :--- | :--- | :--- | :--- |
| **Pool Size** | `DB_POOL_SIZE` | `20` | Base persistent open connections per container instance. |
| **Max Overflow** | `DB_MAX_OVERFLOW` | `30` | Burst allowance during sudden recovery spikes (up to 50 active per node). |
| **Pool Timeout** | `DB_POOL_TIMEOUT` | `15` | Fail-closed limit (seconds) preventing worker thread pileup during DB outages. |
| **Pool Recycle** | `DB_POOL_RECYCLE` | `1800` | Periodically recycles connections (30 min) to avoid stale socket drops. |

---

## 3. High-Concurrency Advisory Locks (`distributed_lock.py`)

Critical financial execution paths serialize access per action or payment using PostgreSQL advisory locks:

```python
# PostgreSQL Advisory Lock implementation:
with distributed_lock(f"action_exec_{action_id}", timeout_seconds=5.0):
    # Exactly one worker process across the cluster executes this block
    execute_action_safely(...)
```

- **Hash Mapping**: Keys are normalized and hashed into signed 32-bit integers via CRC32.
- **Fail-Closed Acquisition**: If a competing instance holds the lock beyond the timeout, the acquisition raises `TimeoutError` and schedules a safe, idempotent retry.

---

## 4. Benchmark Verification Results

From `docs/LOAD_TEST_RESULTS.md`:
- **Concurrency**: 25 concurrent workers executing read and write pipelines.
- **Total Requests**: 100 requests (25 health, 25 dashboard, 25 opportunities, 25 signed webhooks).
- **Elapsed Time**: 1.03 seconds.
- **Throughput**: **97.2 Requests / Second (RPS)**.
- **Error Rate**: **0.00%** (zero HTTP 5xx or unhandled exceptions).
- **Read Latency (p95)**: 166.5 ms (Target SLA: < 200 ms).
- **System Health Latency (p95)**: 51.17 ms (Target SLA: < 50 ms).
