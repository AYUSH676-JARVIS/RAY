#!/usr/bin/env python3
"""Export OpenAPI 3.0 specification from FastAPI application.

Usage:
    python scripts/export_openapi.py [--output docs/openapi.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from apps.api.main import app


def export_openapi(output_path: Path):
    """Generate and write OpenAPI JSON schema."""
    schema = app.openapi()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
    print(f"Exported valid OpenAPI 3.0 schema to: {output_path} ({len(schema.get('paths', {}))} endpoints)")


def main():
    parser = argparse.ArgumentParser(description="Export OpenAPI Specification")
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "docs" / "openapi.json",
        help="Target output JSON path",
    )
    args = parser.parse_args()
    export_openapi(args.output)


if __name__ == "__main__":
    main()
