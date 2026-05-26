"""Identify the sole human player in support groups and bind them for ``/gc`` reuse.

Scans Telethon dialogs for one club MTProto account, applies the same "exactly one
eligible player" rules as contact save, then writes ``support_group_chats.player_telegram_user_id``
so incoming ``/gc`` (DM or outgoing) reuses that megagroup via
``fetch_support_group_chat_by_club_player``.

Dry-run by default; pass ``--apply`` to write Postgres.

Environment: DATABASE_URL, TG_API_ID, TG_API_HASH (same as other MTProto scripts).

Usage:
  python3.11 scripts/backfill_gc_player_bindings.py --club-key round_table
  python3.11 scripts/backfill_gc_player_bindings.py --club-key round_table --apply
  python3.11 scripts/backfill_gc_player_bindings.py --club-key round_table --json
  python3.11 scripts/backfill_gc_player_bindings.py --club-key round_table --quiet
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("backfill_gc_player_bindings")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Python 3.10+ required (this interpreter is {sys.version.split()[0]})."
    )

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

CLUB_KEYS = ("round_table", "creator_club", "clubgto")


@dataclass(frozen=True)
class GroupScanRow:
    chat_id: int
    title: str
    candidate_count: int
    candidate_ids: tuple[int, ...]
    eligible_labels: tuple[str, ...]
    player_telegram_user_id: int | None
    player_username: str | None
    player_display_name: str | None
    gg_player_id: str | None
    bind_status: str | None
    bind_row_id: int | None


@dataclass(frozen=True)
class ScanSummary:
    mtproto_club_key: str
    club_display_name: str
    group_dialogs: int
    sole_player: int
    ambiguous: int
    no_player: int
    apply_mode: bool
    bound_updated: int
    bound_inserted: int
    already_bound: int
    skipped_conflict: int
    errors: int


def _is_group_dialog(dialog) -> bool:
    if dialog.is_group:
        return True
    if dialog.is_channel:
        entity = dialog.entity
        return bool(getattr(entity, "megagroup", False))
    return False


def _configure_logging(*, quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _title_snippet(title: str, max_len: int = 60) -> str:
    t = (title or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _eligible_labels_from_result(sole_result) -> tuple[str, ...]:
    return tuple(
        f"id={p.user_id} {p.username or ''} {p.display_name}".strip()
        for p in sole_result.eligible
    )


def _player_labels(user) -> tuple[str | None, str | None]:
    uname = getattr(user, "username", None)
    username = uname.strip() if isinstance(uname, str) and uname.strip() else None
    fn = (getattr(user, "first_name", None) or "").strip()
    ln = (getattr(user, "last_name", None) or "").strip()
    display = f"{fn} {ln}".strip() or None
    return username, display


async def _scan(club_key: str, *, apply: bool) -> tuple[ScanSummary, list[dict[str, Any]]]:
    from club_gc_settings import CLUB_GC_CONFIG
    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )
    from bot.services.mtproto_group_player import find_sole_player_participant
    from bot.services.player_details import parse_tracking_title
    from bot.services.support_group_chats import bind_player_for_gc_reuse
    from db.connection import init_engine

    cfg = CLUB_GC_CONFIG.get(club_key)
    if not cfg:
        raise SystemExit(f"Unknown club_key: {club_key!r}")

    init_engine()
    logger.info("Database engine ready")

    if not await is_client_authorized(cfg):
        raise SystemExit(
            "Telethon session is not authorized for this club. "
            "Log in via dashboard or scripts/mtproto_login_cli.py"
        )
    logger.info("Telethon session authorized for club_key=%s", club_key)

    rows_out: list[GroupScanRow] = []
    sole = ambiguous = no_player = 0
    bound_updated = bound_inserted = already_bound = skipped_conflict = errors = 0
    group_dialogs = 0

    mode = "APPLY" if apply else "DRY-RUN"
    logger.info(
        "Starting scan club=%s (%s) mode=%s",
        cfg.club_display_name,
        club_key,
        mode,
    )

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        logger.info("Connecting Telethon client...")
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise SystemExit("Telethon not authorized after connect.")

            me = await client.get_me()
            self_id = int(me.id) if me and getattr(me, "id", None) is not None else None
            me_label = getattr(me, "username", None) or getattr(me, "first_name", "?")
            logger.info(
                "Connected as @%s (id=%s); listing dialogs...",
                me_label,
                self_id,
            )

            dialogs_seen = 0
            async for dialog in client.iter_dialogs():
                dialogs_seen += 1
                if dialogs_seen % 50 == 0:
                    logger.info(
                        "Dialog progress: %s scanned (%s groups so far)",
                        dialogs_seen,
                        group_dialogs,
                    )
                if not _is_group_dialog(dialog):
                    continue
                group_dialogs += 1
                chat_id = int(dialog.id)
                title = (dialog.title or dialog.name or "").strip()
                logger.info(
                    "[%s] Checking group chat_id=%s title=%r",
                    group_dialogs,
                    chat_id,
                    _title_snippet(title),
                )

                parsed = parse_tracking_title(title)
                gg_player_id = parsed[1] if parsed else None
                if gg_player_id:
                    logger.info("  Parsed gg_player_id=%s", gg_player_id)

                try:
                    logger.info("  Loading chat entity...")
                    entity = await client.get_entity(chat_id)
                except Exception as e:
                    logger.warning(
                        "  Failed to open chat_id=%s: %s",
                        chat_id,
                        type(e).__name__,
                    )
                    rows_out.append(
                        GroupScanRow(
                            chat_id=chat_id,
                            title=title,
                            candidate_count=-1,
                            candidate_ids=(),
                            eligible_labels=(),
                            player_telegram_user_id=None,
                            player_username=None,
                            player_display_name=None,
                            gg_player_id=gg_player_id,
                            bind_status=f"open_chat_error:{type(e).__name__}",
                            bind_row_id=None,
                        )
                    )
                    errors += 1
                    continue

                try:
                    logger.info("  Listing eligible participants...")
                    sole_result = await find_sole_player_participant(
                        client, entity, cfg, self_id=self_id
                    )
                except Exception as e:
                    logger.warning(
                        "  Failed to list members chat_id=%s: %s",
                        chat_id,
                        type(e).__name__,
                    )
                    rows_out.append(
                        GroupScanRow(
                            chat_id=chat_id,
                            title=title,
                            candidate_count=-1,
                            candidate_ids=(),
                            eligible_labels=(),
                            player_telegram_user_id=None,
                            player_username=None,
                            player_display_name=None,
                            gg_player_id=gg_player_id,
                            bind_status=f"list_members_error:{type(e).__name__}",
                            bind_row_id=None,
                        )
                    )
                    errors += 1
                    continue

                n = sole_result.candidate_count
                if n == 1 and sole_result.user is not None:
                    sole += 1
                    user = sole_result.user
                    pid = int(user.id)
                    username, display = _player_labels(user)
                    logger.info(
                        "  Sole player: id=%s @%s %s",
                        pid,
                        username or "?",
                        display or "",
                    )
                    bind_status: str | None = None
                    bind_row_id: int | None = None

                    if apply:
                        logger.info("  Writing support_group_chats binding...")
                        bind_status, bind_row_id = await asyncio.to_thread(
                            bind_player_for_gc_reuse,
                            club_key=cfg.club_key,
                            club_display_name=cfg.club_display_name,
                            telegram_chat_id=chat_id,
                            telegram_chat_title=title,
                            player_telegram_user_id=pid,
                            player_username=username,
                            player_display_name=display,
                        )
                        if bind_status == "updated":
                            bound_updated += 1
                        elif bind_status == "inserted":
                            bound_inserted += 1
                        elif bind_status == "already_bound":
                            already_bound += 1
                        elif bind_status in (
                            "player_bound_elsewhere",
                            "chat_other_player",
                            "duplicate_club_player",
                        ):
                            skipped_conflict += 1
                        elif bind_status == "error":
                            errors += 1
                        logger.info("  Bind result: %s%s", bind_status, f" row_id={bind_row_id}" if bind_row_id else "")
                    else:
                        bind_status = "would_bind"
                        logger.info("  Would bind player_id=%s (dry-run)", pid)

                    rows_out.append(
                        GroupScanRow(
                            chat_id=chat_id,
                            title=title,
                            candidate_count=1,
                            candidate_ids=sole_result.candidate_ids,
                            eligible_labels=_eligible_labels_from_result(sole_result),
                            player_telegram_user_id=pid,
                            player_username=username,
                            player_display_name=display,
                            gg_player_id=gg_player_id,
                            bind_status=bind_status,
                            bind_row_id=bind_row_id,
                        )
                    )
                elif n == 0:
                    no_player += 1
                    logger.info("  No eligible player (0 candidates)")
                    rows_out.append(
                        GroupScanRow(
                            chat_id=chat_id,
                            title=title,
                            candidate_count=0,
                            candidate_ids=(),
                            eligible_labels=(),
                            player_telegram_user_id=None,
                            player_username=None,
                            player_display_name=None,
                            gg_player_id=gg_player_id,
                            bind_status=None,
                            bind_row_id=None,
                        )
                    )
                else:
                    ambiguous += 1
                    eligible_labels = _eligible_labels_from_result(sole_result)
                    logger.info("  Ambiguous: %s eligible humans (need exactly 1):", n)
                    for label in eligible_labels:
                        logger.info("    %s", label)
                    rows_out.append(
                        GroupScanRow(
                            chat_id=chat_id,
                            title=title,
                            candidate_count=n,
                            candidate_ids=sole_result.candidate_ids,
                            eligible_labels=eligible_labels,
                            player_telegram_user_id=None,
                            player_username=None,
                            player_display_name=None,
                            gg_player_id=gg_player_id,
                            bind_status=None,
                            bind_row_id=None,
                        )
                    )
        finally:
            logger.info("Disconnecting Telethon client...")
            await client.disconnect()

    logger.info(
        "Scan complete: groups=%s sole=%s ambiguous=%s no_player=%s errors=%s",
        group_dialogs,
        sole,
        ambiguous,
        no_player,
        errors,
    )
    if apply:
        logger.info(
            "Apply totals: inserted=%s updated=%s already_bound=%s conflicts=%s",
            bound_inserted,
            bound_updated,
            already_bound,
            skipped_conflict,
        )

    summary = ScanSummary(
        mtproto_club_key=club_key,
        club_display_name=cfg.club_display_name,
        group_dialogs=group_dialogs,
        sole_player=sole,
        ambiguous=ambiguous,
        no_player=no_player,
        apply_mode=apply,
        bound_updated=bound_updated,
        bound_inserted=bound_inserted,
        already_bound=already_bound,
        skipped_conflict=skipped_conflict,
        errors=errors,
    )
    payload = [asdict(r) for r in rows_out]
    return summary, payload


def _print_human(summary: ScanSummary, rows: list[dict[str, Any]]) -> None:
    s = summary
    mode = "APPLY" if s.apply_mode else "DRY-RUN"
    print(f"GC player binding backfill ({mode}) — {s.club_display_name} [{s.mtproto_club_key}]")
    print(
        f"Groups scanned: {s.group_dialogs} | sole player: {s.sole_player} | "
        f"ambiguous: {s.ambiguous} | no eligible player: {s.no_player}"
    )
    if s.apply_mode:
        print(
            f"Applied: inserted={s.bound_inserted} updated={s.bound_updated} "
            f"already_bound={s.already_bound} conflicts={s.skipped_conflict} errors={s.errors}"
        )
    print()

    bind_rows = [r for r in rows if r.get("candidate_count") == 1]
    if bind_rows:
        print("--- Sole player (gc bind candidates) ---")
        for r in bind_rows:
            print(f"  chat_id={r['chat_id']}")
            print(f"    title: {r['title']}")
            if r.get("gg_player_id"):
                print(f"    gg_player_id: {r['gg_player_id']}")
            print(
                f"    player: id={r['player_telegram_user_id']} "
                f"@{r.get('player_username') or '?'} {r.get('player_display_name') or ''}"
            )
            if r.get("bind_status"):
                extra = f" row_id={r['bind_row_id']}" if r.get("bind_row_id") else ""
                print(f"    bind: {r['bind_status']}{extra}")
        print()

    amb = [r for r in rows if r.get("candidate_count", 0) > 1]
    if amb:
        print(f"--- Ambiguous ({len(amb)} groups, not bound) ---")
        for r in amb[:30]:
            print(f"  chat_id={r['chat_id']} count={r['candidate_count']}")
            print(f"    title: {r['title']}")
            for label in r.get("eligible_labels") or []:
                print(f"    - {label}")
        if len(amb) > 30:
            print(f"  ... and {len(amb) - 30} more")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find sole human per support group and bind for /gc reuse."
    )
    parser.add_argument("--club-key", required=True, choices=CLUB_KEYS)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write support_group_chats.player_telegram_user_id (default: report only).",
    )
    parser.add_argument("--json", action="store_true", help="JSON to stdout.")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only warnings/errors on stderr (no per-group progress).",
    )
    args = parser.parse_args()

    if not args.json:
        _configure_logging(quiet=args.quiet)

    summary, rows = asyncio.run(_scan(args.club_key, apply=args.apply))

    if args.json:
        print(json.dumps({"summary": asdict(summary), "groups": rows}, indent=2))
    else:
        _print_human(summary, rows)

    if summary.errors and args.apply:
        sys.exit(2)
    if summary.skipped_conflict and not args.apply:
        sys.exit(0)


if __name__ == "__main__":
    main()
