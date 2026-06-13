"""MTProto membership audit for migration recovery tier 1+2 groups.

Uses the same eligible-player rules as auto contact save
(``find_sole_player_participant`` in ``bot/services/mtproto_group_player.py``):
non-bot humans excluding MTProto self, GC_USERS/bot invitees, operators, and
dashboard admins.

Membership: ``player_in_group`` is true when at least one eligible human is
present (including ambiguous multi-player groups). ``player_telegram_user_id``
is updated only when exactly one eligible human is found (same gate as contact
save).

Dry-run by default; pass ``--apply`` to write Postgres
(``migrated_group_recovery`` + ``support_group_chats`` via ``bind_player_for_gc_reuse``).

Do not run against a club while the Heroku worker holds the same Telethon
session unless ``GC_MTPROTO_ENABLED=false`` on the worker.

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (club MTProto sessions).

Usage:
  python scripts/check_recovery_player_membership.py
  python scripts/check_recovery_player_membership.py --row-id 1
  python scripts/check_recovery_player_membership.py --club clubgto
  python scripts/check_recovery_player_membership.py --apply
  python scripts/check_recovery_player_membership.py --from-csv backups/recovery_player_membership_20260612_041635.csv
  python scripts/check_recovery_player_membership.py --from-csv backups/recovery_player_membership_20260612_041635.csv --apply
  python scripts/check_recovery_player_membership.py -o backups/recovery_player_membership.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")

CSV_FIELDS = (
    "row_id",
    "club_key",
    "priority_tier",
    "group_title",
    "telegram_chat_id",
    "player_telegram_user_id",
    "player_username",
    "readd_status",
    "eligible_player_count",
    "eligible_player_ids",
    "player_in_group",
    "player_id_updated",
    "bind_status",
    "check_error",
)


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return _REPO_ROOT / "backups" / f"recovery_player_membership_{stamp}.csv"


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"CSV is empty: {path}")
    missing = set(CSV_FIELDS) - set(rows[0].keys())
    if missing:
        raise SystemExit(f"CSV missing columns {sorted(missing)}: {path}")
    return rows


def _csv_row_in_group(row: dict[str, Any]) -> bool:
    return str(row.get("player_in_group") or "").strip().lower() == "true"


def _build_stats_lines(
    csv_rows: list[dict[str, Any]],
    *,
    tiers: tuple[int, ...],
    label: str,
) -> list[str]:
    stats = defaultdict(lambda: {"total": 0, "in": 0, "out": 0, "errors": 0})
    status_cross = defaultdict(lambda: {"in": 0, "out": 0, "errors": 0})

    for row in csv_rows:
        club = str(row["club_key"])
        tier = int(row["priority_tier"])
        status = str(row["readd_status"])
        err = str(row.get("check_error") or "").strip()
        if err:
            for key in ((club, tier), (club, "all"), ("ALL", 0)):
                stats[key]["errors"] += 1
            status_cross[status]["errors"] += 1
            continue
        in_group = _csv_row_in_group(row)
        for key in ((club, tier), (club, "all"), ("ALL", 0)):
            stats[key]["total"] += 1
            stats[key]["in" if in_group else "out"] += 1
        status_cross[status]["in" if in_group else "out"] += 1

    lines = [
        label,
        f"Total rows: {len(csv_rows)}",
    ]
    a = stats[("ALL", 0)]
    pct = 100 * a["in"] / a["total"] if a["total"] else 0
    lines.append(
        f"Player in group (>=1 eligible non-support human):\n"
        f"  ALL: {a['in']}/{a['total']} ({pct:.1f}%) | not in: {a['out']} | errors: {a['errors']}"
    )
    for club in CLUB_KEYS:
        s = stats.get((club, "all"))
        if not s or (s["total"] == 0 and s["errors"] == 0):
            continue
        pct = 100 * s["in"] / s["total"] if s["total"] else 0
        lines.append(
            f"  {club}: {s['in']}/{s['total']} ({pct:.1f}%) "
            f"| not in: {s['out']} | errors: {s['errors']}"
        )
        for t in tiers:
            st = stats.get((club, t))
            if st and (st["total"] or st["errors"]):
                pct = 100 * st["in"] / st["total"] if st["total"] else 0
                lines.append(
                    f"    tier{t}: {st['in']}/{st['total']} ({pct:.1f}%) "
                    f"| not in: {st['out']} | errors: {st['errors']}"
                )

    lines.append("By readd_status:")
    for status in sorted(status_cross):
        sc = status_cross[status]
        total = sc["in"] + sc["out"]
        pct = 100 * sc["in"] / total if total else 0
        err_note = f" | errors: {sc['errors']}" if sc["errors"] else ""
        lines.append(
            f"  {status}: {sc['in']}/{total} ({pct:.1f}%) | not in: {sc['out']}{err_note}"
        )
    return lines


def _print_stats(
    csv_rows: list[dict[str, Any]],
    *,
    tiers: tuple[int, ...],
    label: str,
) -> None:
    for line in _build_stats_lines(csv_rows, tiers=tiers, label=label):
        print(line)


async def _post_slack_audit(
    *,
    summary_lines: list[str],
    source_csv: str | None = None,
    output_csv: str | None = None,
) -> bool:
    from bot.services.slack_ops_format import format_recovery_membership_audit_slack
    from bot.services.slack_ops_notify import notify_slack_ops

    text = format_recovery_membership_audit_slack(
        summary_lines=summary_lines,
        source_csv=source_csv,
        output_csv=output_csv,
    )
    return await notify_slack_ops(text, source="recovery_membership_audit")


def _filter_csv_rows(
    rows: list[dict[str, Any]],
    *,
    club_filter: str | None,
    row_id: int | None,
) -> list[dict[str, Any]]:
    out = rows
    if club_filter:
        out = [r for r in out if str(r["club_key"]) == club_filter]
    if row_id is not None:
        out = [r for r in out if int(r["row_id"]) == int(row_id)]
    return out


def _apply_from_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.migration_recovery import (
        maybe_finalize_recovery_row_from_membership,
    )
    from bot.services.support_group_chats import bind_player_for_gc_reuse

    out = dict(row)
    err = str(row.get("check_error") or "").strip()
    if err:
        out["bind_status"] = "skipped_error"
        out["player_id_updated"] = False
        return out

    in_group = _csv_row_in_group(row)
    ids_raw = str(row.get("eligible_player_ids") or "").strip()
    eligible_ids = tuple(
        int(x.strip()) for x in ids_raw.split(",") if x.strip()
    )
    if in_group and eligible_ids:
        if maybe_finalize_recovery_row_from_membership(
            int(row["row_id"]),
            eligible_player_ids=eligible_ids,
        ):
            out["readd_status"] = "complete"
            out["readd_finalized"] = True

    try:
        count = int(str(row.get("eligible_player_count") or "").strip())
    except ValueError:
        out.setdefault("bind_status", "skipped_not_sole_player")
        out.setdefault("player_id_updated", False)
        return out

    if count != 1:
        out.setdefault("bind_status", "skipped_not_sole_player")
        out.setdefault("player_id_updated", False)
        return out

    if not ids_raw or "," in ids_raw:
        out["bind_status"] = "skipped_bad_player_id"
        out["player_id_updated"] = False
        return out

    pid = int(ids_raw)
    club_key = str(row["club_key"])
    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        out["bind_status"] = "skipped_unknown_club"
        out["player_id_updated"] = False
        return out

    username_raw = str(row.get("player_username") or "").strip()
    username = username_raw.lstrip("@") or None
    display = None

    recovery_changed = _update_recovery_row_player(
        row_id=int(row["row_id"]),
        player_telegram_user_id=pid,
        player_username=username,
        player_display_name=display,
    )
    bind_status, _bind_row_id = bind_player_for_gc_reuse(
        club_key=cfg.club_key,
        club_display_name=cfg.club_display_name,
        telegram_chat_id=int(row["telegram_chat_id"]),
        telegram_chat_title=str(row["group_title"]),
        player_telegram_user_id=pid,
        player_username=username,
        player_display_name=display,
    )
    out["player_telegram_user_id"] = pid
    if username:
        out["player_username"] = username
    out["bind_status"] = bind_status
    out["player_id_updated"] = recovery_changed or bind_status in ("updated", "inserted")
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Wrote {len(rows)} rows to {path}")


@dataclass(frozen=True)
class RecoveryRowItem:
    row_id: int
    club_key: str
    priority_tier: int
    group_title: str
    telegram_chat_id: int
    player_telegram_user_id: int | None
    player_username: str
    readd_status: str


def _player_labels(user: Any) -> tuple[str | None, str | None]:
    uname = getattr(user, "username", None)
    username = uname.strip() if isinstance(uname, str) and uname.strip() else None
    fn = (getattr(user, "first_name", None) or "").strip()
    ln = (getattr(user, "last_name", None) or "").strip()
    display = f"{fn} {ln}".strip() or None
    return username, display


def _load_recovery_rows(
    *,
    tiers: tuple[int, ...],
    club_filter: str | None,
    row_id: int | None = None,
) -> list[RecoveryRowItem]:
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    with get_db() as session:
        q = session.query(MigratedGroupRecovery).filter(
            MigratedGroupRecovery.priority_tier.in_(tiers)
        )
        if row_id is not None:
            q = q.filter(MigratedGroupRecovery.id == int(row_id))
        if club_filter:
            q = q.filter(MigratedGroupRecovery.club_key == club_filter)
        rows = q.order_by(
            MigratedGroupRecovery.club_key,
            MigratedGroupRecovery.priority_tier,
            MigratedGroupRecovery.id,
        ).all()
        return [
            RecoveryRowItem(
                row_id=int(r.id),
                club_key=str(r.club_key),
                priority_tier=int(r.priority_tier),
                group_title=str(r.group_title or ""),
                telegram_chat_id=int(r.telegram_chat_id),
                player_telegram_user_id=(
                    int(r.player_telegram_user_id) if r.player_telegram_user_id else None
                ),
                player_username=str(r.player_username or ""),
                readd_status=str(r.readd_status),
            )
            for r in rows
        ]


def _update_recovery_row_player(
    *,
    row_id: int,
    player_telegram_user_id: int,
    player_username: str | None,
    player_display_name: str | None,
) -> bool:
    from db.connection import get_db
    from db.models import MigratedGroupRecovery

    pid = int(player_telegram_user_id)
    un = (player_username or "").strip() or None
    dn = (player_display_name or "").strip() or None
    with get_db() as session:
        row = session.query(MigratedGroupRecovery).filter(MigratedGroupRecovery.id == int(row_id)).first()
        if row is None:
            return False
        changed = False
        if row.player_telegram_user_id != pid:
            row.player_telegram_user_id = pid
            changed = True
        if un is not None and (row.player_username or "") != un:
            row.player_username = un
            changed = True
        if dn is not None and (row.player_display_name or "") != dn:
            row.player_display_name = dn
            changed = True
        return changed


async def _scan_club_rows(
    club_key: str,
    items: list[RecoveryRowItem],
    *,
    apply: bool,
    delay_sec: float,
) -> list[dict[str, Any]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )
    from bot.services.mtproto_group_player import find_sole_player_participant
    from bot.services.support_group_chats import bind_player_for_gc_reuse

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        return [
            _error_csv_row(item, f"unknown_club:{club_key}")
            for item in items
        ]

    if not await is_client_authorized(cfg):
        return [
            _error_csv_row(item, "mtproto_unauthorized")
            for item in items
        ]

    out: list[dict[str, Any]] = []
    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return [
                    _error_csv_row(item, "mtproto_unauthorized")
                    for item in items
                ]

            me = await client.get_me()
            self_id = int(me.id) if me and getattr(me, "id", None) is not None else None

            for i, item in enumerate(items, 1):
                csv_row = await _scan_one_row(
                    client=client,
                    cfg=cfg,
                    item=item,
                    self_id=self_id,
                    apply=apply,
                    bind_player_for_gc_reuse=bind_player_for_gc_reuse,
                    update_recovery_row_player=_update_recovery_row_player,
                )
                out.append(csv_row)
                if i % 25 == 0:
                    print(f"  {club_key}: scanned {i}/{len(items)}", flush=True)
                if delay_sec > 0:
                    await asyncio.sleep(delay_sec)
        finally:
            await client.disconnect()
    return out


def _error_csv_row(item: RecoveryRowItem, error: str) -> dict[str, Any]:
    return {
        "row_id": item.row_id,
        "club_key": item.club_key,
        "priority_tier": item.priority_tier,
        "group_title": item.group_title,
        "telegram_chat_id": item.telegram_chat_id,
        "player_telegram_user_id": item.player_telegram_user_id or "",
        "player_username": item.player_username,
        "readd_status": item.readd_status,
        "eligible_player_count": "",
        "eligible_player_ids": "",
        "player_in_group": "",
        "player_id_updated": "",
        "bind_status": "",
        "check_error": error,
    }


async def _scan_one_row(
    *,
    client: Any,
    cfg: Any,
    item: RecoveryRowItem,
    self_id: int | None,
    apply: bool,
    bind_player_for_gc_reuse: Any,
    update_recovery_row_player: Any,
) -> dict[str, Any]:
    base = {
        "row_id": item.row_id,
        "club_key": item.club_key,
        "priority_tier": item.priority_tier,
        "group_title": item.group_title,
        "telegram_chat_id": item.telegram_chat_id,
        "player_telegram_user_id": item.player_telegram_user_id or "",
        "player_username": item.player_username,
        "readd_status": item.readd_status,
    }

    from bot.services.recovery_membership_check import mtproto_check_group_membership

    check = await mtproto_check_group_membership(
        client,
        cfg,
        telegram_chat_id=int(item.telegram_chat_id),
        self_id=self_id,
    )
    if check.error:
        return {
            **base,
            "eligible_player_count": "",
            "eligible_player_ids": "",
            "player_in_group": "",
            "player_id_updated": "",
            "bind_status": "",
            "check_error": check.error,
        }

    count = int(check.eligible_player_count)
    ids_csv = ",".join(str(x) for x in check.eligible_player_ids)
    in_group = check.player_in_group
    sole_user = check.sole_user
    player_id_updated: bool | str = False
    bind_status = ""
    readd_finalized = False
    stored_pid = item.player_telegram_user_id

    if apply and in_group:
        from bot.services.migration_recovery import (
            maybe_finalize_recovery_row_from_membership,
        )

        readd_finalized = await asyncio.to_thread(
            maybe_finalize_recovery_row_from_membership,
            item.row_id,
            eligible_player_ids=check.eligible_player_ids,
        )
        if readd_finalized:
            base["readd_status"] = "complete"

    if count == 1 and sole_user is not None:
        user = sole_user
        pid = int(user.id)
        username, display = _player_labels(user)
        needs_update = stored_pid != pid or (
            username and (item.player_username or "") != username
        )
        if apply:
            recovery_changed = await asyncio.to_thread(
                update_recovery_row_player,
                row_id=item.row_id,
                player_telegram_user_id=pid,
                player_username=username,
                player_display_name=display,
            )
            bind_status, _bind_row_id = await asyncio.to_thread(
                bind_player_for_gc_reuse,
                club_key=cfg.club_key,
                club_display_name=cfg.club_display_name,
                telegram_chat_id=item.telegram_chat_id,
                telegram_chat_title=item.group_title,
                player_telegram_user_id=pid,
                player_username=username,
                player_display_name=display,
            )
            player_id_updated = recovery_changed or bind_status in ("updated", "inserted")
            base["player_telegram_user_id"] = pid
            if username:
                base["player_username"] = username
        elif needs_update:
            player_id_updated = "would_update"
            base["player_telegram_user_id"] = pid
            if username:
                base["player_username"] = username
        else:
            player_id_updated = False

    return {
        **base,
        "eligible_player_count": count,
        "eligible_player_ids": ids_csv,
        "player_in_group": in_group,
        "player_id_updated": player_id_updated,
        "bind_status": bind_status,
        "check_error": "",
        "readd_finalized": readd_finalized,
    }


def _apply_from_csv(
    rows: list[dict[str, Any]],
    *,
    only_would_update: bool,
) -> list[dict[str, Any]]:
    from db.connection import init_engine

    init_engine()
    out: list[dict[str, Any]] = []
    applied = 0
    skipped = 0
    for row in rows:
        err = str(row.get("check_error") or "").strip()
        in_group = _csv_row_in_group(row)
        sole_player = str(row.get("eligible_player_count") or "") == "1"
        if only_would_update and str(row.get("player_id_updated") or "") != "would_update":
            if err or (not in_group and not sole_player):
                out.append(row)
                skipped += 1
                continue
        if err and not in_group:
            out.append(row)
            skipped += 1
            continue
        out.append(_apply_from_csv_row(row))
        applied += 1
    print(f"Applied DB updates to {applied} rows (left {skipped} unchanged)")
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--from-csv",
        type=Path,
        default=None,
        metavar="PATH",
        help="Use an existing audit CSV (stats and/or --apply) instead of MTProto scan",
    )
    parser.add_argument(
        "--only-would-update",
        action="store_true",
        help="With --from-csv --apply, only rows marked would_update in the CSV",
    )
    parser.add_argument(
        "--delay-sec",
        type=float,
        default=0.05,
        help="Sleep between group scans (default 0.05)",
    )
    parser.add_argument(
        "--tiers",
        default="1,2",
        help="Comma-separated priority tiers (default 1,2)",
    )
    parser.add_argument(
        "--club",
        choices=CLUB_KEYS,
        default=None,
        help="Scan one club only",
    )
    parser.add_argument(
        "--row-id",
        type=int,
        default=None,
        help="Scan one migrated_group_recovery row (test before bulk run)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write player ID updates to migrated_group_recovery and support_group_chats",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write per-row results CSV (default: backups/recovery_player_membership_<ts>.csv)",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Post summary to Slack ops (requires SLACK_OPS_* env on this machine)",
    )
    args = parser.parse_args()
    tiers = tuple(int(x.strip()) for x in args.tiers.split(",") if x.strip())

    from bot.services.migration_recovery import HIGH_PRIORITY_TIERS

    if not tiers:
        tiers = HIGH_PRIORITY_TIERS

    if args.from_csv is not None:
        csv_path = args.from_csv
        if not csv_path.is_file():
            print(f"CSV not found: {csv_path}", file=sys.stderr)
            return 1
        csv_rows = _filter_csv_rows(
            _load_csv_rows(csv_path),
            club_filter=args.club,
            row_id=args.row_id,
        )
        if not csv_rows:
            print("No CSV rows matched filters.")
            return 0
        if args.apply:
            mode = "APPLY-FROM-CSV"
            csv_rows = _apply_from_csv(csv_rows, only_would_update=args.only_would_update)
        else:
            mode = "CSV-STATS"
        label = f"Recovery player membership audit (tier {','.join(map(str, tiers))}, MTProto) — {mode}"
        stats_lines = _build_stats_lines(csv_rows, tiers=tiers, label=label)
        apply_lines: list[str] = []
        if args.apply:
            bind_counts: dict[str, int] = defaultdict(int)
            updated = 0
            finalized = 0
            for row in csv_rows:
                bs = str(row.get("bind_status") or "")
                if bs:
                    bind_counts[bs] += 1
                if row.get("player_id_updated") in (True, "True", "true"):
                    updated += 1
                if row.get("readd_finalized") in (True, "True", "true"):
                    finalized += 1
            apply_lines.append(
                f"Applied player ID bindings from CSV ({len(csv_rows)} rows processed)."
            )
            apply_lines.append("")
            apply_lines.append("DB apply results:")
            apply_lines.append(f"  player_id_updated: {updated}")
            apply_lines.append(f"  readd_finalized: {finalized}")
            for bs, n in sorted(bind_counts.items(), key=lambda x: -x[1]):
                apply_lines.append(f"  bind {bs}: {n}")
            for line in apply_lines:
                print(line)
        for line in stats_lines:
            print(line)
        output_path = args.output or _default_output_path()
        csv_rows.sort(
            key=lambda r: (str(r["club_key"]), int(r["priority_tier"]), int(r["row_id"]))
        )
        _write_csv(output_path, csv_rows)
        if args.slack:
            slack_lines = [label] + apply_lines + stats_lines[1:]
            ok = await _post_slack_audit(
                summary_lines=slack_lines,
                source_csv=str(csv_path),
                output_csv=str(output_path),
            )
            print(f"Slack post: {'ok' if ok else 'failed (check SLACK_OPS_* env)'}")
        return 0

    items = _load_recovery_rows(
        tiers=tiers,
        club_filter=args.club,
        row_id=args.row_id,
    )
    if not items:
        print("No recovery rows matched filters.")
        return 0

    by_club: dict[str, list[RecoveryRowItem]] = defaultdict(list)
    for item in items:
        by_club[item.club_key].append(item)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Migration recovery tiers {tiers} — MTProto eligible-player audit ({mode})")
    print(f"Total rows: {len(items)}")

    csv_rows: list[dict[str, Any]] = []

    club_order = [args.club] if args.club else [k for k in CLUB_KEYS if k in by_club]
    for club_key in club_order:
        club_items = by_club.get(club_key, [])
        if not club_items:
            continue
        print(f"Scanning {club_key} ({len(club_items)} rows)...", flush=True)
        club_csv = await _scan_club_rows(
            club_key,
            club_items,
            apply=args.apply,
            delay_sec=args.delay_sec,
        )
        csv_rows.extend(club_csv)

    csv_rows.sort(key=lambda r: (str(r["club_key"]), int(r["priority_tier"]), int(r["row_id"])))

    label = f"Recovery player membership audit (tier {','.join(map(str, tiers))}, MTProto) — {mode}"
    stats_lines = _build_stats_lines(csv_rows, tiers=tiers, label=label)
    for line in stats_lines:
        print(line)

    output_path = args.output or _default_output_path()
    _write_csv(output_path, csv_rows)

    if args.slack:
        ok = await _post_slack_audit(
            summary_lines=stats_lines,
            output_csv=str(output_path),
        )
        print(f"Slack post: {'ok' if ok else 'failed (check SLACK_OPS_* env)'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
