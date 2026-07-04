"""Kick off bonus recording wizard from /add when a bonus amount is present."""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Bot

from bot.services.bonus_drafts import create_draft, draft_to_context
from bot.services.bonus_notify import notify_staff_bonus_draft
from db.connection import get_db

logger = logging.getLogger(__name__)


async def maybe_start_bonus_recording_from_add(
    bot: Bot,
    *,
    staff_user_id: int,
    club_id: int,
    chat_id: int,
    group_title: str | None,
    bonus_amount: Decimal | None,
    player_name: str | None,
) -> None:
    """Create a bonus draft and DM staff. Never raises; no-op when bonus_amount is None."""
    if bonus_amount is None:
        return

    try:
        with get_db() as session:
            draft = create_draft(
                session,
                staff_telegram_user_id=staff_user_id,
                club_id=club_id,
                group_title=group_title,
                telegram_chat_id=chat_id,
                player_username=player_name,
                amount=bonus_amount,
            )
            draft_ctx = draft_to_context(draft)
    except Exception:
        logger.exception(
            "bonus_from_add: create_draft failed staff=%s club_id=%s chat_id=%s",
            staff_user_id,
            club_id,
            chat_id,
        )
        return

    ok = await notify_staff_bonus_draft(
        bot,
        staff_user_id=staff_user_id,
        draft_id=draft_ctx.id,
        group_title=draft_ctx.group_title,
        amount=draft_ctx.amount,
        player_username=draft_ctx.player_username,
    )
    if not ok:
        logger.warning(
            "bonus_from_add: notify failed draft_id=%s staff=%s",
            draft_ctx.id,
            staff_user_id,
        )
    else:
        logger.info(
            "bonus_from_add: draft_id=%s staff=%s club_id=%s amount=%s",
            draft_ctx.id,
            staff_user_id,
            club_id,
            bonus_amount,
        )
