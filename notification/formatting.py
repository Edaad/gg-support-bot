"""Shared Telegram HTML formatting for payment notifications."""

from __future__ import annotations

import html

from notification.chat_id import telegram_supergroup_chat_url

UNBOUND_GROUP_CHAT_LINE = (
    "Group Chat: Unbound — reply to this message with the group title to bind"
)

# Set True to hyperlink bound group titles in payment notifications.
LINKED_GROUP_CHAT_HYPERLINKS_ENABLED = True


def format_group_chat_line(
    *,
    group_title: str | None,
    telegram_chat_id: int | None,
) -> str:
    """Format the Group Chat line; hyperlinks the title when a linked chat id is known."""
    from bot.services.support_group_chats import fetch_invite_link_for_chat

    title = (group_title or "").strip()
    if not title:
        return UNBOUND_GROUP_CHAT_LINE
    safe_title = html.escape(title, quote=False)
    if (
        LINKED_GROUP_CHAT_HYPERLINKS_ENABLED
        and telegram_chat_id is not None
    ):
        cid = int(telegram_chat_id)
        url = telegram_supergroup_chat_url(cid) or fetch_invite_link_for_chat(
            cid,
            group_title=title,
        )
        if url:
            safe_url = html.escape(url, quote=True)
            return f'Group Chat: <a href="{safe_url}">{safe_title}</a>'
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
