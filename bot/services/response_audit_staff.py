"""Staff / bot identity for the response audit (by Telegram sender id, never name).

Staff ids come from ``clubs.telegram_user_id``, ``club_linked_accounts``, the
``/gc`` invite handles (``GC_USERS_TO_INVITE`` + ``GC_USERS_*``) resolved once via
MTProto into ``staff_handle_ids``, ``ADMIN_USER_IDS``, each club's
``command_admin_user_id`` and ``RESPONSE_AUDIT_EXTRA_STAFF_IDS``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from club_gc_settings import CLUB_GC_CONFIG, ClubGcConfig, get_gc_users_to_add
from config import ADMIN_USER_IDS
from db.connection import get_db
from db.models import Club, ClubLinkedAccount, StaffHandleId

logger = logging.getLogger(__name__)

EXTRA_STAFF_IDS_ENV = "RESPONSE_AUDIT_EXTRA_STAFF_IDS"

# Same list the ticket classifier treats as bots (group_chat_analysis).
TRANSLATION_BOT_USERNAMES = ("ytranslatebot",)


def normalize_handle(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def extra_staff_ids() -> set[int]:
    out: set[int] = set()
    for part in (os.getenv(EXTRA_STAFF_IDS_ENV) or "").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            out.add(int(text))
        except ValueError:
            logger.warning("response_audit: bad %s entry %r", EXTRA_STAFF_IDS_ENV, text)
    return out


def _invite_markers(cfg: ClubGcConfig) -> tuple[set[int], set[str]]:
    """(numeric ids, normalized handles) from one club's /gc invite list."""

    ids: set[int] = set()
    handles: set[str] = set()
    for marker in get_gc_users_to_add(cfg):
        text = str(marker).strip()
        if not text:
            continue
        if text.lstrip("-").isdigit():
            ids.add(int(text))
            continue
        handle = normalize_handle(text)
        if handle:
            handles.add(handle)
    return ids, handles


def invite_handles(cfg: ClubGcConfig) -> set[str]:
    _ids, handles = _invite_markers(cfg)
    bot = normalize_handle(cfg.bot_account)
    handles.discard(bot)
    return handles


def bot_usernames() -> frozenset[str]:
    names = set(TRANSLATION_BOT_USERNAMES)
    for cfg in CLUB_GC_CONFIG.values():
        bot = normalize_handle(cfg.bot_account)
        if bot:
            names.add(bot)
    return frozenset(names)


def load_staff_ids() -> frozenset[int]:
    """Every Telegram user id the audit treats as staff (all clubs)."""

    ids: set[int] = {int(x) for x in ADMIN_USER_IDS}
    ids |= extra_staff_ids()
    for cfg in CLUB_GC_CONFIG.values():
        if cfg.command_admin_user_id:
            ids.add(int(cfg.command_admin_user_id))
        numeric, _handles = _invite_markers(cfg)
        ids |= numeric
    with get_db() as session:
        ids |= {int(r[0]) for r in session.query(Club.telegram_user_id).all() if r[0]}
        ids |= {
            int(r[0])
            for r in session.query(ClubLinkedAccount.telegram_user_id).all()
            if r[0]
        }
        ids |= {
            int(r[0])
            for r in session.query(StaffHandleId.telegram_user_id)
            .filter(StaffHandleId.telegram_user_id.isnot(None))
            .all()
        }
    return frozenset(ids)


def unresolved_handles(handles: set[str]) -> set[str]:
    """Handles with no cached user id yet (never tried, or last try failed)."""

    if not handles:
        return set()
    with get_db() as session:
        resolved = {
            str(r[0])
            for r in session.query(StaffHandleId.handle)
            .filter(
                StaffHandleId.handle.in_(sorted(handles)),
                StaffHandleId.telegram_user_id.isnot(None),
            )
            .all()
        }
    return set(handles) - resolved


def save_handle_resolution(
    handle: str, *, telegram_user_id: int | None, error: str | None
) -> None:
    from datetime import datetime, timezone

    with get_db() as session:
        row = session.get(StaffHandleId, handle)
        if row is None:
            row = StaffHandleId(handle=handle)
            session.add(row)
        row.telegram_user_id = telegram_user_id
        row.error = (error or None) and str(error)[:500]
        row.resolved_at = datetime.now(timezone.utc)


async def resolve_staff_handles_with_client(cfg: ClubGcConfig, client: Any) -> int:
    """Resolve this club's uncached invite handles on an already-connected client.

    Called by the nightly transcript fetch while MTProto is paused, so no second
    connection is opened on the club's auth key. Returns the number resolved.
    """

    import asyncio

    pending = await asyncio.to_thread(unresolved_handles, invite_handles(cfg))
    resolved = 0
    for handle in sorted(pending):
        try:
            entity = await client.get_entity(f"@{handle}")
            user_id = int(getattr(entity, "id"))
        except Exception as exc:
            logger.info(
                "response_audit: could not resolve staff handle @%s club=%s: %s",
                handle,
                cfg.club_key,
                type(exc).__name__,
            )
            await asyncio.to_thread(
                save_handle_resolution,
                handle,
                telegram_user_id=None,
                error=type(exc).__name__,
            )
            continue
        await asyncio.to_thread(
            save_handle_resolution, handle, telegram_user_id=user_id, error=None
        )
        resolved += 1
    return resolved
