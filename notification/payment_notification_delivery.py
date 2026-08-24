"""Fan-out payment notifications to bind chats and Slack AM escalations."""

from __future__ import annotations

import html
import logging
import re

logger = logging.getLogger(__name__)

_A_TAG_RE = re.compile(r'<a href="([^"]+)">([^<]*)</a>')
_B_TAG_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL)


def payment_notification_html_to_slack(text: str) -> str:
    """Best-effort Telegram HTML notification body → Slack mrkdwn."""
    body = (text or "").strip()
    if not body:
        return body
    body = _B_TAG_RE.sub(r"*\1*", body)
    body = _A_TAG_RE.sub(r"<\1|\2>", body)
    # Drop leftover HTML tags; keep Slack links (<https://...|label>).
    body = re.sub(r"<(?!https?://|mailto:)[^>]+>", "", body)
    return html.unescape(body).strip()


async def deliver_payment_notification(
    text: str,
    *,
    bind_chat_ids: list[int],
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
    include_slack_escalation: bool = True,
) -> tuple[int, int]:
    """Post to bind chats and optionally mirror to Slack AM escalations.

    Returns ``(chat_id, message_id)`` for the first successful bind-chat post
    (stored on the payment row for bind/edit flows).
    """
    from bot.services.venmo_payments import send_telegram_notification

    if not bind_chat_ids:
        raise RuntimeError("deliver_payment_notification: no bind_chat_ids")

    primary: tuple[int, int] | None = None
    for chat_id in bind_chat_ids:
        resolved_chat_id, message_id = await send_telegram_notification(
            text,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to_message_id,
            chat_id=int(chat_id),
        )
        if primary is None:
            primary = (resolved_chat_id, message_id)

    if include_slack_escalation:
        try:
            from bot.services.slack_ops_notify import notify_slack_escalation

            slack_text = payment_notification_html_to_slack(text)
            if slack_text:
                await notify_slack_escalation(
                    slack_text,
                    source="payment_notification",
                )
        except Exception:
            logger.warning(
                "payment notification: slack escalation send failed",
                exc_info=True,
            )

    assert primary is not None
    return primary
