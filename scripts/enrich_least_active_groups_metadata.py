"""Enrich a least-active groups CSV with Postgres + Telethon metadata for analysis.

Reads chat ids from ``list_least_active_telegram_groups.py`` output (or similar),
joins bot DB signals, and fetches per-group Telegram fields (participant count,
eligible player roster).

Operational: pause worker MTProto before running locally (``GC_MTPROTO_ENABLED=false``).

Usage:
  python scripts/enrich_least_active_groups_metadata.py \\
    --csv backups/least_active_megagroups_v2.csv \\
    --club-key round_table \\
    --output backups/least_active_megagroups_v2_enriched.csv

  python scripts/enrich_least_active_groups_metadata.py \\
    --csv backups/least_active_megagroups_v2.csv --chat-id -1003931597118 --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("enrich_least_active_groups_metadata")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

OUTPUT_COLUMNS = (
    "rank",
    "title",
    "chat_id",
    "inactive_days",
    "inactive_label",
    "activity_basis",
    "duplicate_title",
    "archived",
    "shorthand",
    "gg_player_id",
    "title_tail",
    "is_tracking_title",
    "db_in_groups",
    "db_groups_club_id",
    "db_groups_name",
    "db_player_details_gg_player_id",
    "db_support_player_telegram_user_id",
    "db_support_player_username",
    "db_migrated_old_chat_id",
    "db_outreach_inactive_90d",
    "db_outreach_inactive_180d",
    "db_outreach_scanned_at",
    "db_payment_count_180d",
    "db_payment_total_usd",
    "db_last_payment_at",
    "db_bind_count_180d",
    "db_cashout_count_180d",
    "telegram_participants_count",
    "telegram_eligible_player_count",
    "telegram_eligible_player_ids",
    "telegram_public_username",
    "telegram_dialog_unread_count",
    "telegram_fetch_error",
    "enriched_at_utc",
)


@dataclass(frozen=True)
class InputRow:
    rank: str
    title: str
    chat_id: int
    fields: dict[str, str]


@dataclass
class DbContext:
    groups_by_chat: dict[int, tuple[int, str]]
    player_details_by_chat: dict[int, str]
    support_by_chat: dict[int, tuple[int | None, str | None]]
    migrated_old_by_current: dict[int, int]
    outreach_by_chat: dict[int, dict[str, Any]]
    payments_by_chat: dict[int, tuple[int, int, datetime | None]]
    binds_by_chat: dict[int, int]
    cashouts_by_chat: dict[int, int]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich least-active group CSV with DB + Telethon metadata."
    )
    parser.add_argument("--csv", type=Path, required=True, help="Input CSV path.")
    parser.add_argument(
        "--club-key",
        choices=CLUB_KEYS,
        default="round_table",
        help="Club MTProto session (default: round_table).",
    )
    parser.add_argument("--output", type=Path, default=None, help="Output CSV path.")
    parser.add_argument("--chat-id", type=int, default=None, help="Enrich one chat id.")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process.")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N input rows.")
    parser.add_argument(
        "--activity-days",
        type=int,
        default=180,
        help="Lookback for DB payment/bind/cashout counts (default: 180).",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _load_input_rows(path: Path) -> list[InputRow]:
    rows: list[InputRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            cid_raw = (raw.get("chat_id") or "").strip()
            if not cid_raw.lstrip("-").isdigit():
                continue
            chat_id = int(cid_raw)
            title = (raw.get("title") or "").strip()
            rows.append(
                InputRow(
                    rank=(raw.get("rank") or "").strip(),
                    title=title,
                    chat_id=chat_id,
                    fields=dict(raw),
                )
            )
    return rows


def _variant_map(chat_ids: list[int]) -> dict[int, int]:
    from notification.chat_id import telegram_chat_id_variants

    out: dict[int, int] = {}
    for cid in chat_ids:
        for variant in telegram_chat_id_variants(int(cid)):
            out[int(variant)] = int(cid)
    return out


def _load_db_context(chat_ids: list[int], *, activity_days: int) -> DbContext:
    from sqlalchemy import func

    from db.connection import get_db
    from db.models import (
        CashierCashoutJob,
        Group,
        GroupPaymentMethodBinding,
        InactiveGroupOutreachRow,
        MigratedGroupRecovery,
        PaymentMethodBindAttempt,
        PlayerDetails,
        StripeCheckoutSession,
        SupportGroupChat,
    )

    variants = _variant_map(chat_ids)
    variant_ids = list(variants.keys())
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(activity_days)))

    groups_by_chat: dict[int, tuple[int, str]] = {}
    player_details_by_chat: dict[int, str] = {}
    support_by_chat: dict[int, tuple[int | None, str | None]] = {}
    migrated_old_by_current: dict[int, int] = {}
    outreach_by_chat: dict[int, dict[str, Any]] = {}
    payments_by_chat: dict[int, tuple[int, int, datetime | None]] = {}
    binds_by_chat: dict[int, int] = {}
    cashouts_by_chat: dict[int, int] = {}

    if not variant_ids:
        return DbContext(
            groups_by_chat=groups_by_chat,
            player_details_by_chat=player_details_by_chat,
            support_by_chat=support_by_chat,
            migrated_old_by_current=migrated_old_by_current,
            outreach_by_chat=outreach_by_chat,
            payments_by_chat=payments_by_chat,
            binds_by_chat=binds_by_chat,
            cashouts_by_chat=cashouts_by_chat,
        )

    with get_db() as session:
        for chat_id, club_id, name in session.query(
            Group.chat_id, Group.club_id, Group.name
        ).filter(Group.chat_id.in_(variant_ids)):
            canonical = variants.get(int(chat_id))
            if canonical is not None and canonical not in groups_by_chat:
                groups_by_chat[canonical] = (int(club_id), str(name or ""))

        for row in session.query(PlayerDetails).filter(
            PlayerDetails.chat_ids.isnot(None)
        ):
            gg = str(row.gg_player_id or "")
            for raw_cid in row.chat_ids or []:
                canonical = variants.get(int(raw_cid))
                if canonical is not None and canonical not in player_details_by_chat:
                    player_details_by_chat[canonical] = gg

        for raw_cid, player_id, username in session.query(
            SupportGroupChat.telegram_chat_id,
            SupportGroupChat.player_telegram_user_id,
            SupportGroupChat.player_username,
        ).filter(SupportGroupChat.telegram_chat_id.in_(variant_ids)):
            canonical = variants.get(int(raw_cid))
            if canonical is not None and canonical not in support_by_chat:
                support_by_chat[canonical] = (
                    int(player_id) if player_id is not None else None,
                    str(username) if username else None,
                )

        for current_id, old_id in session.query(
            MigratedGroupRecovery.telegram_chat_id,
            MigratedGroupRecovery.old_chat_id,
        ).filter(MigratedGroupRecovery.telegram_chat_id.in_(variant_ids)):
            canonical = variants.get(int(current_id))
            if canonical is not None and canonical not in migrated_old_by_current:
                migrated_old_by_current[canonical] = int(old_id)

        for row in session.query(InactiveGroupOutreachRow).filter(
            InactiveGroupOutreachRow.telegram_chat_id.in_(variant_ids)
        ):
            canonical = variants.get(int(row.telegram_chat_id))
            if canonical is None or canonical in outreach_by_chat:
                continue
            outreach_by_chat[canonical] = {
                "inactive_90d": bool(row.inactive_90d),
                "inactive_180d": bool(row.inactive_180d),
                "scanned_at": row.scanned_at,
            }

        pay_rows = (
            session.query(
                StripeCheckoutSession.telegram_chat_id,
                func.count(StripeCheckoutSession.id),
                func.coalesce(func.sum(StripeCheckoutSession.amount_cents), 0),
                func.max(StripeCheckoutSession.created_at),
            )
            .filter(
                StripeCheckoutSession.telegram_chat_id.in_(variant_ids),
                StripeCheckoutSession.created_at >= since,
            )
            .group_by(StripeCheckoutSession.telegram_chat_id)
            .all()
        )
        for raw_cid, count, total_cents, last_at in pay_rows:
            canonical = variants.get(int(raw_cid))
            if canonical is not None and canonical not in payments_by_chat:
                payments_by_chat[canonical] = (
                    int(count or 0),
                    int(total_cents or 0),
                    last_at,
                )

        bind_rows = (
            session.query(
                PaymentMethodBindAttempt.telegram_chat_id,
                func.count(PaymentMethodBindAttempt.id),
            )
            .filter(
                PaymentMethodBindAttempt.telegram_chat_id.in_(variant_ids),
                PaymentMethodBindAttempt.created_at >= since,
            )
            .group_by(PaymentMethodBindAttempt.telegram_chat_id)
            .all()
        )
        for raw_cid, count in bind_rows:
            canonical = variants.get(int(raw_cid))
            if canonical is not None:
                binds_by_chat[canonical] = int(count or 0)

        cashout_rows = (
            session.query(
                CashierCashoutJob.chat_id,
                func.count(CashierCashoutJob.id),
            )
            .filter(
                CashierCashoutJob.chat_id.in_(variant_ids),
                CashierCashoutJob.created_at >= since,
            )
            .group_by(CashierCashoutJob.chat_id)
            .all()
        )
        for raw_cid, count in cashout_rows:
            canonical = variants.get(int(raw_cid))
            if canonical is not None:
                cashouts_by_chat[canonical] = int(count or 0)

    return DbContext(
        groups_by_chat=groups_by_chat,
        player_details_by_chat=player_details_by_chat,
        support_by_chat=support_by_chat,
        migrated_old_by_current=migrated_old_by_current,
        outreach_by_chat=outreach_by_chat,
        payments_by_chat=payments_by_chat,
        binds_by_chat=binds_by_chat,
        cashouts_by_chat=cashouts_by_chat,
    )


async def _build_dialog_map(client) -> dict[int, dict[str, Any]]:
    from telethon.utils import get_peer_id

    out: dict[int, dict[str, Any]] = {}
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        if not getattr(ent, "megagroup", False):
            continue
        cid = int(get_peer_id(ent))
        out[cid] = {
            "archived": bool(getattr(dialog, "archived", False)),
            "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
            "dialog_date": dialog.date,
            "title": (dialog.title or "").strip(),
        }
    return out


async def _telegram_fields(
    client,
    cfg,
    chat_id: int,
    *,
    dialog_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    from bot.services.migration_group_readd import participant_count
    from bot.services.mtproto_group_player import collect_eligible_player_participants

    fields: dict[str, Any] = {
        "telegram_participants_count": "",
        "telegram_eligible_player_count": "",
        "telegram_eligible_player_ids": "",
        "telegram_public_username": "",
        "telegram_dialog_unread_count": "",
        "telegram_fetch_error": "",
    }

    dialog = dialog_map.get(int(chat_id))
    if dialog is not None:
        fields["telegram_dialog_unread_count"] = dialog.get("unread_count", "")

    try:
        entity = await client.get_entity(int(chat_id))
        fields["telegram_public_username"] = (
            getattr(entity, "username", None) or ""
        )
        fields["telegram_participants_count"] = await participant_count(client, entity)

        me = await client.get_me()
        self_id = int(me.id) if me and getattr(me, "id", None) else None
        eligible = await collect_eligible_player_participants(
            client,
            entity,
            cfg,
            self_id=self_id,
        )
        ids = [str(int(u.id)) for u in eligible if getattr(u, "id", None) is not None]
        fields["telegram_eligible_player_count"] = len(ids)
        fields["telegram_eligible_player_ids"] = ",".join(ids)
    except Exception as exc:
        fields["telegram_fetch_error"] = type(exc).__name__

    return fields


def _title_fields(title: str) -> dict[str, str]:
    from bot.services.player_details import parse_group_title_parts, parse_tracking_title

    parsed = parse_group_title_parts(title)
    tracking = parse_tracking_title(title)
    if parsed:
        from bot.services.player_details import format_title_prefix_segment

        shorthand = format_title_prefix_segment(set(parsed.shorthands))
        return {
            "shorthand": shorthand,
            "gg_player_id": parsed.gg_player_id,
            "title_tail": parsed.tail,
            "is_tracking_title": "yes" if tracking else "no",
        }
    return {
        "shorthand": "",
        "gg_player_id": "",
        "title_tail": "",
        "is_tracking_title": "no",
    }


def _format_row(
    item: InputRow,
    *,
    db: DbContext,
    telegram: dict[str, Any],
    enriched_at: datetime,
) -> dict[str, str]:
    title = item.title
    chat_id = int(item.chat_id)
    title_meta = _title_fields(title)

    group = db.groups_by_chat.get(chat_id)
    support = db.support_by_chat.get(chat_id)
    outreach = db.outreach_by_chat.get(chat_id, {})
    payments = db.payments_by_chat.get(chat_id)

    payment_count = ""
    payment_usd = ""
    last_payment = ""
    if payments:
        payment_count = str(payments[0])
        payment_usd = f"{payments[1] / 100:.2f}"
        if payments[2] is not None:
            last_payment = payments[2].astimezone(timezone.utc).strftime("%Y-%m-%d")

    scanned_at = outreach.get("scanned_at")
    return {
        "rank": item.rank or item.fields.get("rank", ""),
        "title": title,
        "chat_id": str(chat_id),
        "inactive_days": item.fields.get("inactive_days", ""),
        "inactive_label": item.fields.get("inactive_label", ""),
        "activity_basis": item.fields.get("activity_basis", ""),
        "duplicate_title": item.fields.get("duplicate_title", ""),
        "archived": item.fields.get("archived", ""),
        **title_meta,
        "db_in_groups": "yes" if group else "no",
        "db_groups_club_id": str(group[0]) if group else "",
        "db_groups_name": group[1] if group else "",
        "db_player_details_gg_player_id": db.player_details_by_chat.get(chat_id, ""),
        "db_support_player_telegram_user_id": (
            str(support[0]) if support and support[0] is not None else ""
        ),
        "db_support_player_username": support[1] if support and support[1] else "",
        "db_migrated_old_chat_id": str(db.migrated_old_by_current.get(chat_id, "")),
        "db_outreach_inactive_90d": (
            str(outreach.get("inactive_90d")) if outreach else ""
        ),
        "db_outreach_inactive_180d": (
            str(outreach.get("inactive_180d")) if outreach else ""
        ),
        "db_outreach_scanned_at": (
            scanned_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
            if scanned_at is not None
            else ""
        ),
        "db_payment_count_180d": payment_count,
        "db_payment_total_usd": payment_usd,
        "db_last_payment_at": last_payment,
        "db_bind_count_180d": str(db.binds_by_chat.get(chat_id, "")),
        "db_cashout_count_180d": str(db.cashouts_by_chat.get(chat_id, "")),
        "telegram_participants_count": str(
            telegram.get("telegram_participants_count", "")
        ),
        "telegram_eligible_player_count": str(
            telegram.get("telegram_eligible_player_count", "")
        ),
        "telegram_eligible_player_ids": str(
            telegram.get("telegram_eligible_player_ids", "")
        ),
        "telegram_public_username": str(telegram.get("telegram_public_username", "")),
        "telegram_dialog_unread_count": str(
            telegram.get("telegram_dialog_unread_count", "")
        ),
        "telegram_fetch_error": str(telegram.get("telegram_fetch_error", "")),
        "enriched_at_utc": enriched_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def _run(args: argparse.Namespace) -> int:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import is_client_authorized, make_client

    cfg = CLUB_GC_CONFIG.get(args.club_key)
    if cfg is None:
        raise SystemExit(f"Unknown club_key: {args.club_key!r}")

    if not args.csv.is_file():
        raise SystemExit(f"CSV not found: {args.csv}")

    rows = _load_input_rows(args.csv)
    if args.chat_id is not None:
        rows = [r for r in rows if int(r.chat_id) == int(args.chat_id)]
    if args.skip:
        rows = rows[int(args.skip) :]
    if args.limit is not None:
        rows = rows[: max(0, int(args.limit))]

    if not rows:
        raise SystemExit("No input rows to enrich.")

    output = args.output or args.csv.with_name(f"{args.csv.stem}_enriched.csv")
    chat_ids = [int(r.chat_id) for r in rows]

    logger.info("Loading DB context for %d chat ids…", len(chat_ids))
    db = _load_db_context(chat_ids, activity_days=int(args.activity_days))

    if not await is_client_authorized(cfg):
        raise SystemExit(
            f"MTProto session not authorized for {args.club_key!r}. "
            "Complete Dashboard → Telegram login first."
        )

    client = make_client(cfg)
    await client.connect()
    enriched_at = datetime.now(timezone.utc)
    out_rows: list[dict[str, str]] = []
    try:
        if not await client.is_user_authorized():
            raise SystemExit(f"MTProto session not authorized for {args.club_key!r}.")

        logger.info("Building megagroup dialog map…")
        dialog_map = await _build_dialog_map(client)

        for idx, item in enumerate(rows, start=1):
            if idx % 25 == 0 or idx == 1:
                logger.info("Telegram enrich %d/%d: %s", idx, len(rows), item.title[:50])
            telegram = await _telegram_fields(
                client,
                cfg,
                int(item.chat_id),
                dialog_map=dialog_map,
            )
            out_rows.append(
                _format_row(item, db=db, telegram=telegram, enriched_at=enriched_at)
            )
    finally:
        await client.disconnect()

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows → {output}")
    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
