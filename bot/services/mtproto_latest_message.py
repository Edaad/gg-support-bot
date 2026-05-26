"""Telethon diagnostics: read latest group messages via the club MTProto session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from club_gc_settings import ClubGcConfig
from bot.services.mtproto_group_create import (
    get_mtproto_lock,
    is_client_authorized,
    make_client,
)


@dataclass(frozen=True)
class TelethonMessageSnapshot:
    message_id: int
    date_utc: datetime
    text: str
    outgoing: bool
    sender_id: int | None


@dataclass(frozen=True)
class TelethonLatestCheck:
    club_key: str
    club_display_name: str
    authorized: bool
    listener_account_id: int | None
    messages: tuple[TelethonMessageSnapshot, ...]
    error: str | None = None


def _snapshot_from_message(msg) -> TelethonMessageSnapshot:
    text = (getattr(msg, "message", None) or getattr(msg, "text", None) or "")[:500]
    return TelethonMessageSnapshot(
        message_id=int(msg.id),
        date_utc=msg.date,
        text=text,
        outgoing=bool(getattr(msg, "out", False)),
        sender_id=getattr(msg, "sender_id", None),
    )


async def fetch_telethon_latest_messages(
    cfg: ClubGcConfig,
    chat_id: int,
    *,
    limit: int = 3,
) -> TelethonLatestCheck:
    """Fetch recent messages visible to this club's Telethon session."""
    if not await is_client_authorized(cfg):
        return TelethonLatestCheck(
            club_key=cfg.club_key,
            club_display_name=cfg.club_display_name,
            authorized=False,
            listener_account_id=None,
            messages=(),
            error="MTProto session not authorized (Dashboard Telegram login required).",
        )

    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return TelethonLatestCheck(
                    club_key=cfg.club_key,
                    club_display_name=cfg.club_display_name,
                    authorized=False,
                    listener_account_id=None,
                    messages=(),
                    error="MTProto session not authorized after connect.",
                )

            me = await client.get_me()
            listener_account_id = int(me.id) if me and getattr(me, "id", None) else None
            rows: list[TelethonMessageSnapshot] = []
            async for msg in client.iter_messages(int(chat_id), limit=max(1, limit)):
                rows.append(_snapshot_from_message(msg))
            return TelethonLatestCheck(
                club_key=cfg.club_key,
                club_display_name=cfg.club_display_name,
                authorized=True,
                listener_account_id=listener_account_id,
                messages=tuple(rows),
            )
        except Exception as e:
            return TelethonLatestCheck(
                club_key=cfg.club_key,
                club_display_name=cfg.club_display_name,
                authorized=True,
                listener_account_id=None,
                messages=(),
                error=f"Telethon read failed: {type(e).__name__}",
            )
        finally:
            await client.disconnect()


def format_telethon_latest_check(
    result: TelethonLatestCheck,
    *,
    bot_command_message_id: int | None,
    listener_status: dict[str, Any],
) -> str:
    """Human-readable report for /telemsg."""
    lines = [
        f"Telethon check ({result.club_display_name})",
        (
            "Listener: "
            f"{'enabled' if listener_status.get('enabled') else 'disabled'}, "
            f"{listener_status.get('connected_clients', 0)}/"
            f"{listener_status.get('total_clients', 0)} sessions connected, "
            f"running={listener_status.get('listener_running')}, "
            f"restarts={listener_status.get('restart_count', 0)}"
        ),
    ]
    if listener_status.get("last_disconnect_reason"):
        lines.append(
            f"Last listener exit: {listener_status.get('last_disconnect_reason')}"
        )

    if not result.authorized:
        lines.append(f"Session: not authorized")
        if result.error:
            lines.append(result.error)
        return "\n".join(lines)

    lines.append("Session: authorized")
    if result.listener_account_id is not None:
        lines.append(f"MTProto account id: {result.listener_account_id}")

    if result.error:
        lines.append(result.error)
        return "\n".join(lines)

    if not result.messages:
        lines.append("Latest (Telethon): no messages returned for this chat.")
        return "\n".join(lines)

    latest = result.messages[0]
    et = latest.date_utc.astimezone(ZoneInfo("America/New_York"))
    direction = "outgoing" if latest.outgoing else "incoming"
    preview = latest.text.replace("\n", " ").strip() or "(no text)"
    if len(preview) > 120:
        preview = preview[:117] + "..."

    lines.extend(
        [
            "",
            "Latest (Telethon):",
            f"  id: {latest.message_id}",
            f"  date: {et.isoformat(timespec='seconds')}",
            f"  {direction}",
            f"  text: {preview}",
        ]
    )

    if bot_command_message_id is not None:
        lines.append("")
        lines.append(f"Bot /telemsg id: {bot_command_message_id}")
        if latest.message_id == bot_command_message_id:
            lines.append("Match: YES — Telethon sees this command.")
        elif latest.message_id > bot_command_message_id:
            lines.append(
                "Match: Telethon latest is newer than /telemsg "
                "(send /telemsg again after your test message)."
            )
        else:
            lines.append(
                "Match: NO — Telethon latest is older than /telemsg "
                "(session may be stale or wrong chat)."
            )

    if len(result.messages) > 1:
        lines.append("")
        lines.append("Previous:")
        for msg in result.messages[1:]:
            et_prev = msg.date_utc.astimezone(ZoneInfo("America/New_York"))
            dir_prev = "out" if msg.outgoing else "in"
            prev = (msg.text.replace("\n", " ").strip() or "(no text)")[:80]
            lines.append(
                f"  #{msg.message_id} {dir_prev} {et_prev.strftime('%H:%M:%S')} {prev}"
            )

    text = "\n".join(lines)
    return text[:4096]
