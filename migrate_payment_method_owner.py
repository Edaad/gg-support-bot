"""Add method_owner to manual payment tables and backfill from Vaughn heuristics.

Usage:
    DATABASE_URL=... python migrate_payment_method_owner.py
    DATABASE_URL=... python migrate_payment_method_owner.py --dry-run

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
from db.models import (
    CashAppPayment,
    Club,
    CryptoPayment,
    PayPalPayment,
    VenmoPayment,
    ZellePayment,
)

load_dotenv()

PAYMENT_TABLES = (
    "venmo_payments",
    "zelle_payments",
    "cashapp_payments",
    "paypal_payments",
    "crypto_payments",
)

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


def _backfill_venmo(session, club_slugs: dict[int, str]) -> int:
    updated = 0
    rows = session.query(VenmoPayment).filter(VenmoPayment.method_owner.is_(None)).all()
    for row in rows:
        row.method_owner = infer_method_owner_for_backfill(
            source="deposit_venmo",
            variant=row.venmo_handle,
            club_slug=_club_slug_for_row(
                club_id=row.club_id,
                alert_scope=None,
                club_slugs=club_slugs,
            ),
            memo=row.memo,
        )
        updated += 1
    return updated


def _backfill_zelle(session, club_slugs: dict[int, str]) -> int:
    updated = 0
    rows = session.query(ZellePayment).filter(ZellePayment.method_owner.is_(None)).all()
    for row in rows:
        row.method_owner = infer_method_owner_for_backfill(
            source="deposit_zelle",
            variant=row.zelle_recipient,
            club_slug=_club_slug_for_row(
                club_id=row.club_id,
                alert_scope=None,
                club_slugs=club_slugs,
            ),
            memo=row.memo,
        )
        updated += 1
    return updated


def _backfill_cashapp(session, club_slugs: dict[int, str]) -> int:
    updated = 0
    rows = (
        session.query(CashAppPayment)
        .filter(CashAppPayment.method_owner.is_(None))
        .all()
    )
    for row in rows:
        row.method_owner = infer_method_owner_for_backfill(
            source="deposit_cashapp",
            variant=row.cashapp_handle,
            club_slug=_club_slug_for_row(
                club_id=row.club_id,
                alert_scope=None,
                club_slugs=club_slugs,
            ),
            memo=row.memo,
        )
        updated += 1
    return updated


def _backfill_paypal(session, club_slugs: dict[int, str]) -> int:
    updated = 0
    rows = session.query(PayPalPayment).filter(PayPalPayment.method_owner.is_(None)).all()
    for row in rows:
        row.method_owner = infer_method_owner_for_backfill(
            source="deposit_paypal",
            variant=row.paypal_email,
            club_slug=_club_slug_for_row(
                club_id=row.club_id,
                alert_scope=None,
                club_slugs=club_slugs,
            ),
            memo=row.memo,
        )
        updated += 1
    return updated


def _backfill_crypto(session, club_slugs: dict[int, str]) -> int:
    updated = 0
    rows = session.query(CryptoPayment).filter(CryptoPayment.method_owner.is_(None)).all()
    for row in rows:
        row.method_owner = infer_method_owner_for_backfill(
            source="deposit_crypto",
            variant=row.token_symbol,
            club_slug=_club_slug_for_row(
                club_id=row.club_id,
                alert_scope=row.alert_scope,
                club_slugs=club_slugs,
            ),
            memo=None,
        )
        updated += 1
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


def dry_run_backfill() -> None:
    engine = init_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    previews: dict[str, tuple[int, Counter[str]]] = {}
    with get_db() as session:
        club_slugs = _club_slug_by_id(session)
        if "venmo_payments" in existing_tables:
            previews["venmo_payments"] = _preview_table(
                session,
                table="venmo_payments",
                source="deposit_venmo",
                variant_col="venmo_handle",
                club_slugs=club_slugs,
            )
        if "zelle_payments" in existing_tables:
            previews["zelle_payments"] = _preview_table(
                session,
                table="zelle_payments",
                source="deposit_zelle",
                variant_col="zelle_recipient",
                club_slugs=club_slugs,
            )
        if "cashapp_payments" in existing_tables:
            previews["cashapp_payments"] = _preview_table(
                session,
                table="cashapp_payments",
                source="deposit_cashapp",
                variant_col="cashapp_handle",
                club_slugs=club_slugs,
            )
        if "paypal_payments" in existing_tables:
            previews["paypal_payments"] = _preview_table(
                session,
                table="paypal_payments",
                source="deposit_paypal",
                variant_col="paypal_email",
                club_slugs=club_slugs,
            )
        if "crypto_payments" in existing_tables:
            previews["crypto_payments"] = _preview_table(
                session,
                table="crypto_payments",
                source="deposit_crypto",
                variant_col="token_symbol",
                club_slugs=club_slugs,
                memo_col=None,
                alert_scope_col="alert_scope",
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

    with get_db() as session:
        club_slugs = _club_slug_by_id(session)
        counts = {
            "venmo_payments": _backfill_venmo(session, club_slugs),
            "zelle_payments": _backfill_zelle(session, club_slugs),
            "cashapp_payments": _backfill_cashapp(session, club_slugs),
            "paypal_payments": _backfill_paypal(session, club_slugs),
            "crypto_payments": _backfill_crypto(session, club_slugs),
        }
        session.commit()

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
    args = parser.parse_args()
    if args.dry_run:
        dry_run_backfill()
    else:
        main()
