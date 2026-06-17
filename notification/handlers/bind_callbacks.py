"""CallbackQuery handler for payment bind confirm flows."""

from __future__ import annotations

import logging

from telegram import ForceReply, Update
from telegram.ext import ContextTypes

from bot.services.payment_bind_candidates import (
    METHOD_FROM_SHORT,
    bind_scope_mismatch_error,
    candidates_for_payment,
)
from bot.services.payment_bind_logging import format_payment_row, log_callback, log_callback_result
from bot.services.venmo_payments import resolve_bound_group, resolve_display_group_title
from db.connection import get_db
from notification.bind_actions import (
    confirm_add_candidate,
    confirm_bind_payment,
    confirm_reset_candidates,
    crypto_scope_error,
    load_payment,
    refresh_unbound_notification,
)
from notification.bind_keyboards import (
    confirm_add_candidate_markup,
    confirm_bind_markup,
    confirm_reset_markup,
    candidate_picker_markup,
    to_inline_keyboard,
)
from notification.chat_id import telegram_chat_ids_match
from notification.handlers._chat import notification_chat_id
from notification.payment_lookup import find_payment_by_notification

logger = logging.getLogger(__name__)

_BIND_ADD_MEMBER_KEY = "bind_add_member_pending"


def _parse_callback(data: str) -> tuple[str, str, int, int | None] | None:
    """Return (action, method_slug, payment_id, chat_id?) from pb:action:short:id[:chat]."""
    parts = (data or "").split(":")
    if len(parts) < 4 or parts[0] != "pb":
        return None
    action = parts[1]
    short = parts[2]
    method_slug = METHOD_FROM_SHORT.get(short)
    if method_slug is None:
        return None
    try:
        payment_id = int(parts[3])
    except ValueError:
        return None
    chat_id: int | None = None
    if len(parts) >= 5:
        try:
            chat_id = int(parts[4])
        except ValueError:
            return None
    return action, method_slug, payment_id, chat_id


def _bind_scope_error_for_chat(payment: object, chat_id: int) -> str | None:
    title = resolve_display_group_title(int(chat_id))
    if not title:
        return None
    return bind_scope_mismatch_error(
        payment_is_test=bool(getattr(payment, "is_test", False)),
        group_title=title,
    )


async def payment_bind_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return

    expected_chat = notification_chat_id()
    if expected_chat is None:
        await query.answer("Notification chat not configured.")
        return

    message = query.message
    if message is None or not telegram_chat_ids_match(int(message.chat_id), expected_chat):
        await query.answer("Wrong chat.")
        return

    parsed = _parse_callback(query.data)
    if parsed is None:
        await query.answer("Unknown action.")
        return

    action, method_slug, payment_id, target_chat_id = parsed
    actor_id = int(query.from_user.id) if query.from_user else 0

    log_callback(
        action=action,
        method_slug=method_slug,
        payment_id=payment_id,
        target_chat_id=target_chat_id,
        actor_telegram_user_id=actor_id,
        notification_message_id=int(message.message_id),
    )

    payment = load_payment(method_slug, payment_id)
    if payment is None:
        log_callback_result(
            action=action,
            method_slug=method_slug,
            payment_id=payment_id,
            ok=False,
            error="payment_not_found",
        )
        await query.answer("Payment not found or expired.", show_alert=True)
        return

    notif_msg_id = getattr(payment, "notification_message_id", None)
    if notif_msg_id is not None and int(notif_msg_id) != int(message.message_id):
        log_callback_result(
            action=action,
            method_slug=method_slug,
            payment_id=payment_id,
            ok=False,
            error="stale_button",
        )
        await query.answer("This button is stale.", show_alert=True)
        return

    logger.info(
        "payment_bind: callback payment_row method=%s payment_id=%s %s",
        method_slug,
        payment_id,
        format_payment_row(payment),
    )

    if action == "s" and target_chat_id is not None:
        title = resolve_display_group_title(int(target_chat_id)) or "selected group"
        scope_err = crypto_scope_error(method_slug, payment, _club_id_for_chat(int(target_chat_id)))
        if scope_err:
            await query.answer(scope_err, show_alert=True)
            return
        bind_scope_err = _bind_scope_error_for_chat(payment, int(target_chat_id))
        if bind_scope_err:
            await query.answer(bind_scope_err, show_alert=True)
            return
        await query.answer()
        await message.edit_reply_markup(
            reply_markup=to_inline_keyboard(
                confirm_bind_markup(method_slug, payment_id, target_chat_id)
            )
        )
        return

    if action == "b":
        await query.answer()
        with get_db() as session:
            fresh = load_payment(method_slug, payment_id)
            if fresh is None:
                return
            candidates = candidates_for_payment(
                session,
                fresh,
                method_slug,
                filter_alert_scope=getattr(fresh, "alert_scope", None)
                if method_slug == "crypto"
                else None,
            )
        if len(candidates) > 1:
            await message.edit_reply_markup(
                reply_markup=to_inline_keyboard(
                    candidate_picker_markup(method_slug, payment_id, candidates)
                )
            )
        else:
            await message.edit_reply_markup(reply_markup=None)
        return

    if action in ("r", "c") and target_chat_id is not None:
        if action == "r":
            scope_err = crypto_scope_error(method_slug, payment, _club_id_for_chat(int(target_chat_id)))
            if scope_err:
                await query.answer(scope_err, show_alert=True)
                return
            bind_scope_err = _bind_scope_error_for_chat(payment, int(target_chat_id))
            if bind_scope_err:
                await query.answer(bind_scope_err, show_alert=True)
                return
            await query.answer()
            await message.edit_reply_markup(
                reply_markup=to_inline_keyboard(
                    confirm_bind_markup(method_slug, payment_id, target_chat_id)
                )
            )
            return
        ok, err = await confirm_bind_payment(
            method_slug=method_slug,
            payment_id=payment_id,
            target_chat_id=target_chat_id,
            actor_telegram_user_id=actor_id,
        )
        log_callback_result(
            action=action,
            method_slug=method_slug,
            payment_id=payment_id,
            ok=ok,
            error=err,
        )
        if not ok:
            await query.answer(err or "Bind failed.", show_alert=True)
            return
        await query.answer("Payment bound.")
        await message.edit_reply_markup(reply_markup=None)
        return

    if action == "a" and target_chat_id is not None:
        scope_err = crypto_scope_error(method_slug, payment, _club_id_for_chat(int(target_chat_id)))
        if scope_err:
            await query.answer(scope_err, show_alert=True)
            return
        bind_scope_err = _bind_scope_error_for_chat(payment, int(target_chat_id))
        if bind_scope_err:
            await query.answer(bind_scope_err, show_alert=True)
            return
        current = resolve_display_group_title(int(payment.telegram_chat_id)) if payment.telegram_chat_id else "Unbound"
        await query.answer()
        await message.edit_reply_markup(
            reply_markup=to_inline_keyboard(
                confirm_add_candidate_markup(method_slug, payment_id, target_chat_id)
            )
        )
        return

    if action == "ac" and target_chat_id is not None:
        ok, err = await confirm_add_candidate(
            method_slug=method_slug,
            payment_id=payment_id,
            target_chat_id=target_chat_id,
            actor_telegram_user_id=actor_id,
        )
        log_callback_result(
            action=action,
            method_slug=method_slug,
            payment_id=payment_id,
            ok=ok,
            error=err,
        )
        if not ok:
            await query.answer(err or "Could not add candidate.", show_alert=True)
            return
        await query.answer("Added as possible match.")
        return

    if action == "rs":
        await query.answer()
        await message.edit_reply_markup(
            reply_markup=to_inline_keyboard(confirm_reset_markup(method_slug, payment_id))
        )
        return

    if action == "rc":
        ok, err = await confirm_reset_candidates(
            method_slug=method_slug,
            payment_id=payment_id,
            actor_telegram_user_id=actor_id,
        )
        log_callback_result(
            action=action,
            method_slug=method_slug,
            payment_id=payment_id,
            ok=ok,
            error=err,
        )
        if not ok:
            await query.answer(err or "Reset failed.", show_alert=True)
            return
        await query.answer("Bindings reset. Reply with a group title to bind.")
        await message.edit_reply_markup(reply_markup=None)
        return

    if action == "m":
        context.user_data[_BIND_ADD_MEMBER_KEY] = {
            "method_slug": method_slug,
            "payment_id": payment_id,
            "notification_message_id": int(message.message_id),
        }
        logger.info(
            "payment_bind: add_member_prompt method=%s payment_id=%s actor_user_id=%s",
            method_slug,
            payment_id,
            actor_id,
        )
        await query.answer()
        await message.reply_text(
            "Send the group title for the member to add.",
            reply_markup=ForceReply(selective=True),
        )
        return

    await query.answer("Unknown action.")


async def payment_bind_add_member_reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle ForceReply title after Add another member."""
    if not update.message or not update.effective_user:
        return

    pending = context.user_data.get(_BIND_ADD_MEMBER_KEY)
    if not pending:
        return

    expected_chat = notification_chat_id()
    if expected_chat is None or not telegram_chat_ids_match(
        int(update.effective_chat.id), expected_chat
    ):
        return

    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Send the group title as text.")
        return

    method_slug = pending["method_slug"]
    payment_id = int(pending["payment_id"])
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        context.user_data.pop(_BIND_ADD_MEMBER_KEY, None)
        await update.message.reply_text("Payment not found.")
        return

    resolved = resolve_bound_group(title)
    if not resolved.ok or resolved.bound_group is None:
        await update.message.reply_text(resolved.error or "Could not resolve group.")
        return

    group = resolved.bound_group
    scope_err = crypto_scope_error(method_slug, payment, group.club_id)
    if scope_err:
        await update.message.reply_text(scope_err)
        return

    bind_scope_err = bind_scope_mismatch_error(
        payment_is_test=bool(getattr(payment, "is_test", False)),
        group_title=group.group_title,
    )
    if bind_scope_err:
        await update.message.reply_text(bind_scope_err)
        return

    context.user_data.pop(_BIND_ADD_MEMBER_KEY, None)
    logger.info(
        "payment_bind: add_member_resolved method=%s payment_id=%s target_chat_id=%s "
        "title=%r actor_user_id=%s",
        method_slug,
        payment_id,
        group.telegram_chat_id,
        group.group_title,
        update.effective_user.id,
    )
    await update.message.reply_text(
        f"Confirm add {group.group_title} as possible match?",
        reply_markup=to_inline_keyboard(
            confirm_add_candidate_markup(method_slug, payment_id, group.telegram_chat_id)
        ),
    )


def _club_id_for_chat(chat_id: int) -> int:
    from bot.services.club import get_group_title_for_chat

    _title, club_id = get_group_title_for_chat(int(chat_id))
    if club_id is None:
        raise ValueError("club_id not found")
    return int(club_id)
