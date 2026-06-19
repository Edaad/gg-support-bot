"""Find the sole eligible human player in a support megagroup (shared by contact save + backfill)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from club_gc_settings import (
    ClubGcConfig,
    gc_mtproto_operator_telegram_user_ids,
    get_gc_users_to_add,
)
from config import ADMIN_USER_IDS
from bot.services.mtproto_group_create import _with_single_flood_retry

logger = logging.getLogger(__name__)


async def _resolve_invitee_user_ids(client, cfg: ClubGcConfig) -> set[int]:
    out: set[int] = set()
    markers: list[str] = list(get_gc_users_to_add(cfg))
    if cfg.bot_account and str(cfg.bot_account).strip():
        markers.append(str(cfg.bot_account).strip())
    seen: set[str] = set()
    for marker in markers:
        m = marker.strip()
        key = m.lower().lstrip("@")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            ent = await _with_single_flood_retry(
                f"invite_entity:{key}",
                lambda: client.get_entity(m),
            )
            uid = getattr(ent, "id", None)
            if uid is not None:
                out.add(int(uid))
        except Exception as e:
            logger.warning(
                "group_player: unresolved users_to_add marker %s: %s",
                m[:40],
                type(e).__name__,
            )
    return out


@dataclass(frozen=True)
class EligiblePlayer:
    user_id: int
    display_name: str
    username: str | None


@dataclass(frozen=True)
class SolePlayerResult:
    user: Any | None
    candidate_count: int
    candidate_ids: tuple[int, ...]
    eligible: tuple[EligiblePlayer, ...] = ()


def format_telegram_user_display(user: Any) -> tuple[str, str | None]:
    """Return ``(display_name, @username or None)`` for a Telethon user."""
    un = getattr(user, "username", None)
    username = f"@{un.strip().lstrip('@')}" if isinstance(un, str) and un.strip() else None
    fn = (getattr(user, "first_name", None) or "").strip()
    ln = (getattr(user, "last_name", None) or "").strip()
    display = f"{fn} {ln}".strip() or (username or "").lstrip("@") or f"user {getattr(user, 'id', '?')}"
    return display, username


def is_eligible_player_user(
    user: Any,
    *,
    self_id: int | None,
    invite_ids: frozenset[int] | set[int],
    invite_usernames: frozenset[str],
    skip_operators: frozenset[int] | set[int],
    skip_dashboard_admins: frozenset[int] | set[int],
) -> bool:
    """True when ``user`` is a non-bot player candidate (not staff/support)."""
    if not user or getattr(user, "bot", False):
        return False
    uid = getattr(user, "id", None)
    if uid is None:
        return False
    uid_int = int(uid)
    if self_id is not None and uid_int == self_id:
        return False
    if uid_int in invite_ids:
        return False
    if invite_usernames:
        un = getattr(user, "username", None)
        key = un.strip().lower().lstrip("@") if isinstance(un, str) and un.strip() else ""
        if key and key in invite_usernames:
            return False
    if uid_int in skip_operators:
        return False
    if uid_int in skip_dashboard_admins:
        return False
    return True


async def _eligible_player_filter_context(
    client: Any,
    cfg: ClubGcConfig,
    *,
    self_id: int | None,
) -> tuple[set[int], frozenset[str], frozenset[int], frozenset[int]]:
    invite_ids = await _resolve_invitee_user_ids(client, cfg)
    invite_usernames = frozenset(
        m.strip().lower().lstrip("@")
        for m in (list(get_gc_users_to_add(cfg)) + ([cfg.bot_account] if cfg.bot_account else []))
        if isinstance(m, str) and m.strip()
    )
    skip_operators = frozenset(gc_mtproto_operator_telegram_user_ids())
    skip_dashboard_admins = frozenset(int(x) for x in ADMIN_USER_IDS)
    return invite_ids, invite_usernames, skip_operators, skip_dashboard_admins


async def find_latest_eligible_message_sender(
    client: Any,
    channel_ent: Any,
    cfg: ClubGcConfig,
    *,
    self_id: int | None,
    limit: int = 50,
) -> Any | None:
    """Return the sender of the newest message from an eligible non-support human."""
    invite_ids, invite_usernames, skip_operators, skip_dashboard_admins = (
        await _eligible_player_filter_context(client, cfg, self_id=self_id)
    )

    async def scan():
        async for msg in client.iter_messages(channel_ent, limit=max(1, limit)):
            if getattr(msg, "out", False):
                continue
            if not getattr(msg, "sender_id", None):
                continue
            try:
                sender = await msg.get_sender()
            except Exception as e:
                logger.warning(
                    "group_player: get_sender failed msg_id=%s: %s",
                    getattr(msg, "id", "?"),
                    type(e).__name__,
                )
                continue
            if is_eligible_player_user(
                sender,
                self_id=self_id,
                invite_ids=invite_ids,
                invite_usernames=invite_usernames,
                skip_operators=skip_operators,
                skip_dashboard_admins=skip_dashboard_admins,
            ):
                return sender
        return None

    return await _with_single_flood_retry("iter_messages_eligible_sender", scan)


async def collect_eligible_player_participants(
    client: Any,
    channel_ent: Any,
    cfg: ClubGcConfig,
    *,
    self_id: int | None,
) -> list[Any]:
    """All non-bot player candidates after staff/operator exclusions (see ``find_sole_player_participant``)."""
    invite_ids, invite_usernames, skip_operators, skip_dashboard_admins = (
        await _eligible_player_filter_context(client, cfg, self_id=self_id)
    )

    candidates: list[Any] = []

    async def collect():
        async for u in client.iter_participants(channel_ent):
            if is_eligible_player_user(
                u,
                self_id=self_id,
                invite_ids=invite_ids,
                invite_usernames=invite_usernames,
                skip_operators=skip_operators,
                skip_dashboard_admins=skip_dashboard_admins,
            ):
                candidates.append(u)

    await _with_single_flood_retry("iter_participants_sole_player", collect)
    return candidates


def _eligible_from_users(users: list[Any]) -> tuple[EligiblePlayer, ...]:
    out: list[EligiblePlayer] = []
    for u in users:
        uid = getattr(u, "id", None)
        if uid is None:
            continue
        display, username = format_telegram_user_display(u)
        out.append(
            EligiblePlayer(
                user_id=int(uid),
                display_name=display,
                username=username,
            )
        )
    return tuple(out)


async def find_sole_player_participant(
    client: Any,
    channel_ent: Any,
    cfg: ClubGcConfig,
    *,
    self_id: int | None,
) -> SolePlayerResult:
    """Return the single eligible player candidate, or count != 1.

    Excludes bots, the scanning MTProto account (``self``), ``GC_USERS_*`` / bot
    invitees, club MTProto operator IDs, and ``ADMIN_USER_IDS``. Does **not** exclude
    everyone with Telegram admin rights — players are often promoted to admin in
    support groups; blanket admin exclusion produced false ``0`` counts.
    """
    candidates = await collect_eligible_player_participants(
        client, channel_ent, cfg, self_id=self_id
    )
    eligible = _eligible_from_users(candidates)
    ids = tuple(p.user_id for p in eligible)
    if len(candidates) == 1:
        return SolePlayerResult(
            user=candidates[0],
            candidate_count=1,
            candidate_ids=ids,
            eligible=eligible,
        )
    return SolePlayerResult(
        user=None,
        candidate_count=len(candidates),
        candidate_ids=ids,
        eligible=eligible,
    )
