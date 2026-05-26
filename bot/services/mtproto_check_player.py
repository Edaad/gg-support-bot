"""Telethon: list eligible human players in a support group (for /checkplayer)."""

from __future__ import annotations

from dataclasses import dataclass

from club_gc_settings import ClubGcConfig
from bot.services.club import get_group_title_for_chat
from bot.services.mtproto_group_create import (
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)
from bot.services.mtproto_group_player import (
    collect_eligible_player_participants,
    format_telegram_user_display,
)


@dataclass(frozen=True)
class CheckPlayerResult:
    club_key: str
    club_display_name: str
    chat_id: int
    group_title: str | None
    authorized: bool
    eligible_count: int
    eligible_lines: tuple[str, ...]
    error: str | None = None


async def check_players_in_group(cfg: ClubGcConfig, chat_id: int) -> CheckPlayerResult:
    """List eligible player humans visible to the club MTProto session."""
    cid = int(chat_id)
    stored_title, _ = get_group_title_for_chat(cid)

    if not await is_client_authorized(cfg):
        return CheckPlayerResult(
            club_key=cfg.club_key,
            club_display_name=cfg.club_display_name,
            chat_id=cid,
            group_title=stored_title,
            authorized=False,
            eligible_count=0,
            eligible_lines=(),
            error="MTProto session not authorized (Dashboard Telegram login required).",
        )

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return CheckPlayerResult(
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    chat_id=cid,
                    group_title=stored_title,
                    authorized=False,
                    eligible_count=0,
                    eligible_lines=(),
                    error="MTProto session not authorized after connect.",
                )

            me = await client.get_me()
            self_id = int(me.id) if me and getattr(me, "id", None) is not None else None
            entity = await client.get_entity(cid)
            live_title = getattr(entity, "title", None)
            group_title = (
                (stored_title or "").strip()
                or (live_title.strip() if isinstance(live_title, str) else None)
            )

            users = await collect_eligible_player_participants(
                client, entity, cfg, self_id=self_id
            )
            lines: list[str] = []
            for i, u in enumerate(users, start=1):
                display, username = format_telegram_user_display(u)
                uid = int(u.id)
                handle = f" {username}" if username else ""
                lines.append(f"{i}. {display}{handle} (id={uid})")

            return CheckPlayerResult(
                club_key=cfg.club_key,
                club_display_name=cfg.club_display_name,
                chat_id=cid,
                group_title=group_title,
                authorized=True,
                eligible_count=len(lines),
                eligible_lines=tuple(lines),
            )
        except Exception as e:
            return CheckPlayerResult(
                club_key=cfg.club_key,
                club_display_name=cfg.club_display_name,
                chat_id=cid,
                group_title=stored_title,
                authorized=True,
                eligible_count=0,
                eligible_lines=(),
                error=f"Telethon check failed: {type(e).__name__}",
            )
        finally:
            await client.disconnect()


def format_check_player_result(result: CheckPlayerResult) -> str:
    lines = [
        f"Check player ({result.club_display_name})",
        f"Chat ID: {result.chat_id}",
    ]
    if result.group_title:
        lines.append(f"Group: {result.group_title}")
    if not result.authorized or result.error:
        if result.error:
            lines.append(result.error)
        return "\n".join(lines)[:4096]

    lines.append(f"Eligible players: {result.eligible_count}")
    if result.eligible_count == 0:
        lines.append(
            "(No eligible humans — bots, MTProto self, GC_USERS, operators, and "
            "dashboard admins are excluded.)"
        )
    else:
        lines.extend(result.eligible_lines)
    return "\n".join(lines)[:4096]
