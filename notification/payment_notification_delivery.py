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


def _record_manual_action_event(
    text: str,
    *,
    support_chat_id: int | None,
    support_club_id: int | None,
    support_group_title: str | None,
    slack_ok: bool,
) -> None:
    """Log a *Manual action required* notification as an escalation event.

    Only when the payment is tied to a player support group; the response audit
    starts a ``slack_escalation`` clock from this row.
    """
    if support_chat_id is None:
        return
    from bot.services.payment_auto_deposit import is_manual_action_staff_notification

    if not is_manual_action_staff_notification(text):
        return
    from bot.services.escalation_notification import REASON_PAYMENT_MANUAL_ACTION
    from bot.services.escalation_observability import record_escalation_event

    record_escalation_event(
        reason=REASON_PAYMENT_MANUAL_ACTION,
        telegram_chat_id=int(support_chat_id),
        club_id=support_club_id,
        group_title=support_group_title,
        slack_ok=slack_ok,
        trigger_messages=[{"text": payment_notification_html_to_slack(text)}],
    )


async def deliver_payment_notification(
    text: str,
    *,
    bind_chat_ids: list[int],
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
    include_slack_escalation: bool = True,
    support_chat_id: int | None = None,
    support_club_id: int | None = None,
    support_group_title: str | None = None,
) -> list[tuple[int, int]]:
    """Post to bind chats and optionally mirror to Slack AM escalations.

    Returns every successful ``(chat_id, message_id)`` bind-chat post. The first
    entry is the primary copy stored on the payment row for legacy callers.
    Failed club-chat sends are skipped; head-admin Slack is notified for those
    failures.

    ``support_chat_id`` (the player support group, when known) lets a *Manual
    action required* notification be logged to ``escalation_events``.
    """
    from bot.services.venmo_payments import send_telegram_notification

    if not bind_chat_ids:
        raise RuntimeError("deliver_payment_notification: no bind_chat_ids")

    posts: list[tuple[int, int]] = []
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
        posts.append((int(resolved_chat_id), int(message_id)))

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

    if not posts:
        raise RuntimeError(
            "deliver_payment_notification: all bind chat sends failed "
            f"chat_ids={bind_chat_ids}"
        )

    slack_ok = False
    if include_slack_escalation:
        from bot.services.payment_auto_deposit import (
            is_fully_automatic_staff_notification,
        )

        if is_fully_automatic_staff_notification(text):
            logger.info(
                "payment notification: skipping slack escalation for fully automatic auto-add"
            )
        else:
            try:
                from bot.services.slack_ops_notify import notify_slack_escalation

                slack_text = payment_notification_html_to_slack(text)
                if slack_text:
                    slack_ok = await notify_slack_escalation(
                        slack_text,
                        source="payment_notification",
                    )
            except Exception:
                logger.warning(
                    "payment notification: slack escalation send failed",
                    exc_info=True,
                )

    try:
        _record_manual_action_event(
            text,
            support_chat_id=support_chat_id,
            support_club_id=support_club_id,
            support_group_title=support_group_title,
            slack_ok=bool(slack_ok),
        )
    except Exception:
        logger.warning(
            "payment notification: manual-action escalation event failed",
            exc_info=True,
        )

    return posts
