"""MTProto eligible-player membership checks for migration recovery groups.

Same rules as ``scripts/check_recovery_player_membership.py`` and auto contact
save: ``find_sole_player_participant`` — non-bot humans excluding staff invitees,
operators, and dashboard admins. ``player_in_group`` when count >= 1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from club_gc_settings import CLUB_GC_CONFIG, ClubGcConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryMembershipResult:
    eligible_player_count: int
    eligible_player_ids: tuple[int, ...]
    player_in_group: bool
    sole_user: Any | None = None
    error: str | None = None


async def mtproto_check_group_membership(
    client: Any,
    cfg: ClubGcConfig,
    *,
    telegram_chat_id: int,
    self_id: int | None,
) -> RecoveryMembershipResult:
    """Return eligible-player count for one support group (MTProto)."""
    from bot.services.mtproto_group_player import find_sole_player_participant

    try:
        entity = await client.get_entity(int(telegram_chat_id))
    except Exception as e:
        return RecoveryMembershipResult(
            eligible_player_count=0,
            eligible_player_ids=(),
            player_in_group=False,
            error=f"open_chat_error:{type(e).__name__}",
        )

    try:
        sole = await find_sole_player_participant(client, entity, cfg, self_id=self_id)
    except Exception as e:
        return RecoveryMembershipResult(
            eligible_player_count=0,
            eligible_player_ids=(),
            player_in_group=False,
            error=f"list_members_error:{type(e).__name__}",
        )

    count = int(sole.candidate_count)
    ids = tuple(int(x) for x in sole.candidate_ids)
    return RecoveryMembershipResult(
        eligible_player_count=count,
        eligible_player_ids=ids,
        player_in_group=count >= 1,
        sole_user=sole.user if count == 1 else None,
        error=None,
    )


async def mtproto_scan_recovery_rows(
    club_key: str,
    rows: list[Any],
    *,
    delay_sec: float = 0.05,
) -> dict[int, RecoveryMembershipResult]:
    """Scan many recovery rows for one club with a single MTProto session.

    ``rows`` must expose ``id`` and ``telegram_chat_id`` attributes.
    Returns ``{row_id: RecoveryMembershipResult}``.
    """
    import asyncio

    from bot.services.mtproto_group_create import (
        get_mtproto_lock,
        is_client_authorized,
        make_client,
    )

    cfg = CLUB_GC_CONFIG.get(club_key)
    if cfg is None:
        err = RecoveryMembershipResult(
            0, (), False, error=f"unknown_club:{club_key}"
        )
        return {int(r.id): err for r in rows}

    if not await is_client_authorized(cfg):
        err = RecoveryMembershipResult(0, (), False, error="mtproto_unauthorized")
        return {int(r.id): err for r in rows}

    out: dict[int, RecoveryMembershipResult] = {}
    async with get_mtproto_lock(cfg.club_key):
        client = make_client(cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                err = RecoveryMembershipResult(0, (), False, error="mtproto_unauthorized")
                return {int(r.id): err for r in rows}

            me = await client.get_me()
            self_id = int(me.id) if me and getattr(me, "id", None) is not None else None

            for i, row in enumerate(rows, 1):
                result = await mtproto_check_group_membership(
                    client,
                    cfg,
                    telegram_chat_id=int(row.telegram_chat_id),
                    self_id=self_id,
                )
                out[int(row.id)] = result
                if delay_sec > 0 and i < len(rows):
                    await asyncio.sleep(delay_sec)
        finally:
            await client.disconnect()
    return out
