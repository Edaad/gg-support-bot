"""Allow multiple group-chat candidates per payer/wallet identity.

Replaces single-row unique constraints with composite (identity, telegram_chat_id).

Usage:
    DATABASE_URL=... python migrate_payment_bind_multi_candidates.py
    DATABASE_URL=... python migrate_payment_bind_multi_candidates.py --skip-backup

Backs up payer/wallet binding tables with pg_dump before applying DDL (unless
``--skip-backup``). Requires pg_dump on PATH.

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from db.connection import _database_url, init_engine

_PAYER_TABLES = (
    "venmo_payer_bindings",
    "zelle_payer_bindings",
    "cashapp_payer_bindings",
    "paypal_payer_bindings",
)

_BACKUP_TABLES = _PAYER_TABLES + ("crypto_wallet_bindings",)

_DROP_PAYER_UQ = """
ALTER TABLE {table}
    DROP CONSTRAINT IF EXISTS uq_{table}_payer_name;
DROP INDEX IF EXISTS uq_{table}_payer_name;
"""

_CREATE_PAYER_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_payer_chat
    ON {table} (payer_name_normalized, telegram_chat_id);
"""

_DROP_CRYPTO_UQ = """
ALTER TABLE crypto_wallet_bindings
    DROP CONSTRAINT IF EXISTS uq_crypto_wallet_bindings_address_scope;
DROP INDEX IF EXISTS uq_crypto_wallet_bindings_address_scope;
"""

_CREATE_CRYPTO_UQ = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_crypto_wallet_bindings_address_scope_chat
    ON crypto_wallet_bindings (from_address_normalized, alert_scope, telegram_chat_id);
"""


def _backup_binding_tables(*, backup_dir: Path | None) -> Path:
    if shutil.which("pg_dump") is None:
        raise SystemExit(
            "pg_dump not found on PATH. Install PostgreSQL client tools, "
            "or re-run with --skip-backup (not recommended)."
        )

    url = _database_url()
    if not url:
        raise SystemExit("DATABASE_URL is not set")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = backup_dir or Path("backups") / f"payment_bind_multi_candidates_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_path = out_dir / "binding_tables.dump"

    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        f"--file={dump_path}",
    ]
    for table in _BACKUP_TABLES:
        cmd.append(f"--table={table}")
    cmd.append(url)

    print(f"Backing up binding tables to {dump_path} …")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "unknown error").strip()
        raise SystemExit(f"pg_dump failed ({result.returncode}): {err}")

    engine = init_engine()
    manifest_lines = [
        f"backup_created_utc={datetime.now(timezone.utc).isoformat()}",
        f"dump_path={dump_path}",
        "",
        "table\trow_count",
    ]
    with engine.connect() as conn:
        for table in _BACKUP_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            manifest_lines.append(f"{table}\t{count}")

    manifest_path = out_dir / "manifest.txt"
    manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    size_mb = dump_path.stat().st_size / (1024 * 1024)
    print(
        f"Backup complete: {dump_path} ({size_mb:.2f} MB). "
        f"Manifest: {manifest_path}"
    )
    print(
        "Restore binding tables with:\n"
        f"  pg_restore --data-only --no-owner --dbname=$DATABASE_URL {dump_path}"
    )
    return dump_path


def _run_migration() -> None:
    engine = init_engine()
    with engine.begin() as conn:
        for table in _PAYER_TABLES:
            conn.execute(text(_DROP_PAYER_UQ.format(table=table)))
            conn.execute(text(_CREATE_PAYER_UQ.format(table=table)))
        conn.execute(text(_DROP_CRYPTO_UQ))
        conn.execute(text(_CREATE_CRYPTO_UQ))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate payer/wallet bindings to multi-candidate unique keys."
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Apply DDL without pg_dump (not recommended).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Directory for binding_tables.dump (default: backups/payment_bind_multi_candidates_<ts>/).",
    )
    args = parser.parse_args()

    if not args.skip_backup:
        _backup_binding_tables(backup_dir=args.backup_dir)

    _run_migration()
    print("payment_bind_multi_candidates migration complete.")


if __name__ == "__main__":
    main()
