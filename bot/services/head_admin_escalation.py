"""Fan-out selected escalation alerts to the head-admin Slack channel."""

from __future__ import annotations

import logging

from bot.services.escalation_notification import (
    REASON_RPA_CASHOUT_FAILED,
    REASON_RPA_DEPOSIT_FAILED,
)

logger = logging.getLogger(__name__)

HEAD_ADMIN_ESCALATION_REASONS = frozenset(
    {
        REASON_RPA_DEPOSIT_FAILED,
        REASON_RPA_CASHOUT_FAILED,
    }
)


async def maybe_notify_head_admin_escalation(text: str, *, reason: str) -> bool:
    """Post identical text to head-admin channel when reason is allowlisted.

    No-op for other reasons. Never raises.
    """
    if reason not in HEAD_ADMIN_ESCALATION_REASONS:
        return False
    message = (text or "").strip()
    if not message:
        return False
    try:
        from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

        return await notify_slack_head_admin_escalation(message, source=reason)
    except Exception:
        logger.warning(
            "head_admin_escalation: slack failed reason=%s",
            reason,
            exc_info=True,
        )
        return False
