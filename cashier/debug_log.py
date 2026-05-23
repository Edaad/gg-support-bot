"""Debug helpers for GGCashier — enable with CASHIER_VERBOSE_LOGS=true."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from telegram import Update
from telegram.ext import ConversationHandler

logger = logging.getLogger(__name__)

_STATE_NAMES = {
    0: "GC_TITLE",
    1: "GC_AMOUNT",
    2: "GC_CONFIRM",
    3: "GC_TRADE",
    4: "GC_COOLDOWN",
    5: "GC_METHOD",
    6: "GC_SUB",
    7: "GC_PAYOUT",
    8: "GC_CONFIRM_DETAILS",
}


def is_cashier_verbose() -> bool:
    v = (os.getenv("CASHIER_VERBOSE_LOGS") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def state_label(state: Any) -> str:
    if state is None:
        return "None"
    if state == ConversationHandler.END:
        return "END"
    if isinstance(state, int):
        return _STATE_NAMES.get(state, f"state_{state}")
    return repr(state)


def log_update(update: Update, label: str, *, level: int = logging.INFO) -> None:
    """Log summary of an incoming Telegram update."""
    parts = [f"cashier_debug [{label}]"]
    if update.update_id is not None:
        parts.append(f"update_id={update.update_id}")
    if update.effective_user:
        parts.append(f"user_id={update.effective_user.id}")
        if update.effective_user.username:
            parts.append(f"username=@{update.effective_user.username}")
    if update.effective_chat:
        parts.append(f"chat_id={update.effective_chat.id}")
        parts.append(f"chat_type={update.effective_chat.type}")
    if update.callback_query:
        parts.append(f"callback_data={update.callback_query.data!r}")
        msg = update.callback_query.message
        if msg:
            parts.append(f"callback_msg_id={msg.message_id}")
    elif update.message:
        parts.append(f"msg_id={update.message.message_id}")
        text = (update.message.text or "")[:80]
        if text:
            parts.append(f"text={text!r}")
    logger.log(level, " ".join(parts))


def log_conversation_state(
    conversation: ConversationHandler,
    update: Update,
    label: str,
    *,
    new_state: Optional[Any] = None,
) -> None:
    """Log ConversationHandler key and state before/after a transition."""
    if not is_cashier_verbose() and new_state is None:
        return
    try:
        key = conversation._get_key(update)
        old = conversation._conversations.get(key)
    except Exception:
        logger.debug("cashier_debug [%s] could not read conversation state", label)
        return

    if new_state is not None:
        logger.info(
            "cashier_debug [%s] conv_key=%s old_state=%s new_state=%s active_keys=%s",
            label,
            key,
            state_label(old),
            state_label(new_state),
            len(conversation._conversations),
        )
    else:
        logger.info(
            "cashier_debug [%s] conv_key=%s current_state=%s active_keys=%s",
            label,
            key,
            state_label(old),
            len(conversation._conversations),
        )


def log_user_data_keys(context, label: str) -> None:
    if not is_cashier_verbose():
        return
    keys = [k for k in context.user_data.keys() if str(k).startswith("gc_")]
    logger.info("cashier_debug [%s] user_data gc_keys=%s", label, keys)
