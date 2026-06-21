"""Process one migrated_group_recovery row manually (local / heroku run).

Uses club MTProto session directly (not dm_gc listener). Sends GG Support bot
admin DMs like the worker cron.

Usage:
  python scripts/run_migration_recovery_one.py
  python scripts/run_migration_recovery_one.py --row-id 1
  python scripts/run_migration_recovery_one.py --dry-run
  python scripts/run_migration_recovery_one.py --row-id 1 --elevate-link-join

Do not run while the Heroku worker holds the same club Telethon session unless
GC_MTPROTO_ENABLED=false on the worker.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
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

logger = logging.getLogger("run_migration_recovery_one")


def _load_row(*, row_id: int | None) -> "RecoveryRow | None":
    from sqlalchemy import select

    from bot.services.migration_recovery import RecoveryRow
    from db.connection import get_db, init_engine
    from db.models import MigratedGroupRecovery

    init_engine()
    with get_db() as session:
        q = session.query(MigratedGroupRecovery)
        if row_id is not None:
            row = q.filter(MigratedGroupRecovery.id == int(row_id)).first()
        else:
            row = (
                q.filter(MigratedGroupRecovery.readd_status == "pending")
                .order_by(
                    MigratedGroupRecovery.priority_tier.asc(),
                    MigratedGroupRecovery.priority_rank.asc(),
                )
                .first()
            )
        if row is None:
            return None
        return RecoveryRow(
            id=int(row.id),
            telegram_chat_id=int(row.telegram_chat_id),
            club_key=str(row.club_key),
            club_id=int(row.club_id),
            group_title=str(row.group_title),
            old_chat_id=int(row.old_chat_id),
            player_telegram_user_id=(
                int(row.player_telegram_user_id)
                if row.player_telegram_user_id is not None
                else None
            ),
            player_username=(row.player_username or None),
        )


async def _setup_notify_bot():
    from telegram import Bot

    from bot.services.mtproto_track_contact import set_contact_save_notify_bot

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required for admin DMs")
    bot = Bot(token=token.strip())
    await bot.initialize()
    set_contact_save_notify_bot(bot)
    return bot


def _assert_elevate_trial(row) -> None:
    from club_gc_settings import is_round_table_elevate_recovery_enabled

    if row.club_key != "round_table":
        raise SystemExit("--elevate-link-join requires a round_table recovery row")
    if not is_round_table_elevate_recovery_enabled():
        raise SystemExit(
            "GC_ELEVATE_CREATOR_ROUND_TABLE must be true for --elevate-link-join"
        )


async def _process_elevate_link_join(row, *, apply: bool) -> str:
    from club_gc_settings import get_club_gc_config_by_link_club_id
    from bot.services.migration_group_readd import (
        ReaddGroupResult,
        elevate_join_recovery_group,
        readd_round_table_player_and_link,
    )
    from bot.services.migration_recovery import (
        _load_row_invite_link,
        _notify_rt_ops_if_needed,
        finalize_row,
        maybe_persist_resolved_player_from_readd,
        notify_readd_admin_dm,
    )
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client
    from scripts.backfill_support_group_invite_links import LinkedGroupRow

    _assert_elevate_trial(row)
    cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
    if cfg is None:
        raise SystemExit("No MTProto config for row club_id")

    group = LinkedGroupRow(
        chat_id=int(row.telegram_chat_id),
        club_id=int(row.club_id),
        title=row.group_title,
    )

    async with get_mtproto_lock(cfg.club_key):
        rt_client = make_client(cfg)
        await rt_client.connect()
        try:
            if not await rt_client.is_user_authorized():
                raise RuntimeError(f"Telethon not authorized (club_key={cfg.club_key})")
            me = await rt_client.get_me()
            listener_user_id = int(me.id) if me and getattr(me, "id", None) else None

            rt_result = await readd_round_table_player_and_link(
                client=rt_client,
                cfg=cfg,
                group=group,
                dialog_chat_id=int(row.telegram_chat_id),
                player_id=row.player_telegram_user_id,
                player_username=row.player_username,
                apply=apply,
                update_invite_links=apply,
                listener_user_id=listener_user_id,
                old_chat_id=int(row.old_chat_id),
            )
            if not apply:
                print(f"RT DRY-RUN status={rt_result.status} added={rt_result.added}")
                print(f"already_member={rt_result.already_member}")
                print(f"privacy={rt_result.privacy_blocked} failed={rt_result.failed}")
                print(f"invite_link={'(would export)' if rt_result.invite_link else 'would_export:invite_link'}")
                elevate = await elevate_join_recovery_group(
                    invite_link=rt_result.invite_link or "https://t.me/+dryrun",
                    dialog_chat_id=int(row.telegram_chat_id),
                    rt_client=rt_client,
                    apply=False,
                )
                print(f"Elevate DRY-RUN would_join={not elevate.dry_run} error={elevate.error}")
                return "dry_run"

            try:
                maybe_persist_resolved_player_from_readd(row, rt_result, cfg)
            except Exception:
                logger.warning(
                    "run_migration_recovery_one: persist resolved player failed row_id=%s",
                    row.id,
                    exc_info=True,
                )

            invite_link = rt_result.invite_link or _load_row_invite_link(row.id)
            if not invite_link:
                status = finalize_row(
                    row.id,
                    rt_result,
                    require_elevate=True,
                )
                print("RT pass done but no invite_link; skipping Elevate join")
                await notify_readd_admin_dm(
                    cfg, row=row, result=rt_result, terminal_status=status
                )
                await _notify_rt_ops_if_needed(
                    row=row, result=rt_result, terminal_status=status
                )
                return status

            elevate = await elevate_join_recovery_group(
                invite_link=invite_link,
                dialog_chat_id=int(row.telegram_chat_id),
                rt_client=rt_client,
                apply=True,
            )
            status = finalize_row(
                row.id,
                rt_result,
                elevate=elevate,
                require_elevate=True,
            )
            print(
                f"Elevate join joined={elevate.joined} already_member={elevate.already_member} "
                f"error={elevate.error!r}"
            )
            print(f"invite_link={invite_link}")
            await notify_readd_admin_dm(
                cfg, row=row, result=rt_result, terminal_status=status
            )
            await _notify_rt_ops_if_needed(
                row=row, result=rt_result, terminal_status=status
            )
            return status
        finally:
            await rt_client.disconnect()


async def _process_with_client(row, *, apply: bool) -> str:
    from club_gc_settings import get_club_gc_config_by_link_club_id
    from bot.services.migration_group_readd import ReaddGroupResult, readd_group
    from bot.services.migration_recovery import (
        _notify_rt_ops_if_needed,
        finalize_row,
        maybe_persist_resolved_player_from_readd,
        notify_readd_admin_dm,
    )
    from bot.services.mtproto_group_create import get_mtproto_lock, make_client
    from scripts.backfill_support_group_invite_links import LinkedGroupRow

    cfg = get_club_gc_config_by_link_club_id(int(row.club_id))
    if cfg is None:
        result = ReaddGroupResult(
            chat_id=row.telegram_chat_id,
            club_id=row.club_id,
            club_key=row.club_key,
            title=row.group_title,
            member_count_before=0,
            member_count_after=None,
            status="error",
            error="no_mtproto_config",
        )
        status = finalize_row(row.id, result) if apply else "dry_run"
        return status

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError(f"Telethon not authorized (club_key={cfg.club_key})")
            me = await client.get_me()
            listener_user_id = int(me.id) if me and getattr(me, "id", None) else None
            group = LinkedGroupRow(
                chat_id=int(row.telegram_chat_id),
                club_id=int(row.club_id),
                title=row.group_title,
            )
            result = await readd_group(
                client=client,
                cfg=cfg,
                group=group,
                dialog_chat_id=int(row.telegram_chat_id),
                player_id=row.player_telegram_user_id,
                player_username=row.player_username,
                apply=apply,
                update_invite_links=apply,
                invite_staff=False,
                listener_user_id=listener_user_id,
                old_chat_id=int(row.old_chat_id),
            )
            if not apply:
                print(f"DRY-RUN status={result.status} added={result.added}")
                print(f"already_member={result.already_member}")
                print(f"privacy={result.privacy_blocked} failed={result.failed}")
                if result.resolved_player_source:
                    print(
                        f"resolved_player id={result.resolved_player_id} "
                        f"source={result.resolved_player_source}"
                    )
                return "dry_run"
            try:
                maybe_persist_resolved_player_from_readd(row, result, cfg)
            except Exception:
                logger.warning(
                    "run_migration_recovery_one: persist resolved player failed row_id=%s",
                    row.id,
                    exc_info=True,
                )
            status = finalize_row(row.id, result)
            await notify_readd_admin_dm(
                cfg, row=row, result=result, terminal_status=status
            )
            await _notify_rt_ops_if_needed(
                row=row, result=result, terminal_status=status
            )
            return status
        finally:
            await client.disconnect()


async def _run(
    *,
    row_id: int | None,
    dry_run: bool,
    elevate_link_join: bool,
) -> None:
    from db.connection import init_engine

    init_engine()
    row = _load_row(row_id=row_id)
    if row is None:
        raise SystemExit("No matching migrated_group_recovery row found")

    print(
        f"Target row_id={row.id} club={row.club_key} chat_id={row.telegram_chat_id}\n"
        f"  GC: {row.group_title}\n"
        f"  player_id={row.player_telegram_user_id} username={row.player_username!r}"
    )
    if elevate_link_join:
        print("  mode=elevate-link-join (RT player+link, then Elevate join)")

    bot = await _setup_notify_bot()
    try:
        if elevate_link_join:
            status = await _process_elevate_link_join(row, apply=not dry_run)
        else:
            status = await _process_with_client(row, apply=not dry_run)
        print(f"Done: readd_status={status}")
    finally:
        await bot.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--row-id", type=int, help="Specific migrated_group_recovery.id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--elevate-link-join",
        action="store_true",
        help="Round Table only: RT direct-add + invite link, then Elevate link-join",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            row_id=args.row_id,
            dry_run=bool(args.dry_run),
            elevate_link_join=bool(args.elevate_link_join),
        )
    )


if __name__ == "__main__":
    main()
