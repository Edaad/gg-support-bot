"""Update-age staleness and amount detection for deposit/cashout conversation handlers."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import ContextTypes, filters

logger = logging.getLogger(__name__)

_STALE_CALLBACK_ALERT = "This session expired — use {flow_command} again."

_DEFAULT_MAX_AGE_SECONDS = 60
_NON_AMOUNT_TEXT_RE = re.compile(r"[a-zA-Z]")


def update_max_age_seconds() -> int:
    raw = (os.getenv("BOT_UPDATE_MAX_AGE_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_AGE_SECONDS
    return max(1, value)


def update_effective_date(update: Update) -> datetime | None:
    message = update.effective_message
    if message is None or message.date is None:
        return None
    dt = message.date
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def update_age_seconds(update: Update, *, now: datetime | None = None) -> float | None:
    effective = update_effective_date(update)
    if effective is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return (now - effective).total_seconds()


def is_update_too_old(update: Update, *, now: datetime | None = None) -> bool:
    age = update_age_seconds(update, now=now)
    if age is None:
        return True
    return age > update_max_age_seconds()


def log_stale_update(update: Update, *, handler: str) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    age = update_age_seconds(update)
    logger.info(
        "stale update ignored handler=%s age=%ss max=%ss chat_id=%s",
        handler,
        f"{age:.1f}" if age is not None else "?",
        update_max_age_seconds(),
        chat_id,
    )


class _AmountTextFilter(filters.MessageFilter):
    """Only match messages that look like a dollar amount (not usernames or notes)."""

    def filter(self, message) -> bool:  # type: ignore[override]
        return looks_like_amount(getattr(message, "text", None))


AMOUNT_TEXT = _AmountTextFilter()


def looks_like_amount(text: str | None) -> bool:
    """True when the full message is a single numeric amount (no extra words)."""
    raw = (text or "").strip().replace("$", "").replace(",", "")
    if not raw or _NON_AMOUNT_TEXT_RE.search(raw):
        return False
    try:
        Decimal(raw)
    except (InvalidOperation, Exception):
        return False
    return True


def deposit_amount_actor_allowed(
    context,
    *,
    sender_id: int | None,
    text: str | None,
) -> bool:
    if sender_id is None or not looks_like_amount(text):
        return False
    if context.chat_data.get("deposit_admin_initiated"):
        return sender_id == context.chat_data.get("deposit_admin_user_id")
    depositor_id = context.chat_data.get("deposit_user_id")
    return depositor_id is not None and sender_id == depositor_id


def cashout_amount_actor_allowed(
    context,
    *,
    sender_id: int | None,
    text: str | None,
) -> bool:
    if sender_id is None or not looks_like_amount(text):
        return False
    if context.chat_data.get("cashout_admin_initiated"):
        return sender_id == context.chat_data.get("cashout_admin_user_id")
    cashouter_id = context.chat_data.get("cashout_user_id")
    return cashouter_id is not None and sender_id == cashouter_id


async def answer_stale_callback(
    update: Update,
    _context: ContextTypes.DEFAULT_TYPE,
    *,
    flow_command: str,
) -> None:
    query = update.callback_query
    if query is None:
        return
    try:
        await query.answer(
            _STALE_CALLBACK_ALERT.format(flow_command=flow_command),
            show_alert=True,
        )
    except Exception:
        pass
