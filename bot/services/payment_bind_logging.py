"""Structured logs for multi-candidate payment binding.

Grep notification / web dyno logs with: payment_bind:
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.services.payment_bind_candidates import CandidateGroup

logger = logging.getLogger(__name__)

_BINDING_TABLE = {
    "venmo": "venmo_payer_bindings",
    "zelle": "zelle_payer_bindings",
    "cashapp": "cashapp_payer_bindings",
    "paypal": "paypal_payer_bindings",
    "crypto": "crypto_wallet_bindings",
}


def format_candidates(candidates: list[Any]) -> str:
    if not candidates:
        return "[]"
    parts: list[str] = []
    for candidate in candidates:
        chat_id = getattr(candidate, "telegram_chat_id", None)
        title = getattr(candidate, "group_title", None) or "?"
        club_id = getattr(candidate, "club_id", None)
        parts.append(f"chat_id={chat_id} club_id={club_id} title={title!r}")
    return "[" + "; ".join(parts) + "]"


def format_payment_row(payment: object) -> str:
    parts = [f"id={getattr(payment, 'id', None)}"]
    if getattr(payment, "payer_name", None):
        parts.append(f"payer={getattr(payment, 'payer_name')!r}")
    if getattr(payment, "from_address", None):
        parts.append(f"from_address={getattr(payment, 'from_address')!r}")
    if getattr(payment, "alert_scope", None):
        parts.append(f"alert_scope={getattr(payment, 'alert_scope')!r}")
    parts.append(f"telegram_chat_id={getattr(payment, 'telegram_chat_id', None)}")
    parts.append(f"auto_bound={getattr(payment, 'auto_bound', None)}")
    parts.append(
        f"notification={getattr(payment, 'notification_chat_id', None)}"
        f"/{getattr(payment, 'notification_message_id', None)}"
    )
    return " ".join(parts)


def log_candidate_list(
    *,
    method_slug: str,
    identity_label: str,
    candidates: list[Any],
    payment_id: int | None = None,
    filter_alert_scope: str | None = None,
) -> None:
    logger.info(
        "payment_bind: candidate_list method=%s payment_id=%s identity=%s "
        "count=%s filter_scope=%s candidates=%s",
        method_slug,
        payment_id,
        identity_label,
        len(candidates),
        filter_alert_scope,
        format_candidates(candidates),
    )


def log_ingest_outcome(
    *,
    method_slug: str,
    payment_id: int,
    identity_label: str,
    candidate_count: int,
    auto_bound: bool,
    bound_chat_id: int | None = None,
    bound_title: str | None = None,
    setup_blocked: bool = False,
    setup_target_chat_id: int | None = None,
    notification_keyboard: str | None = None,
) -> None:
    if auto_bound:
        outcome = "auto_bind_single_candidate"
    elif setup_blocked:
        outcome = "setup_blocked_unbound"
    elif candidate_count > 1:
        outcome = "ambiguous_unbound"
    elif candidate_count == 1:
        outcome = "single_candidate_unbound"
    else:
        outcome = "unbound_no_candidates"

    logger.info(
        "payment_bind: ingest_outcome method=%s payment_id=%s identity=%s "
        "outcome=%s candidate_count=%s auto_bound=%s bound_chat_id=%s "
        "bound_title=%r setup_blocked=%s setup_target_chat_id=%s "
        "notification_keyboard=%s",
        method_slug,
        payment_id,
        identity_label,
        outcome,
        candidate_count,
        auto_bound,
        bound_chat_id,
        bound_title,
        setup_blocked,
        setup_target_chat_id,
        notification_keyboard,
    )


def log_binding_table_write(
    *,
    operation: str,
    method_slug: str,
    identity_label: str,
    telegram_chat_id: int,
    club_id: int | None = None,
    bound_group_title: str | None = None,
    actor_telegram_user_id: int | None = None,
    rows_affected: int | None = None,
) -> None:
    table = _BINDING_TABLE.get(method_slug, "?")
    logger.info(
        "payment_bind: binding_table operation=%s table=%s method=%s "
        "identity=%s telegram_chat_id=%s club_id=%s bound_title=%r "
        "actor_user_id=%s rows_affected=%s",
        operation,
        table,
        method_slug,
        identity_label,
        telegram_chat_id,
        club_id,
        bound_group_title,
        actor_telegram_user_id,
        rows_affected,
    )


def log_notification_post(
    *,
    method_slug: str,
    payment_id: int,
    notification_chat_id: int,
    notification_message_id: int,
    has_keyboard: bool,
    keyboard_kind: str | None = None,
) -> None:
    logger.info(
        "payment_bind: notification_post method=%s payment_id=%s "
        "chat_id=%s message_id=%s has_keyboard=%s keyboard_kind=%s",
        method_slug,
        payment_id,
        notification_chat_id,
        notification_message_id,
        has_keyboard,
        keyboard_kind,
    )


def log_notification_edit(
    *,
    method_slug: str,
    payment_id: int,
    notification_chat_id: int,
    notification_message_id: int,
    has_keyboard: bool,
    keyboard_kind: str | None = None,
    reason: str | None = None,
) -> None:
    logger.info(
        "payment_bind: notification_edit method=%s payment_id=%s "
        "chat_id=%s message_id=%s has_keyboard=%s keyboard_kind=%s reason=%s",
        method_slug,
        payment_id,
        notification_chat_id,
        notification_message_id,
        has_keyboard,
        keyboard_kind,
        reason,
    )


def log_callback(
    *,
    action: str,
    method_slug: str,
    payment_id: int,
    target_chat_id: int | None,
    actor_telegram_user_id: int,
    notification_message_id: int | None,
    payment_row: str | None = None,
) -> None:
    logger.info(
        "payment_bind: callback action=%s method=%s payment_id=%s "
        "target_chat_id=%s actor_user_id=%s notification_message_id=%s payment=%s",
        action,
        method_slug,
        payment_id,
        target_chat_id,
        actor_telegram_user_id,
        notification_message_id,
        payment_row,
    )


def log_callback_result(
    *,
    action: str,
    method_slug: str,
    payment_id: int,
    ok: bool,
    error: str | None = None,
) -> None:
    level = logging.INFO if ok else logging.WARNING
    logger.log(
        level,
        "payment_bind: callback_result action=%s method=%s payment_id=%s ok=%s error=%r",
        action,
        method_slug,
        payment_id,
        ok,
        error,
    )


def log_reply_branch(
    *,
    method_slug: str,
    payment_id: int,
    branch: str,
    actor_telegram_user_id: int,
    reply_title: str,
    bound_chat_id: int | None = None,
    candidate_count: int = 0,
    payment_row: str | None = None,
) -> None:
    logger.info(
        "payment_bind: reply_branch method=%s payment_id=%s branch=%s "
        "actor_user_id=%s reply_title=%r bound_chat_id=%s candidate_count=%s "
        "payment=%s",
        method_slug,
        payment_id,
        branch,
        actor_telegram_user_id,
        reply_title,
        bound_chat_id,
        candidate_count,
        payment_row,
    )
