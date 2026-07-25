"""Warn staff when a support group accumulates >2 distinct payer names on one method."""

from __future__ import annotations

import logging
from typing import Optional

from db.connection import get_db
from db.models import CashAppPayment, PayPalPayment, VenmoPayment, ZellePayment

logger = logging.getLogger(__name__)

_METHOD_MODELS = {
    "venmo": VenmoPayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "zelle": ZellePayment,
}

_METHOD_LABELS = {
    "venmo": "Venmo",
    "cashapp": "CashApp",
    "paypal": "PayPal",
    "zelle": "Zelle",
}

_MULTI_PAYER_SOURCE = "multi_payer_warning"


def normalize_payer_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def method_label(payment_method_slug: str) -> str:
    slug = (payment_method_slug or "").strip().lower()
    return _METHOD_LABELS.get(slug, slug.title() or "Unknown")


def _distinct_payer_names(
    session,
    *,
    payment_method_slug: str,
    telegram_chat_id: int,
    exclude_payment_id: Optional[int] = None,
) -> dict[str, str]:
    """Return normalized_name -> display_name for bound non-test payments."""
    slug = (payment_method_slug or "").strip().lower()
    model = _METHOD_MODELS.get(slug)
    if model is None:
        return {}

    q = session.query(model.id, model.payer_name).filter(
        model.telegram_chat_id == int(telegram_chat_id),
        model.is_test.isnot(True),
    )
    if exclude_payment_id is not None:
        q = q.filter(model.id != int(exclude_payment_id))

    out: dict[str, str] = {}
    for _pid, raw in q.order_by(model.id.asc()).all():
        display = (raw or "").strip()
        norm = normalize_payer_name(display)
        if not norm:
            continue
        if norm not in out:
            out[norm] = display
    return out


def evaluate_multi_payer_warning(
    *,
    payment_method_slug: str,
    payment_id: int,
    telegram_chat_id: int,
    payer_name: str,
) -> Optional[list[str]]:
    """
    If this bind newly introduces a distinct payer name and the group now has
    more than 2 on this method, return the full display-name list; else None.
    """
    slug = (payment_method_slug or "").strip().lower()
    if slug not in _METHOD_MODELS:
        return None

    current_norm = normalize_payer_name(payer_name)
    if not current_norm:
        return None

    with get_db() as session:
        prior = _distinct_payer_names(
            session,
            payment_method_slug=slug,
            telegram_chat_id=int(telegram_chat_id),
            exclude_payment_id=int(payment_id),
        )

    if current_norm in prior:
        return None

    names = dict(prior)
    names[current_norm] = (payer_name or "").strip() or current_norm
    if len(names) <= 2:
        return None

    return sorted(names.values(), key=lambda s: normalize_payer_name(s))


def format_multi_payer_warning_text(
    *,
    payment_method_slug: str,
    group_title: str,
    payer_names: list[str],
    html: bool = False,
) -> str:
    label = method_label(payment_method_slug)
    count = len(payer_names)
    title = (group_title or "").strip() or "(unknown group)"

    if html:
        from bot.services.venmo_payments import escape_notification_html

        safe_label = escape_notification_html(label)
        safe_title = escape_notification_html(title)
        name_lines = "\n".join(
            f"• {escape_notification_html(n)}" for n in payer_names
        )
        return (
            "⚠️ <b>WARNING — DO NOT ADD CHIPS</b>\n\n"
            f"This group has payments from more than 2 different payers on {safe_label}.\n"
            f"They appear to be using {count} different accounts to pay.\n\n"
            f"Group: {safe_title}\n"
            f"Payers ({count}):\n{name_lines}"
        )

    name_lines = "\n".join(f"• {n}" for n in payer_names)
    return (
        "⚠️ WARNING — DO NOT ADD CHIPS\n\n"
        f"This group has payments from more than 2 different payers on {label}.\n"
        f"They appear to be using {count} different accounts to pay.\n\n"
        f"Group: {title}\n"
        f"Payers ({count}):\n{name_lines}"
    )


async def maybe_warn_multi_payer(
    *,
    payment_method_slug: str,
    payment_id: int,
    telegram_chat_id: Optional[int],
    payer_name: str,
    group_title: Optional[str],
    notification_message_id: Optional[int] = None,
    is_test: bool = False,
) -> bool:
    """
    Send Telegram + Slack multi-payer warnings when warranted.
    Returns True if a warning was sent.
    """
    if is_test or telegram_chat_id is None:
        return False

    try:
        payer_names = evaluate_multi_payer_warning(
            payment_method_slug=payment_method_slug,
            payment_id=int(payment_id),
            telegram_chat_id=int(telegram_chat_id),
            payer_name=payer_name,
        )
    except Exception:
        logger.exception(
            "multi_payer_warning: evaluate failed method=%s payment_id=%s chat_id=%s",
            payment_method_slug,
            payment_id,
            telegram_chat_id,
        )
        return False

    if not payer_names:
        return False

    title = (group_title or "").strip() or str(telegram_chat_id)
    telegram_text = format_multi_payer_warning_text(
        payment_method_slug=payment_method_slug,
        group_title=title,
        payer_names=payer_names,
        html=True,
    )
    slack_text = format_multi_payer_warning_text(
        payment_method_slug=payment_method_slug,
        group_title=title,
        payer_names=payer_names,
        html=False,
    )

    try:
        from bot.services.venmo_payments import send_telegram_notification

        reply_to = (
            int(notification_message_id)
            if notification_message_id is not None
            else None
        )
        try:
            await send_telegram_notification(
                telegram_text,
                reply_to_message_id=reply_to,
            )
        except Exception:
            if reply_to is not None:
                logger.warning(
                    "multi_payer_warning: reply send failed; retrying without reply "
                    "method=%s payment_id=%s",
                    payment_method_slug,
                    payment_id,
                    exc_info=True,
                )
                await send_telegram_notification(telegram_text)
            else:
                raise
    except Exception:
        logger.exception(
            "multi_payer_warning: telegram send failed method=%s payment_id=%s",
            payment_method_slug,
            payment_id,
        )

    try:
        from bot.services.slack_ops_notify import notify_slack_ops

        await notify_slack_ops(slack_text, source=_MULTI_PAYER_SOURCE)
    except Exception:
        logger.exception(
            "multi_payer_warning: slack send failed method=%s payment_id=%s",
            payment_method_slug,
            payment_id,
        )

    logger.info(
        "multi_payer_warning: sent method=%s payment_id=%s chat_id=%s names=%s",
        payment_method_slug,
        payment_id,
        telegram_chat_id,
        len(payer_names),
    )
    return True
