#!/usr/bin/env python3
"""Package RAY project into a clean, interview-grade submission ZIP.

Strictly adheres to exclusion rules:
- No .env / .env.local / credentials
- No node_modules
- No .next
- No .venv
- No __pycache__ / *.pyc
- No logs / temporary files / .DS_Store
- No database dumps / SQLite files
- No test-results / playwright-report / coverage caches
"""

import os
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ZIP = PROJECT_ROOT / "RAY_FINAL_SUBMISSION.zip"

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "playwright-report",
    "test-results",
    "htmlcov",
    "backups",
}

EXCLUDED_FILE_PATTERNS = {
    ".env",
    ".env.local",
    ".DS_Store",
    ".coverage",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".log",
    ".db",
    ".sqlite",
    ".sqlite3",
}

def should_exclude(rel_path: Path) -> bool:
    # Check if any directory part is in EXCLUDED_DIR_NAMES
    for part in rel_path.parts[:-1]:
        if part in EXCLUDED_DIR_NAMES:
            return True

    # If the path is a directory and in EXCLUDED_DIR_NAMES
    if rel_path.name in EXCLUDED_DIR_NAMES:
        return True

    # Check file patterns
    name = rel_path.name
    if name in EXCLUDED_FILE_PATTERNS:
        return True

    if name.startswith(".env.") and not name.endswith(".example"):
        return True

    if rel_path.suffix in EXCLUDED_EXTENSIONS:
        return True

    # Exclude the zip itself if it exists
    if name == "RAY_FINAL_SUBMISSION.zip":
        return True

    return False


def build_file_list():
    included_files = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Prune excluded directories in-place so os.walk does not traverse them
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]

        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(PROJECT_ROOT)
            if not should_exclude(rel_path):
                included_files.append((full_path, rel_path))

    return sorted(included_files, key=lambda x: str(x[1]))


def create_zip():
    files = build_file_list()
    print(f"Total files to package: {len(files)}")

    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for full_path, rel_path in files:
            # Preserve relative POSIX path structure
            arcname = rel_path.as_posix()
            zf.write(full_path, arcname=arcname)

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"Successfully created {OUTPUT_ZIP.name} ({size_mb:.2f} MB)")
    return OUTPUT_ZIP, len(files), size_mb


if __name__ == "__main__":
    create_zip()
