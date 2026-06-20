"""CallbackQuery handler for payment bind confirm flows."""

from __future__ import annotations

import logging

from telegram import ForceReply, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.services.group_chat_invite_links import resolve_group_chat_url_for_payment
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
from notification.payment_bind_helpers import (
    format_payment_notification,
    inject_pending_confirm_group_line,
)

logger = logging.getLogger(__name__)

BIND_ADD_MEMBER_PENDING_KEY = "bind_add_member_pending"
_BIND_ADD_MEMBER_KEY = BIND_ADD_MEMBER_PENDING_KEY


def _canonical_notification_chat_id(chat_id: int) -> int | None:
    """Map Telegram chat id variants to the configured notification chat id."""
    expected = notification_chat_id()
    if expected is None:
        return int(chat_id)
    if telegram_chat_ids_match(int(chat_id), int(expected)):
        return int(expected)
    return None


def _pending_store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault(BIND_ADD_MEMBER_PENDING_KEY, {})


def get_add_member_pending(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
) -> dict | None:
    """Pending add-member flow for the shared notification chat (any staff may reply)."""
    key = _canonical_notification_chat_id(chat_id)
    if key is None:
        return None
    pending = _pending_store(context).get(key)
    return pending if isinstance(pending, dict) else None


def set_add_member_pending(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    method_slug: str,
    payment_id: int,
    notification_message_id: int,
    actor_user_id: int,
) -> None:
    key = _canonical_notification_chat_id(chat_id)
    if key is None:
        logger.warning(
            "payment_bind: add_member_pending_set skipped unknown chat_id=%s",
            chat_id,
        )
        return
    _pending_store(context)[key] = {
        "method_slug": method_slug,
        "payment_id": payment_id,
        "notification_message_id": notification_message_id,
        "actor_user_id": actor_user_id,
    }
    logger.info(
        "payment_bind: add_member_pending_set chat_id=%s key=%s method=%s payment_id=%s",
        chat_id,
        key,
        method_slug,
        payment_id,
    )


def clear_add_member_pending(context: ContextTypes.DEFAULT_TYPE, *, chat_id: int) -> None:
    key = _canonical_notification_chat_id(chat_id)
    if key is None:
        return
    _pending_store(context).pop(key, None)

# Reassign / add-candidate flows post buttons on a separate bot reply, not the
# original notification message — do not apply notification message_id stale check.
_REPLY_MESSAGE_ACTIONS = frozenset({"r", "a", "c", "ac", "b"})


async def _safe_clear_reply_markup(message) -> None:
    """Remove inline keyboard; ignore Telegram 'not modified' after prior edits."""
    try:
        await message.edit_reply_markup(reply_markup=None)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _safe_finish_reply_message(message, text: str) -> None:
    """Replace bot prompt text and remove inline keyboard."""
    try:
        await message.edit_text(text, reply_markup=None)
    except BadRequest as exc:
        err = str(exc).lower()
        if "not modified" in err:
            await _safe_clear_reply_markup(message)
            return
        if "there is no text in the message to edit" in err:
            await _safe_clear_reply_markup(message)
            await message.reply_text(text)
            return
        raise


async def _safe_edit_notification_message(message, text: str, *, reply_markup=None) -> None:
    """Edit notification body (HTML) and keyboard; ignore harmless Telegram errors."""
    try:
        kwargs: dict = {"text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await message.edit_text(**kwargs)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _ambiguous_notification_text(
    *,
    method_slug: str,
    payment: object,
    candidates: list,
) -> str:
    group_chat_url = await resolve_group_chat_url_for_payment(payment, group_title=None)
    return format_payment_notification(
        method_slug,
        payment,
        group_chat_url=group_chat_url,
        ambiguous_candidates=candidates if len(candidates) > 1 else None,
    )


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


def _is_stale_notification_button(
    *,
    action: str,
    payment_notification_message_id: int | None,
    callback_message_id: int,
) -> bool:
    if payment_notification_message_id is None:
        return False
    if int(payment_notification_message_id) == int(callback_message_id):
        return False
    return action not in _REPLY_MESSAGE_ACTIONS


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
    if _is_stale_notification_button(
        action=action,
        payment_notification_message_id=(
            int(notif_msg_id) if notif_msg_id is not None else None
        ),
        callback_message_id=int(message.message_id),
    ):
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
        with get_db() as session:
            candidates = candidates_for_payment(
                session,
                payment,
                method_slug,
                filter_alert_scope=getattr(payment, "alert_scope", None)
                if method_slug == "crypto"
                else None,
            )
        base_text = await _ambiguous_notification_text(
            method_slug=method_slug,
            payment=payment,
            candidates=candidates,
        )
        text = inject_pending_confirm_group_line(base_text, title)
        await _safe_edit_notification_message(
            message,
            text,
            reply_markup=to_inline_keyboard(
                confirm_bind_markup(
                    method_slug,
                    payment_id,
                    target_chat_id,
                    group_title=title,
                )
            ),
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
            text = await _ambiguous_notification_text(
                method_slug=method_slug,
                payment=fresh,
                candidates=candidates,
            )
            await _safe_edit_notification_message(
                message,
                text,
                reply_markup=to_inline_keyboard(
                    candidate_picker_markup(method_slug, payment_id, candidates)
                ),
            )
        else:
            await _safe_clear_reply_markup(message)
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
            title = resolve_display_group_title(int(target_chat_id)) or "selected group"
            await message.edit_reply_markup(
                reply_markup=to_inline_keyboard(
                    confirm_bind_markup(
                        method_slug,
                        payment_id,
                        target_chat_id,
                        group_title=title,
                    )
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
        title = resolve_display_group_title(int(target_chat_id)) or "selected group"
        await query.answer("Payment bound.")
        if notif_msg_id is not None and int(message.message_id) == int(notif_msg_id):
            await _safe_clear_reply_markup(message)
        else:
            await _safe_finish_reply_message(message, f"Bound to {title}.")
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
        title = resolve_display_group_title(int(target_chat_id)) or "selected group"
        await query.answer("Added as possible match.")
        await _safe_finish_reply_message(
            message,
            f"Added {title} as possible match.",
        )
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
        await _safe_clear_reply_markup(message)
        return

    if action == "m":
        set_add_member_pending(
            context,
            chat_id=int(message.chat_id),
            method_slug=method_slug,
            payment_id=payment_id,
            notification_message_id=int(message.message_id),
            actor_user_id=actor_id,
        )
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

    pending = get_add_member_pending(context, chat_id=int(update.effective_chat.id))
    if not pending:
        if update.message.reply_to_message is not None:
            logger.info(
                "payment_bind: add_member_no_pending chat_id=%s user_id=%s "
                "reply_to_message_id=%s pending_keys=%s",
                update.effective_chat.id,
                update.effective_user.id,
                update.message.reply_to_message.message_id,
                list(_pending_store(context).keys()),
            )
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

    actor_id = int(update.effective_user.id)
    logger.info(
        "payment_bind: add_member_reply method=%s payment_id=%s actor_user_id=%s "
        "prompt_actor_user_id=%s title=%r",
        pending.get("method_slug"),
        pending.get("payment_id"),
        actor_id,
        pending.get("actor_user_id"),
        title,
    )

    method_slug = pending["method_slug"]
    payment_id = int(pending["payment_id"])
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        clear_add_member_pending(context, chat_id=int(update.effective_chat.id))
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

    clear_add_member_pending(context, chat_id=int(update.effective_chat.id))
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
