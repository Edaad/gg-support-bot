#!/usr/bin/env python
"""Read-only: compare the DATABASE_URL schema with a fresh database migrated by Alembic.

The target database is only read (pg_dump --schema-only in a read-only session).
Exits 1 and prints a diff when the schemas differ.

Usage:
  DATABASE_URL=<prod> python scripts/check_prod_schema_parity.py
  DATABASE_URL=<prod> python scripts/check_prod_schema_parity.py --revision 0001_baseline
    (before stamping prod: prod must equal the baseline)
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from alembic import command
from alembic.config import Config

from db.connection import _database_url
from scripts._temp_postgres import local_env, pg_bin_dir, temp_postgres

_DUMP_FLAGS = (
    "--schema-only",
    "--no-owner",
    "--no-privileges",
    "--schema=public",
    "--exclude-table=alembic_version",
)
_NOISE_PREFIXES = ("--", "\\restrict", "\\unrestrict", "SET ", "SELECT pg_catalog.")


def _dump(url: str, env: dict[str, str]) -> list[str]:
    out = subprocess.run(
        [str(pg_bin_dir() / "pg_dump"), url, *_DUMP_FLAGS],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    ).stdout
    return [
        line
        for line in out.splitlines()
        if line.strip() and not line.startswith(_NOISE_PREFIXES)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--revision", default="head")
    args = parser.parse_args()

    target_url = _database_url()
    if not target_url:
        raise SystemExit("DATABASE_URL is not set")

    read_only_env = {**os.environ, "PGOPTIONS": "-c default_transaction_read_only=on"}
    target = _dump(target_url, read_only_env)

    with temp_postgres("parity_check") as local_url:
        os.environ["DATABASE_URL"] = local_url
        command.upgrade(Config(str(_REPO_ROOT / "alembic.ini")), args.revision)
        migrated = _dump(local_url, local_env())

    diff = list(
        difflib.unified_diff(
            target,
            migrated,
            "target (DATABASE_URL)",
            f"alembic {args.revision}",
            lineterm="",
        )
    )
    if diff:
        print("\n".join(diff))
        print(f"\nSchema differs from alembic {args.revision}.", file=sys.stderr)
        return 1
    print(f"Schema matches alembic {args.revision}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
