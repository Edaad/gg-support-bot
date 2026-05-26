"""Find the sole eligible human player in a support megagroup (shared by contact save + backfill)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from club_gc_settings import ClubGcConfig, gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.services.mtproto_group_create import _with_single_flood_retry

logger = logging.getLogger(__name__)


async def _admin_user_ids(client, channel_ent) -> set[int]:
    from telethon.tl.types import ChannelParticipantsAdmins

    ids: set[int] = set()

    async def walk():
        async for u in client.iter_participants(
            channel_ent, filter=ChannelParticipantsAdmins()
        ):
            if u and getattr(u, "id", None) is not None:
                ids.add(int(u.id))

    try:
        await _with_single_flood_retry("iter_admin_participants", walk)
    except Exception as e:
        logger.warning(
            "group_player: admin list failed chat=%s: %s",
            getattr(channel_ent, "id", "?"),
            type(e).__name__,
        )
    return ids


async def _resolve_invitee_user_ids(client, cfg: ClubGcConfig) -> set[int]:
    out: set[int] = set()
    markers: list[str] = list(cfg.users_to_add)
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
class SolePlayerResult:
    user: Any | None
    candidate_count: int
    candidate_ids: tuple[int, ...]


async def find_sole_player_participant(
    client: Any,
    channel_ent: Any,
    cfg: ClubGcConfig,
    *,
    self_id: int | None,
) -> SolePlayerResult:
    """Return the single non-bot player candidate, or count != 1."""
    admin_ids = await _admin_user_ids(client, channel_ent)
    invite_ids = await _resolve_invitee_user_ids(client, cfg)
    skip_operators = gc_mtproto_operator_telegram_user_ids()
    skip_dashboard_admins = frozenset(int(x) for x in ADMIN_USER_IDS)

    candidates: list[Any] = []

    async def collect():
        async for u in client.iter_participants(channel_ent):
            if not u or getattr(u, "bot", False):
                continue
            uid = getattr(u, "id", None)
            if uid is None:
                continue
            uid_int = int(uid)
            if self_id is not None and uid_int == self_id:
                continue
            if uid_int in admin_ids:
                continue
            if uid_int in invite_ids:
                continue
            if uid_int in skip_operators:
                continue
            if uid_int in skip_dashboard_admins:
                continue
            candidates.append(u)

    await _with_single_flood_retry("iter_participants_sole_player", collect)

    ids = tuple(int(getattr(u, "id", 0)) for u in candidates)
    if len(candidates) == 1:
        return SolePlayerResult(user=candidates[0], candidate_count=1, candidate_ids=ids)
    return SolePlayerResult(user=None, candidate_count=len(candidates), candidate_ids=ids)
