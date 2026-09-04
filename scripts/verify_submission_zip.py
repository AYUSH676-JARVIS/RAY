#!/usr/bin/env python3
"""Verify the created submission ZIP file.

Checks:
1. Integrity & extraction without errors.
2. Presence of required files (README.md, frontend, backend, tests, docker, CI/CD, docs).
3. Complete absence of forbidden files (.env, .env.local, node_modules, .next, .venv, *.pyc, etc.).
4. Runs gitleaks scan against the extracted directory.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ZIP_PATH = PROJECT_ROOT / "RAY_FINAL_SUBMISSION.zip"

REQUIRED_PATHS = [
    "README.md",
    ".env.example",
    "Dockerfile.api",
    "Dockerfile.worker",
    "docker-compose.yml",
    "docker-compose.prod.yml",
    ".github/workflows/ci.yml",
    "requirements.txt",
    "package.json",
    "alembic.ini",
    "apps/api/main.py",
    "apps/worker/main.py",
    "apps/web/package.json",
    "apps/web/src/app/page.tsx",
    "apps/web/e2e/control_plane.spec.ts",
    "services/action_layer/gateway.py",
    "services/money_graph/models.py",
    "tests/invariants/test_financial_execution_invariants.py",
    "tests/concurrency/test_concurrency_races.py",
    "docs/INTERVIEW_GUIDE.md",
    "docs/THREAT_MODEL.md",
    "docs/ARCHITECTURE.md",
    "docs/DEMO.md",
]

FORBIDDEN_PATTERNS = [
    ".env",
    ".env.local",
    "node_modules",
    ".next",
    ".venv",
    ".DS_Store",
    "__pycache__",
]

def verify():
    assert ZIP_PATH.exists(), f"ZIP not found: {ZIP_PATH}"
    print(f"[1/5] Testing ZIP integrity: {ZIP_PATH.name} ({ZIP_PATH.stat().st_size / (1024*1024):.2f} MB)")

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        corrupted = zf.testzip()
        assert corrupted is None, f"ZIP file corrupted at: {corrupted}"
        all_names = zf.namelist()
        print(f"      ZIP integrity valid. Archive contains {len(all_names)} files.")

    temp_dir = Path(tempfile.mkdtemp(prefix="ray_submission_verify_"))
    try:
        print(f"[2/5] Extracting to temporary sandbox: {temp_dir}")
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            zf.extractall(temp_dir)

        print("[3/5] Verifying required files exist...")
        missing = []
        for req in REQUIRED_PATHS:
            p = temp_dir / req
            if not p.exists():
                missing.append(req)
        if missing:
            print(f"FAILED: Missing required paths: {missing}")
            sys.exit(1)
        print(f"      All {len(REQUIRED_PATHS)} critical anchor files verified.")

        print("[4/5] Checking for forbidden / leaked files...")
        violations = []
        for root, dirs, files in os.walk(temp_dir):
            rel_root = Path(root).relative_to(temp_dir)
            for part in rel_root.parts:
                for pat in FORBIDDEN_PATTERNS:
                    if part == pat:
                        violations.append(str(rel_root))
            for f in files:
                rel_f = str(rel_root / f)
                for pat in FORBIDDEN_PATTERNS:
                    if f == pat or (pat.startswith(".") and f == pat):
                        violations.append(rel_f)
                if f.endswith(".pyc") or f.endswith(".log") or f.endswith(".sqlite") or f.endswith(".db"):
                    violations.append(rel_f)

        if violations:
            print(f"FAILED: Forbidden files detected in ZIP: {violations[:10]}")
            sys.exit(1)
        print("      Zero forbidden files detected (clean: no .env, credentials, node_modules, .next, .venv).")

        print("[5/5] Running Gitleaks scan against extracted submission...")
        cmd = ["gitleaks", "dir", str(temp_dir), "--redact", "--verbose"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        print(res.stdout)
        if res.returncode != 0:
            print(f"FAILED: Gitleaks detected secrets:\n{res.stderr}")
            sys.exit(res.returncode)
        print("      Gitleaks: ZERO leaks found in extracted submission.")

        print("\nAll submission integrity and security checks PASSED cleanly!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    verify()
