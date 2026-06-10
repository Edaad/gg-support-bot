"""Shared Telegram HTML formatting for payment notifications."""

from __future__ import annotations

import html

from notification.chat_id import (
    is_joinable_invite_url,
    notification_group_chat_url,
)
from notification.constants import linked_group_chat_hyperlinks_enabled

UNBOUND_GROUP_CHAT_LINE = (
    "Group Chat: Unbound — reply to this message with the group title to bind"
)


def format_group_chat_line(
    *,
    group_title: str | None,
    telegram_chat_id: int | None,
    group_chat_url: str | None = None,
) -> str:
    """Format the Group Chat line; hyperlinks the title when a safe member-only URL exists."""
    title = (group_title or "").strip()
    if not title:
        return UNBOUND_GROUP_CHAT_LINE
    safe_title = html.escape(title, quote=False)
    if linked_group_chat_hyperlinks_enabled() and telegram_chat_id is not None:
        url = (group_chat_url or "").strip() or None
        if url and is_joinable_invite_url(url):
            url = None
        if url is None:
            url = notification_group_chat_url(int(telegram_chat_id))
        if url:
            safe_url = html.escape(url, quote=True)
            return f'Group Chat: <a href="{safe_url}">{safe_title}</a>'
    return f"Group Chat: {safe_title}"


async def resolve_and_format_group_chat_line(
    *,
    group_title: str | None,
    telegram_chat_id: int | None,
    club_id: int | None = None,
) -> str:
    """Resolve group chat URL on-demand, then format the Group Chat line."""
    from bot.services.group_chat_invite_links import resolve_group_chat_notification_url

    title = (group_title or "").strip()
    group_chat_url: str | None = None
    if title and telegram_chat_id is not None:
        group_chat_url = await resolve_group_chat_notification_url(
            telegram_chat_id=int(telegram_chat_id),
            group_title=title,
            club_id=club_id,
        )
    return format_group_chat_line(
        group_title=group_title,
        telegram_chat_id=telegram_chat_id,
        group_chat_url=group_chat_url,
    )


def resolve_notification_linked_chat_id(
    payment: object,
    *,
    telegram_chat_id: int | None = None,
) -> int | None:
    if telegram_chat_id is not None:
        return int(telegram_chat_id)
    raw = getattr(payment, "telegram_chat_id", None)
    return int(raw) if raw is not None else None
