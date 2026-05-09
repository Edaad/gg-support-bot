"""Persistence for support_group_chats (/gc)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import IntegrityError

from db.connection import get_db, get_session
from db.models import SupportGroupChat

logger = logging.getLogger(__name__)


def fetch_support_group_chat_by_club_player(
    club_key: str, player_telegram_user_id: int
) -> SupportGroupChat | None:
    with get_db() as session:
        row = (
            session.query(SupportGroupChat)
            .filter(
                SupportGroupChat.club_key == club_key,
                SupportGroupChat.player_telegram_user_id == player_telegram_user_id,
            )
            .one_or_none()
        )
        if row is not None:
            session.expunge(row)
        return row


def persist_support_group_chat_row(
    *,
    club_key: str,
    club_display_name: str,
    telegram_chat_id: int,
    telegram_chat_title: str,
    invite_link: str | None,
    created_by_telegram_user_id: int | None,
    mtproto_session_name: str | None,
    added_users: list[dict[str, Any]],
    failed_users: list[dict[str, Any]],
    group_photo_path: str | None,
    initial_group_message_sent: bool,
    last_error_message: str | None = None,
    player_telegram_user_id: int | None = None,
    player_username: str | None = None,
    player_display_name: str | None = None,
    player_dm_status: str | None = None,
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
                initial_group_message_sent=initial_group_message_sent,
                last_error_message=last_error_message,
                player_telegram_user_id=player_telegram_user_id,
                player_username=player_username,
                player_display_name=player_display_name,
                player_dm_status=player_dm_status,
            )
            session.add(row)
            session.flush()
            pk = row.id

        return pk, None
    except IntegrityError as e:
        raw = str(getattr(e, "orig", e)).lower()
        if "uq_support_group_chats_club_player" in raw:
            logger.warning("support_group_chats club+player unique violation")
            return None, "duplicate_club_player"
        logger.exception("support_group_chats IntegrityError")
        return None, type(e).__name__
    except Exception as e:

        hint = type(e).__name__
        logger.exception("support_group_chats insert failed (%s)", hint)
        return None, hint


def update_support_group_chat_row(
    row_id: int,
    *,
    invite_link: str | None = None,
    added_users: list[dict[str, Any]] | None = None,
    failed_users: list[dict[str, Any]] | None = None,
    player_username: str | None = None,
    player_display_name: str | None = None,
    player_dm_status: str | None = None,
    last_error_message: str | None = None,
    initial_group_message_sent: bool | None = None,
    telegram_chat_title: str | None = None,
) -> tuple[bool, str | None]:
    """Update an existing row by primary key. Returns (ok, error)."""
    try:
        with get_db() as session:
            row = session.get(SupportGroupChat, row_id)
            if row is None:
                return False, "not_found"
            if invite_link is not None:
                row.invite_link = invite_link
            if added_users is not None:
                row.added_users = added_users
            if failed_users is not None:
                row.failed_users = failed_users
            if player_username is not None:
                row.player_username = player_username
            if player_display_name is not None:
                row.player_display_name = player_display_name
            if player_dm_status is not None:
                row.player_dm_status = player_dm_status
            if last_error_message is not None:
                row.last_error_message = last_error_message
            if initial_group_message_sent is not None:
                row.initial_group_message_sent = initial_group_message_sent
            if telegram_chat_title is not None:
                row.telegram_chat_title = telegram_chat_title[:5000]
        return True, None
    except Exception as e:
        logger.exception("support_group_chats update failed (%s)", type(e).__name__)
        return False, type(e).__name__


def try_pg_advisory_lock_club_player(club_key: str, player_telegram_user_id: int) -> tuple[Any, bool]:
    """Acquire session-level advisory lock. Returns (session, acquired). Caller must unlock and close."""
    import zlib

    from sqlalchemy import text

    k1 = zlib.crc32(club_key.encode("utf-8")) & 0x7FFFFFFF
    k2 = abs(int(player_telegram_user_id)) & 0x7FFFFFFF
    session = get_session()
    try:
        got = session.execute(
            text("SELECT pg_try_advisory_lock(:k1, :k2)"), {"k1": k1, "k2": k2}
        ).scalar()
        if not got:
            session.close()
            return None, False
        return session, True
    except Exception:
        session.close()
        raise


def pg_advisory_unlock_session(session, club_key: str, player_telegram_user_id: int) -> None:
    import zlib

    from sqlalchemy import text

    if session is None:
        return
    k1 = zlib.crc32(club_key.encode("utf-8")) & 0x7FFFFFFF
    k2 = abs(int(player_telegram_user_id)) & 0x7FFFFFFF
    try:
        session.execute(text("SELECT pg_advisory_unlock(:k1, :k2)"), {"k1": k1, "k2": k2})
        session.commit()
    finally:
        session.close()
