"""Reply-to-bind handler for payment notifications (Cash App, PayPal, Venmo, Zelle, Crypto)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.cashapp_payments import bind_cashapp_payment_from_reply
from bot.services.paypal_payments import bind_paypal_payment_from_reply
from bot.services.crypto_payments import bind_crypto_payment_from_reply
from bot.services.payment_bind_candidates import (
    bind_scope_mismatch_error,
    candidates_for_payment,
)
from bot.services.payment_bind_logging import format_payment_row, log_reply_branch
from bot.services.venmo_payments import (
    bind_venmo_payment_from_reply,
    resolve_bound_group,
    resolve_display_group_title,
)
from bot.services.zelle_payments import bind_zelle_payment_from_reply
from db.connection import get_db
from notification.bind_actions import crypto_scope_error
from notification.bind_keyboards import reassign_or_add_markup, to_inline_keyboard
from notification.chat_id import telegram_chat_ids_match
from notification.constants import PAYMENT_NOTIFICATION_CHAT_ID_ENV
from notification.handlers._chat import notification_chat_id
from notification.payment_lookup import find_payment_by_notification

logger = logging.getLogger(__name__)

_BIND_REPLY_FUNCS = {
    "crypto": bind_crypto_payment_from_reply,
    "paypal": bind_paypal_payment_from_reply,
    "cashapp": bind_cashapp_payment_from_reply,
    "zelle": bind_zelle_payment_from_reply,
    "venmo": bind_venmo_payment_from_reply,
}


def _message_body(message) -> str:
    text = (getattr(message, "text", None) or "").strip()
    if text:
        return text
    return (getattr(message, "caption", None) or "").strip()


def _reply_targets_payment_notification(reply) -> bool:
    """Only bind when staff replied to an actual payment notification post."""
    return "Payment Notification" in _message_body(reply)


def _titles_match(bound_title: str | None, reply_title: str) -> bool:
    a = (bound_title or "").strip().lower()
    b = (reply_title or "").strip().lower()
    return bool(a) and a == b


async def payment_bind_reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    expected_chat = notification_chat_id()
    if expected_chat is None:
        logger.warning("payment bind: %s not set", PAYMENT_NOTIFICATION_CHAT_ID_ENV)
        return

    chat_id = int(update.effective_chat.id)
    if not telegram_chat_ids_match(chat_id, expected_chat):
        logger.debug(
            "payment bind: ignoring message chat_id=%s (expected %s)",
            chat_id,
            expected_chat,
        )
        return

    reply = update.message.reply_to_message
    if reply is None:
        return

    if not _reply_targets_payment_notification(reply):
        return

    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Send the group title as your reply text.")
        return

    ref = find_payment_by_notification(int(expected_chat), int(reply.message_id))
    if ref is None:
        logger.warning(
            "payment_bind: reply no_payment notification_message_id=%s",
            reply.message_id,
        )
        await update.message.reply_text("No payment found for this notification.")
        return

    user_id = int(update.effective_user.id)

    resolved = resolve_bound_group(title)
    if not resolved.ok or resolved.bound_group is None:
        await update.message.reply_text(resolved.error or "Could not resolve group.")
        return

    new_group = resolved.bound_group
    if ref.method_slug == "crypto":
        with get_db() as session:
            from db.models import CryptoPayment

            crypto_payment = (
                session.query(CryptoPayment)
                .filter_by(id=ref.payment_id)
                .one_or_none()
            )
            scope_err = (
                crypto_scope_error(ref.method_slug, crypto_payment, new_group.club_id)
                if crypto_payment is not None
                else None
            )
        if scope_err:
            await update.message.reply_text(scope_err)
            return

    bind_scope_err = bind_scope_mismatch_error(
        payment_is_test=ref.payment_is_test,
        group_title=new_group.group_title,
    )
    if bind_scope_err:
        await update.message.reply_text(bind_scope_err)
        return

    bound_chat_id = ref.telegram_chat_id

    with get_db() as session:
        from db.models import (
            CashAppPayment,
            CryptoPayment,
            PayPalPayment,
            VenmoPayment,
            ZellePayment,
        )

        model_map = {
            "venmo": VenmoPayment,
            "zelle": ZellePayment,
            "cashapp": CashAppPayment,
            "paypal": PayPalPayment,
            "crypto": CryptoPayment,
        }
        fresh = (
            session.query(model_map[ref.method_slug])
            .filter_by(id=ref.payment_id)
            .one_or_none()
        )
        if fresh is None:
            await update.message.reply_text("Payment not found.")
            return
        candidates = candidates_for_payment(
            session,
            fresh,
            ref.method_slug,
            filter_alert_scope=getattr(fresh, "alert_scope", None)
            if ref.method_slug == "crypto"
            else None,
        )
        candidate_count = len(candidates)
        bound_title = resolve_display_group_title(int(bound_chat_id)) if bound_chat_id else None
        if not bound_title:
            bound_title = getattr(fresh, "bound_group_title_at_bind", None)
        payment_row = format_payment_row(fresh)

    if bound_chat_id is None:
        if candidate_count > 1:
            log_reply_branch(
                method_slug=ref.method_slug,
                payment_id=ref.payment_id,
                branch="unbound_ambiguous_use_buttons",
                actor_telegram_user_id=user_id,
                reply_title=title,
                candidate_count=candidate_count,
                payment_row=payment_row,
            )
            await update.message.reply_text(
                "Multiple possible matches — use the buttons on the notification, "
                "or Add another member / Reset bindings."
            )
            return
        log_reply_branch(
            method_slug=ref.method_slug,
            payment_id=ref.payment_id,
            branch="unbound_immediate_bind",
            actor_telegram_user_id=user_id,
            reply_title=title,
            candidate_count=candidate_count,
            payment_row=payment_row,
        )
        await _immediate_bind(
            update=update,
            expected_chat=expected_chat,
            reply=reply,
            title=title,
            user_id=user_id,
            method_slug=ref.method_slug,
            payment_id=ref.payment_id,
        )
        return

    if _titles_match(bound_title, new_group.group_title) or int(bound_chat_id) == int(
        new_group.telegram_chat_id
    ):
        log_reply_branch(
            method_slug=ref.method_slug,
            payment_id=ref.payment_id,
            branch="already_bound_same_group",
            actor_telegram_user_id=user_id,
            reply_title=title,
            bound_chat_id=int(bound_chat_id),
            candidate_count=candidate_count,
            payment_row=payment_row,
        )
        await update.message.reply_text(
            f"Already bound to {bound_title or new_group.group_title}."
        )
        return

    current_label = bound_title or f"chat_id {bound_chat_id}"
    log_reply_branch(
        method_slug=ref.method_slug,
        payment_id=ref.payment_id,
        branch="bound_different_group_offer_reassign_add",
        actor_telegram_user_id=user_id,
        reply_title=title,
        bound_chat_id=int(bound_chat_id),
        candidate_count=candidate_count,
        payment_row=payment_row,
    )
    await update.message.reply_text(
        f"This payment is bound to {current_label}. "
        f"You replied with {new_group.group_title}.",
        reply_markup=to_inline_keyboard(
            reassign_or_add_markup(
                ref.method_slug,
                ref.payment_id,
                target_chat_id=new_group.telegram_chat_id,
                target_title=new_group.group_title,
                show_add=True,
            ),
        ),
    )


async def _immediate_bind(
    *,
    update: Update,
    expected_chat: int,
    reply,
    title: str,
    user_id: int,
    method_slug: str | None = None,
    payment_id: int | None = None,
) -> None:
    bind_kwargs = dict(
        notification_chat_id=expected_chat,
        notification_message_id=int(reply.message_id),
        group_title_input=title,
        bound_by_telegram_user_id=user_id,
    )

    try:
        result = await bind_crypto_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_paypal_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_cashapp_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_zelle_payment_from_reply(**bind_kwargs)
        if not result.ok:
            result = await bind_venmo_payment_from_reply(**bind_kwargs)
    except Exception:
        logger.exception(
            "payment bind failed reply_to=%s title=%r",
            reply.message_id,
            title,
        )
        await update.message.reply_text(
            "Bind failed due to a server error. Check notification dyno logs."
        )
        return

    if not result.ok or result.bound_group is None:
        await update.message.reply_text(result.error or "Could not bind payment.")
        return

    group = result.bound_group
    logger.info(
        "payment_bind: immediate_bind ok method=%s payment_id=%s group=%r chat_id=%s user_id=%s",
        method_slug,
        payment_id,
        group.group_title,
        group.telegram_chat_id,
        user_id,
    )
    await update.message.reply_text(
        f"Bound to {group.group_title} (chat_id {group.telegram_chat_id})"
    )


async def venmo_bind_reply_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Backward-compatible alias for payment_bind_reply_handler."""
    await payment_bind_reply_handler(update, context)
