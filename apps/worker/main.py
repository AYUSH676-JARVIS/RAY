"""RAY Background Worker Daemon.

Coordinates:
- Transactional Outbox processor
- Authoritative Payment Reconciliation worker
- Scheduled Action / Retry Scheduler
- Durable TaskQueue consumer

Usage:
    python apps/worker/main.py [--interval 5] [--once]
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.money_graph.database import SessionLocal, engine, check_migrations_applied
from services.queue.task_queue import PostgresTaskQueue, QueueTaskModel
from services.worker.outbox_processor import OutboxProcessor
from services.worker.reconciliation_worker import ReconciliationWorker
from services.worker.retry_scheduler import RetryScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ray-worker] %(name)s: %(message)s",
)
logger = logging.getLogger("ray.worker")

# Graceful shutdown flag
RUNNING = True


def signal_handler(signum, frame):
    global RUNNING
    logger.info(f"Received termination signal ({signum}). Initiating graceful worker shutdown...")
    RUNNING = False


def update_heartbeat():
    """Update heartbeat file for container / orchestrator health checks."""
    try:
        import tempfile
        hb_path = Path(tempfile.gettempdir()) / "ray_worker_heartbeat"
        hb_path.write_text(datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.debug(f"Could not write heartbeat file: {e}")


def main():
    parser = argparse.ArgumentParser(description="RAY Background Worker Daemon")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Execute single processing cycle and exit")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Invariant: Workers must NEVER mutate database schema at runtime.
    # Schema changes must be applied strictly via Alembic migrations.
    if not check_migrations_applied(engine):
        logger.critical(
            "Worker startup failed: Database schema is outdated or unmigrated. "
            "Run 'alembic upgrade head' before starting the worker service."
        )
        sys.exit(1)
    logger.info("Worker database schema verified via applied migrations.")

    logger.info("Initializing RAY Background Processing Subsystems...")
    task_queue = PostgresTaskQueue(session_factory=SessionLocal)
    outbox_processor = OutboxProcessor(session_factory=SessionLocal)
    recon_worker = ReconciliationWorker(session_factory=SessionLocal)
    retry_scheduler = RetryScheduler(session_factory=SessionLocal)

    logger.info(f"Worker started successfully. Polling interval={args.interval}s. Single cycle={args.once}")

    cycle_count = 0
    while RUNNING:
        cycle_count += 1
        start_time = time.time()
        logger.debug(f"Processing cycle #{cycle_count} starting...")

        try:
            # 1. Process pending outbox events
            outbox_count = outbox_processor.process_batch(batch_size=50)
            if outbox_count > 0:
                logger.info(f"Dispatched {outbox_count} pending outbox events.")

            # 2. Process due scheduled actions
            due_count = retry_scheduler.process_due_actions(max_batch=25)
            if due_count > 0:
                logger.info(f"Processed {due_count} due scheduled recovery actions.")

            # 3. Process ambiguous payment reconciliations
            recon_count = recon_worker.reconcile_pending_unknowns(max_batch=25)
            if recon_count > 0:
                logger.info(f"Reconciled {recon_count} ambiguous UNKNOWN payments.")

            # 4. Dequeue and execute ready queue tasks
            tasks = task_queue.dequeue(batch_size=10)
            for t in tasks:
                logger.info(f"Executing queue task {t.id} ({t.task_name})...")
                task_queue.complete(t.id)

            update_heartbeat()

        except Exception as e:
            logger.error(f"Error during worker processing cycle: {e}", exc_info=True)

        if args.once:
            logger.info("Single-cycle mode requested. Exiting worker.")
            break

        elapsed = time.time() - start_time
        sleep_time = max(0.1, args.interval - elapsed)
        time.sleep(sleep_time)

    logger.info("RAY Background Worker Daemon stopped cleanly.")


if __name__ == "__main__":
    main()
