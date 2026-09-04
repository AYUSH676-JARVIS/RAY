#!/usr/bin/env python3
"""CLI script to seed the RAY Merchant database with reproducible synthetic data.

Usage:
    python scripts/generate_data.py [--seed 42] [--reset]
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.money_graph.database import SessionLocal, init_db, drop_db
from data.synthetic.generator import SyntheticDataGenerator



def main():
    parser = argparse.ArgumentParser(description="Seed database with deterministic synthetic data.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate tables before seeding")
    parser.add_argument("--customers", type=int, default=10000, help="Number of customers to generate (default: 10,000)")
    parser.add_argument("--orders", type=int, default=20000, help="Number of orders to generate (default: 20,000)")
    parser.add_argument("--payments", type=int, default=25000, help="Number of payments to generate (default: 25,000)")
    parser.add_argument("--failed", type=int, default=5000, help="Number of failed payments to generate (default: 5,000)")
    args = parser.parse_args()

    print(f"=== RAY Synthetic Data Seeder ===")
    print(f"Seed: {args.seed}")
    print(f"Target: {args.customers} customers | {args.orders} orders | {args.payments} payments ({args.failed} failures)")

    start_time = time.time()

    if args.reset:
        print("[1/3] Dropping existing tables...")
        drop_db()
        print("[2/3] Initializing clean database schema...")
        init_db()
    else:
        print("[1/2] Ensuring schema tables exist...")
        init_db()

    print("[Seeding] Generating deterministic synthetic records...")
    db = SessionLocal()
    try:
        generator = SyntheticDataGenerator(seed=args.seed)
        counts = generator.generate_all(
            session=db,
            num_customers=args.customers,
            num_orders=args.orders,
            num_payments=args.payments,
            num_failed_payments=args.failed,
        )
        elapsed = time.time() - start_time
        print(f"\n[Done] Database seeded in {elapsed:.2f}s!")
        print(f"Summary: {counts}")
    except Exception as e:
        print(f"\n[Error] Seeding failed: {e}", file=sys.stderr)
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
