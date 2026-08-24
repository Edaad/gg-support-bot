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


async def _notify_head_admin_delivery_failure(
    *,
    text: str,
    intended_chat_ids: list[int],
    failed_chat_ids: list[int],
) -> None:
    slack_text = payment_notification_html_to_slack(text)
    if not slack_text:
        return
    failed = ", ".join(str(cid) for cid in failed_chat_ids)
    intended = ", ".join(str(cid) for cid in intended_chat_ids)
    message = "\n".join(
        [
            "Payment notification Telegram delivery failed for one or more club chats.",
            f"Intended chat_ids: {intended}",
            f"Failed chat_ids: {failed}",
            "",
            slack_text,
        ]
    )
    from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

    await notify_slack_head_admin_escalation(
        message,
        source="payment_notification_delivery_failed",
    )


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
    (stored on the payment row for bind/edit flows). Failed club-chat sends are
    skipped; head-admin Slack is notified for those failures.
    """
    from bot.services.venmo_payments import send_telegram_notification

    if not bind_chat_ids:
        raise RuntimeError("deliver_payment_notification: no bind_chat_ids")

    primary: tuple[int, int] | None = None
    failed_chat_ids: list[int] = []
    for chat_id in bind_chat_ids:
        try:
            resolved_chat_id, message_id = await send_telegram_notification(
                text,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                chat_id=int(chat_id),
            )
        except Exception:
            failed_chat_ids.append(int(chat_id))
            logger.warning(
                "payment notification: bind chat send failed chat_id=%s",
                chat_id,
                exc_info=True,
            )
            continue
        if primary is None:
            primary = (resolved_chat_id, message_id)

    if failed_chat_ids:
        try:
            await _notify_head_admin_delivery_failure(
                text=text,
                intended_chat_ids=[int(cid) for cid in bind_chat_ids],
                failed_chat_ids=failed_chat_ids,
            )
        except Exception:
            logger.warning(
                "payment notification: head-admin slack notify failed",
                exc_info=True,
            )

    if primary is None:
        raise RuntimeError(
            "deliver_payment_notification: all bind chat sends failed "
            f"chat_ids={bind_chat_ids}"
        )

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

    return primary
