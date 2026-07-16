"""Record daily non-bot message activity for club-linked support groups."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from telegram import Update
from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker
from bot.services.club import EST, get_club_for_chat
from db.connection import get_db

logger = logging.getLogger(__name__)


def message_timestamp_utc(message_date: datetime) -> datetime:
    if message_date.tzinfo is None:
        return message_date.replace(tzinfo=timezone.utc)
    return message_date.astimezone(timezone.utc)


def activity_date_for_message(message_date: datetime) -> date:
    return message_timestamp_utc(message_date).astimezone(EST).date()


def upsert_group_chat_daily_activity(
    *,
    chat_id: int,
    club_id: int,
    message_at: datetime,
) -> None:
    message_at_utc = message_timestamp_utc(message_at)
    activity_date = message_at_utc.astimezone(EST).date()

    with get_db() as session:
        session.execute(
            text(
                """
                INSERT INTO group_chat_daily_activity (
                    activity_date,
                    chat_id,
                    club_id,
                    non_bot_message_count,
                    first_message_at,
                    last_message_at
                )
                VALUES (
                    :activity_date,
                    :chat_id,
                    :club_id,
                    1,
                    :message_at,
                    :message_at
                )
                ON CONFLICT (activity_date, chat_id) DO UPDATE SET
                    non_bot_message_count = group_chat_daily_activity.non_bot_message_count + 1,
                    first_message_at = LEAST(
                        group_chat_daily_activity.first_message_at,
                        EXCLUDED.first_message_at
                    ),
                    last_message_at = GREATEST(
                        group_chat_daily_activity.last_message_at,
                        EXCLUDED.last_message_at
                    ),
                    updated_at = NOW()
                """
            ),
            {
                "activity_date": activity_date,
                "chat_id": int(chat_id),
                "club_id": int(club_id),
                "message_at": message_at_utc,
            },
        )


async def record_group_chat_daily_activity(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Group handler: upsert daily activity when a linked group gets a non-bot message."""
    del context

    if is_test_bot_worker():
        return

    message = update.message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or message.date is None:
        return
    if user.is_bot:
        return

    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    try:
        upsert_group_chat_daily_activity(
            chat_id=chat.id,
            club_id=club_id,
            message_at=message.date,
        )
    except Exception:
        logger.warning(
            "group_chat_daily_activity: failed to record chat_id=%s club_id=%s",
            chat.id,
            club_id,
            exc_info=True,
        )
