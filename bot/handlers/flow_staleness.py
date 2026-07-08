"""Update-age staleness and amount detection for deposit/cashout conversation handlers."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

from telegram import Update
from telegram.ext import ContextTypes, filters

from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

_STALE_CALLBACK_ALERT = "This session expired — use {flow_command} again."
_ORPHAN_CALLBACK_ALERT = (
    "That button is from an earlier {flow_label} — use the latest message or "
    "run {flow_command} again."
)

_DEFAULT_MAX_AGE_SECONDS = 60
_NON_AMOUNT_TEXT_RE = re.compile(r"[a-zA-Z]")

FlowKind = Literal["deposit", "cashout"]
FlowCallbackStaleness = Literal["fresh", "orphaned", "expired"]

_FLOW_AMOUNT_KEY = {
    "deposit": "deposit_amount",
    "cashout": "cashout_amount",
}
_FLOW_CALLBACK_IDS_KEY = {
    "deposit": "deposit_callback_message_ids",
    "cashout": "cashout_callback_message_ids",
}
_FLOW_COMMAND = {
    "deposit": "/deposit",
    "cashout": "/cashout",
}
_FLOW_LABEL = {
    "deposit": "deposit",
    "cashout": "cashout",
}


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


def log_stale_flow_callback(
    update: Update,
    *,
    handler: str,
    flow: FlowKind,
    kind: FlowCallbackStaleness,
) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    age = update_age_seconds(update)
    logger.info(
        "stale flow callback ignored handler=%s flow=%s kind=%s age=%ss chat_id=%s",
        handler,
        flow,
        kind,
        f"{age:.1f}" if age is not None else "?",
        chat_id,
    )


def track_flow_callback_message(
    context,
    flow: FlowKind,
    message_id: int | None,
) -> None:
    if message_id is None:
        return
    key = _FLOW_CALLBACK_IDS_KEY[flow]
    ids = context.chat_data.setdefault(key, [])
    mid = int(message_id)
    if mid not in ids:
        ids.append(mid)


def clear_flow_callback_messages(context, flow: FlowKind) -> None:
    context.chat_data.pop(_FLOW_CALLBACK_IDS_KEY[flow], None)


def _tracked_flow_callback_message_ids(context, flow: FlowKind) -> set[int]:
    raw = context.chat_data.get(_FLOW_CALLBACK_IDS_KEY[flow]) or []
    return {int(mid) for mid in raw}


def classify_flow_callback(
    update: Update,
    context,
    *,
    flow: FlowKind,
    now: datetime | None = None,
) -> FlowCallbackStaleness:
    """Classify an in-flow inline button tap.

    fresh — active session and callback targets the current flow UI (or no
    tracked ids yet). Not limited by message post age.

    orphaned — active session but callback is on an older inline keyboard.

    expired — no active session and update is older than the deploy-backlog
    cutoff.
    """
    amount_key = _FLOW_AMOUNT_KEY[flow]
    has_session = context.chat_data.get(amount_key) is not None

    query = update.callback_query
    if query is None:
        return "expired" if is_update_too_old(update, now=now) else "fresh"

    msg_id = query.message.message_id if query.message else None
    tracked = _tracked_flow_callback_message_ids(context, flow)

    if has_session:
        if not tracked or (msg_id is not None and msg_id in tracked):
            return "fresh"
        return "orphaned"

    return "expired" if is_update_too_old(update, now=now) else "fresh"


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
    orphaned: bool = False,
) -> None:
    query = update.callback_query
    if query is None:
        return
    flow_label = flow_command.lstrip("/")
    alert = (
        _ORPHAN_CALLBACK_ALERT.format(
            flow_label=flow_label,
            flow_command=flow_command,
        )
        if orphaned
        else _STALE_CALLBACK_ALERT.format(flow_command=flow_command)
    )
    try:
        await query.answer(alert, show_alert=True)
    except Exception:
        pass


async def reject_stale_flow_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    handler: str,
    flow: FlowKind,
) -> FlowCallbackStaleness | None:
    """If callback is stale, answer the user and return the staleness kind."""
    kind = classify_flow_callback(update, context, flow=flow)
    if kind == "fresh":
        return None
    log_stale_flow_callback(update, handler=handler, flow=flow, kind=kind)
    await answer_stale_callback(
        update,
        context,
        flow_command=_FLOW_COMMAND[flow],
        orphaned=kind == "orphaned",
    )
    return kind
