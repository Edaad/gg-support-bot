"""Add method_owner to manual payment tables and backfill from Vaughn heuristics.

Usage:
    DATABASE_URL=... python migrate_payment_method_owner.py
    DATABASE_URL=... python migrate_payment_method_owner.py --dry-run
    DATABASE_URL=... python migrate_payment_method_owner.py --reclassify
    DATABASE_URL=... python migrate_payment_method_owner.py --reclassify --dry-run

Idempotent: safe to run multiple times (IF NOT EXISTS / skips rows already set).
"""

from __future__ import annotations

import argparse
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import inspect, text

from api.club_slug import slug_for_club_name
from api.method_owner import METHOD_OWNER_ROUND_TABLE, METHOD_OWNER_VAUGHN, infer_method_owner_for_backfill
from db.connection import get_db, init_engine
from db.models import Club

load_dotenv()

PAYMENT_TABLES = (
    "venmo_payments",
    "zelle_payments",
    "cashapp_payments",
    "paypal_payments",
    "crypto_payments",
)

TABLE_BACKFILL_CONFIG: dict[str, dict[str, str | None]] = {
    "venmo_payments": {
        "source": "deposit_venmo",
        "variant_col": "venmo_handle",
        "memo_col": "memo",
        "alert_scope_col": None,
    },
    "zelle_payments": {
        "source": "deposit_zelle",
        "variant_col": "zelle_recipient",
        "memo_col": "memo",
        "alert_scope_col": None,
    },
    "cashapp_payments": {
        "source": "deposit_cashapp",
        "variant_col": "cashapp_handle",
        "memo_col": "memo",
        "alert_scope_col": None,
    },
    "paypal_payments": {
        "source": "deposit_paypal",
        "variant_col": "paypal_email",
        "memo_col": "memo",
        "alert_scope_col": None,
    },
    "crypto_payments": {
        "source": "deposit_crypto",
        "variant_col": "token_symbol",
        "memo_col": None,
        "alert_scope_col": "alert_scope",
    },
}

ADD_COLUMN = """
ALTER TABLE {table}
ADD COLUMN IF NOT EXISTS method_owner VARCHAR(32);
"""

SET_NOT_NULL = """
ALTER TABLE {table}
ALTER COLUMN method_owner SET NOT NULL;
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS ix_{table}_method_owner_created
ON {table} (method_owner, created_at);
"""


def _club_slug_by_id(session) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in session.query(Club.id, Club.name).all():
        slug = slug_for_club_name(row.name) or ""
        out[int(row.id)] = slug
    return out


def _club_slug_for_row(
    *,
    club_id: int | None,
    alert_scope: str | None,
    club_slugs: dict[int, str],
) -> str:
    if club_id is not None:
        return club_slugs.get(int(club_id), "")
    if (alert_scope or "").strip() == "clubgto":
        return "clubgto"
    return ""


def _null_count(session, table: str) -> int:
    return int(
        session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE method_owner IS NULL")
        ).scalar_one()
    )


def _backfill_table(
    session,
    *,
    table: str,
    source: str,
    variant_col: str,
    club_slugs: dict[int, str],
    memo_col: str | None = "memo",
    alert_scope_col: str | None = None,
) -> int:
    cols = ["id", "club_id", variant_col]
    if memo_col:
        cols.append(memo_col)
    if alert_scope_col:
        cols.append(alert_scope_col)
    rows = (
        session.execute(
            text(
                f"SELECT {', '.join(cols)} FROM {table} "
                "WHERE method_owner IS NULL ORDER BY id"
            )
        )
        .mappings()
        .all()
    )
    updated = 0
    for row in rows:
        owner = infer_method_owner_for_backfill(
            source=source,
            variant=row[variant_col],
            club_slug=_club_slug_for_row(
                club_id=row["club_id"],
                alert_scope=row.get(alert_scope_col) if alert_scope_col else None,
                club_slugs=club_slugs,
            ),
            memo=row.get(memo_col) if memo_col else None,
        )
        result = session.execute(
            text(
                f"UPDATE {table} "
                "SET method_owner = :owner "
                "WHERE id = :id AND method_owner IS NULL"
            ),
            {"owner": owner, "id": row["id"]},
        )
        updated += int(result.rowcount or 0)
    return updated


def _preview_table(
    session,
    *,
    table: str,
    source: str,
    variant_col: str,
    club_slugs: dict[int, str],
    memo_col: str | None = "memo",
    alert_scope_col: str | None = None,
) -> tuple[int, Counter[str]]:
    cols = ["id", "club_id", variant_col]
    if memo_col:
        cols.append(memo_col)
    if alert_scope_col:
        cols.append(alert_scope_col)
    rows = session.execute(
        text(
            f"SELECT {', '.join(cols)} FROM {table} ORDER BY id"
        )
    ).mappings().all()
    counts: Counter[str] = Counter()
    for row in rows:
        owner = infer_method_owner_for_backfill(
            source=source,
            variant=row[variant_col],
            club_slug=_club_slug_for_row(
                club_id=row["club_id"],
                alert_scope=row.get(alert_scope_col) if alert_scope_col else None,
                club_slugs=club_slugs,
            ),
            memo=row.get(memo_col) if memo_col else None,
        )
        counts[owner] += 1
    return sum(counts.values()), counts


def _reclassify_table(
    session,
    *,
    table: str,
    source: str,
    variant_col: str,
    club_slugs: dict[int, str],
    memo_col: str | None = "memo",
    alert_scope_col: str | None = None,
    dry_run: bool = False,
) -> tuple[int, Counter[str]]:
    cols = ["id", "club_id", "method_owner", variant_col]
    if memo_col:
        cols.append(memo_col)
    if alert_scope_col:
        cols.append(alert_scope_col)
    rows = session.execute(
        text(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id")
    ).mappings().all()
    changes: Counter[str] = Counter()
    updated = 0
    for row in rows:
        inferred = infer_method_owner_for_backfill(
            source=source,
            variant=row[variant_col],
            club_slug=_club_slug_for_row(
                club_id=row["club_id"],
                alert_scope=row.get(alert_scope_col) if alert_scope_col else None,
                club_slugs=club_slugs,
            ),
            memo=row.get(memo_col) if memo_col else None,
        )
        current = (row["method_owner"] or "").strip()
        if current == inferred:
            continue
        changes[f"{current}->{inferred}"] += 1
        if dry_run:
            continue
        result = session.execute(
            text(
                f"UPDATE {table} "
                "SET method_owner = :owner "
                "WHERE id = :id AND method_owner = :current"
            ),
            {"owner": inferred, "id": row["id"], "current": current},
        )
        updated += int(result.rowcount or 0)
    return updated, changes


def reclassify_method_owner(*, dry_run: bool = False) -> None:
    engine = init_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    totals: Counter[str] = Counter()
    total_updated = 0
    with get_db() as session:
        club_slugs = _club_slug_by_id(session)
        for table, cfg in TABLE_BACKFILL_CONFIG.items():
            if table not in existing_tables:
                continue
            updated, changes = _reclassify_table(
                session,
                table=table,
                source=str(cfg["source"]),
                variant_col=str(cfg["variant_col"]),
                club_slugs=club_slugs,
                memo_col=cfg["memo_col"],
                alert_scope_col=cfg["alert_scope_col"],
                dry_run=dry_run,
            )
            total_updated += updated
            totals.update(changes)
            if changes:
                print(f"  {table}: {dict(changes)}")
        if not dry_run:
            session.commit()

    mode = "dry run" if dry_run else "applied"
    print(f"method_owner reclassify {mode}: {total_updated} row(s) updated")
    if totals:
        print(f"  transitions: {dict(totals)}")
    else:
        print("  no mismatches found")


def dry_run_backfill() -> None:
    engine = init_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    previews: dict[str, tuple[int, Counter[str]]] = {}
    with get_db() as session:
        club_slugs = _club_slug_by_id(session)
        for table, cfg in TABLE_BACKFILL_CONFIG.items():
            if table not in existing_tables:
                continue
            previews[table] = _preview_table(
                session,
                table=table,
                source=str(cfg["source"]),
                variant_col=str(cfg["variant_col"]),
                club_slugs=club_slugs,
                memo_col=cfg["memo_col"],
                alert_scope_col=cfg["alert_scope_col"],
            )

    totals = Counter()
    print("method_owner backfill dry run (no writes):")
    for table, (total, counts) in previews.items():
        vaughn = counts.get(METHOD_OWNER_VAUGHN, 0)
        round_table = counts.get(METHOD_OWNER_ROUND_TABLE, 0)
        mateos = counts.get("mateos", 0)
        print(
            f"  {table}: {total} row(s) -> "
            f"vaughn={vaughn}, round-table={round_table}, mateos={mateos}"
        )
        totals.update(counts)

    print(
        f"  TOTAL: {sum(totals.values())} row(s) -> "
        f"vaughn={totals.get(METHOD_OWNER_VAUGHN, 0)}, "
        f"round-table={totals.get(METHOD_OWNER_ROUND_TABLE, 0)}, "
        f"mateos={totals.get('mateos', 0)}"
    )

    for table in PAYMENT_TABLES:
        if table not in existing_tables:
            print(f"  (skip {table}: table does not exist)")
            continue
        columns = {col["name"] for col in inspector.get_columns(table)}
        if "method_owner" in columns:
            print(f"  note: {table}.method_owner column already exists")


def main() -> None:
    engine = init_engine()
    with engine.connect() as conn:
        for table in PAYMENT_TABLES:
            conn.execute(text(ADD_COLUMN.format(table=table)))
        conn.commit()

    counts: dict[str, int] = {}
    with get_db() as session:
        club_slugs = _club_slug_by_id(session)
        for table, cfg in TABLE_BACKFILL_CONFIG.items():
            counts[table] = _backfill_table(
                session,
                table=table,
                source=str(cfg["source"]),
                variant_col=str(cfg["variant_col"]),
                club_slugs=club_slugs,
                memo_col=cfg["memo_col"],
                alert_scope_col=cfg["alert_scope_col"],
            )
        session.commit()

        remaining: dict[str, int] = {}
        for table in PAYMENT_TABLES:
            nulls = _null_count(session, table)
            if nulls:
                remaining[table] = nulls
        if remaining:
            raise RuntimeError(
                "method_owner backfill incomplete; NULL rows remain: "
                + ", ".join(f"{table}={count}" for table, count in remaining.items())
            )

    with engine.connect() as conn:
        for table in PAYMENT_TABLES:
            conn.execute(text(SET_NOT_NULL.format(table=table)))
            conn.execute(text(CREATE_INDEX.format(table=table)))
        conn.commit()

    print("method_owner migration complete:")
    for table, count in counts.items():
        print(f"  {table}: backfilled {count} row(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Add and backfill payment method_owner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview backfill counts without writing to the database",
    )
    parser.add_argument(
        "--reclassify",
        action="store_true",
        help="Update rows whose method_owner no longer matches Vaughn heuristics",
    )
    args = parser.parse_args()
    if args.reclassify:
        print("method_owner reclassify preview:" if args.dry_run else "method_owner reclassify:")
        reclassify_method_owner(dry_run=args.dry_run)
    elif args.dry_run:
        dry_run_backfill()
    else:
        main()
