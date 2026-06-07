"""Shared Telegram HTML formatting for payment notifications."""

from __future__ import annotations

import html

from notification.chat_id import telegram_supergroup_chat_url

UNBOUND_GROUP_CHAT_LINE = (
    "Group Chat: Unbound — reply to this message with the group title to bind"
)


def format_group_chat_line(
    *,
    group_title: str | None,
    telegram_chat_id: int | None,
) -> str:
    """Format the Group Chat line; hyperlinks the title when a linked chat id is known."""
    title = (group_title or "").strip()
    if not title:
        return UNBOUND_GROUP_CHAT_LINE
    safe_title = html.escape(title, quote=False)
    if telegram_chat_id is not None:
        url = telegram_supergroup_chat_url(int(telegram_chat_id))
        if url:
            return f'Group Chat: <a href="{url}">{safe_title}</a>'
    return f"Group Chat: {safe_title}"


def resolve_notification_linked_chat_id(
    payment: object,
    *,
    telegram_chat_id: int | None = None,
) -> int | None:
    if telegram_chat_id is not None:
        return int(telegram_chat_id)
    raw = getattr(payment, "telegram_chat_id", None)
    return int(raw) if raw is not None else None
