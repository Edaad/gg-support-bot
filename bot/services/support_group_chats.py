"""Persistence for support_group_chats (/gc)."""

from __future__ import annotations

import logging
from typing import Any

from db.connection import get_db
from db.models import SupportGroupChat

logger = logging.getLogger(__name__)


def persist_support_group_chat_row(
    *,
    club_key: str,
    club_display_name: str,
    telegram_chat_id: int,
    telegram_chat_title: str,
    invite_link: str | None,
    created_by_telegram_user_id: int,
    mtproto_session_name: str | None,
    added_users: list[dict[str, Any]],
    failed_users: list[dict[str, Any]],
    group_photo_path: str | None,
    initial_message_sent: bool,
    error_message: str | None = None,
) -> tuple[int | None, str | None]:
    """Insert audit row; returns ``(record_id, error_text)``. ``error_text`` is None on success."""

    try:
        with get_db() as session:
            row = SupportGroupChat(
                club_key=club_key,
                club_display_name=club_display_name,
                telegram_chat_id=telegram_chat_id,
                telegram_chat_title=telegram_chat_title[:5000],
                invite_link=invite_link,
                created_by_telegram_user_id=created_by_telegram_user_id,
                mtproto_session_name=mtproto_session_name,
                added_users=added_users or None,
                failed_users=failed_users or None,
                group_photo_path=group_photo_path,
                initial_message_sent=initial_message_sent,
                error_message=error_message,
            )
            session.add(row)
            session.flush()
            pk = row.id

        return pk, None
    except Exception as e:

        hint = type(e).__name__
        logger.exception("support_group_chats insert failed (%s)", hint)
        return None, hint
