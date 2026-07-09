"""Update-age staleness and amount detection for deposit/cashout conversation handlers."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from telegram import Update
from telegram.ext import ContextTypes, filters

from config import ADMIN_USER_IDS

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


def log_stale_update(update: Update, *, handler: str, reason: str = "age") -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    age = update_age_seconds(update)
    logger.info(
        "stale update ignored handler=%s reason=%s age=%ss max=%ss chat_id=%s",
        handler,
        reason,
        f"{age:.1f}" if age is not None else "?",
        update_max_age_seconds(),
        chat_id,
    )


FlowName = Literal["deposit", "cashout"]


def _flow_message_ids_key(flow: FlowName) -> str:
    return f"{flow}_flow_message_ids"


def reset_flow_callback_messages(context: ContextTypes.DEFAULT_TYPE, *, flow: FlowName) -> None:
    context.chat_data.pop(_flow_message_ids_key(flow), None)


def register_flow_callback_message(
    context: ContextTypes.DEFAULT_TYPE,
    message_id: int | None,
    *,
    flow: FlowName,
) -> None:
    if message_id is None:
        return
    key = _flow_message_ids_key(flow)
    ids = context.chat_data.get(key)
    if not isinstance(ids, set):
        ids = set()
        context.chat_data[key] = ids
    ids.add(int(message_id))


def _callback_message_id(update: Update) -> int | None:
    query = update.callback_query
    if query is None or query.message is None:
        return None
    return int(query.message.message_id)


def has_active_deposit_flow(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.chat_data.get("deposit_amount") is not None


def has_active_cashout_flow(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.chat_data.get("cashout_amount") is not None


def is_flow_callback_stale(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    flow: FlowName,
    handler: str,
    now: datetime | None = None,
) -> bool:
    """True when an in-flow inline button should be rejected.

  Active deposit/cashout sessions trust conversation_timeout instead of message
  age, but only for callback messages registered during the current flow.
  Orphan taps on older pickers are rejected even when a new session is active.
    """
    if update.callback_query is None:
        return is_update_too_old(update, now=now)

    active = has_active_deposit_flow(context) if flow == "deposit" else has_active_cashout_flow(context)
    if active:
        msg_id = _callback_message_id(update)
        flow_ids = context.chat_data.get(_flow_message_ids_key(flow))
        if isinstance(flow_ids, set) and msg_id is not None and msg_id in flow_ids:
            return False
        log_stale_update(update, handler=handler, reason="orphan_callback")
        return True

    if is_update_too_old(update, now=now):
        log_stale_update(update, handler=handler, reason="age")
        return True
    return False


async def handle_stale_flow_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    flow: FlowName,
    handler: str,
    cleanup: Callable[[Any], None],
) -> bool:
    """Answer, cleanup, and return True when the callback is stale."""
    if not is_flow_callback_stale(update, context, flow=flow, handler=handler):
        return False
    await answer_stale_callback(update, context, flow_command=f"/{flow}")
    cleanup(context)
    return True


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
        # Admin-initiated: player or support account may enter the amount.
        return True
    depositor_id = context.chat_data.get("deposit_user_id")
    return depositor_id is not None and sender_id == depositor_id


def deposit_amount_show_validation_error(context, *, sender_id: int | None) -> bool:
    """Players get parse errors; support accounts in admin-initiated flow do not."""
    if (
        context.chat_data.get("deposit_admin_initiated")
        and sender_id is not None
        and sender_id in ADMIN_USER_IDS
    ):
        return False
    return True


def cashout_amount_actor_allowed(
    context,
    *,
    sender_id: int | None,
    text: str | None,
) -> bool:
    if sender_id is None or not looks_like_amount(text):
        return False
    if context.chat_data.get("cashout_admin_initiated"):
        # Admin starts /cashout; the customer (not a global admin) enters the amount.
        return sender_id not in ADMIN_USER_IDS
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
