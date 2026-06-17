"""Execute confirmed payment bind actions and refresh notifications."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from bot.services.group_chat_invite_links import resolve_group_chat_url_for_payment
from bot.services.payment_bind_candidates import (
    CandidateGroup,
    bind_scope_mismatch_error,
    candidates_for_payment,
    identity_kwargs_for_payment,
    method_handle_for_payment,
    reset_all_candidates,
    upsert_candidate_on_bind,
)
from bot.services.payment_binding_events import (
    record_payment_bound,
    sync_payment_notification_edit,
)
from bot.services.payment_method_binding import BOUND_VIA_MANUAL_NOTIFICATION
from bot.services.venmo_payments import (
    BoundGroup,
    resolve_bound_group,
    resolve_display_group_title,
)
from db.connection import get_db
from db.models import (
    CashAppPayment,
    CryptoPayment,
    PayPalPayment,
    VenmoPayment,
    ZellePayment,
)
from notification.bind_keyboards import candidate_picker_markup, empty_markup
from notification.payment_bind_helpers import format_payment_notification
from bot.services.payment_bind_logging import (
    format_payment_row,
    log_notification_edit,
)

logger = logging.getLogger(__name__)

BOUND_VIA_MANUAL_NOTIFICATION_CONFIRMED = "manual_notification_confirmed"

_PAYMENT_MODELS = {
    "venmo": VenmoPayment,
    "zelle": ZellePayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "crypto": CryptoPayment,
}

_BIND_BY_ID: dict[str, Callable[..., object]] = {}


def _register_binders() -> None:
    if _BIND_BY_ID:
        return
    from bot.services.cashapp_payments import bind_cashapp_payment_by_id
    from bot.services.crypto_payments import bind_crypto_payment_by_id
    from bot.services.paypal_payments import bind_paypal_payment_by_id
    from bot.services.venmo_payments import bind_venmo_payment_by_id
    from bot.services.zelle_payments import bind_zelle_payment_by_id

    _BIND_BY_ID.update(
        {
            "venmo": bind_venmo_payment_by_id,
            "zelle": bind_zelle_payment_by_id,
            "cashapp": bind_cashapp_payment_by_id,
            "paypal": bind_paypal_payment_by_id,
            "crypto": bind_crypto_payment_by_id,
        }
    )


def load_payment(method_slug: str, payment_id: int):
    model = _PAYMENT_MODELS.get(method_slug)
    if model is None:
        return None
    with get_db() as session:
        payment = session.query(model).filter_by(id=int(payment_id)).one_or_none()
        if payment is None:
            return None
        session.expunge(payment)
        return payment


def crypto_scope_error(method_slug: str, payment: object, club_id: int) -> str | None:
    if method_slug != "crypto":
        return None
    from bot.services.crypto_payments import validate_bind_alert_scope

    result = validate_bind_alert_scope(payment, bound_club_id=int(club_id))
    if result is None:
        return None
    return result.error


async def refresh_unbound_notification(
    *,
    method_slug: str,
    payment: object,
    notification_chat_id: int,
    notification_message_id: int,
    candidates: list[CandidateGroup],
) -> None:
    group_chat_url = await resolve_group_chat_url_for_payment(payment, group_title=None)
    text = format_payment_notification(
        method_slug,
        payment,
        group_chat_url=group_chat_url,
        ambiguous_candidates=candidates if len(candidates) > 1 else None,
    )
    markup = (
        candidate_picker_markup(method_slug, int(payment.id), candidates)
        if len(candidates) > 1
        else None
    )
    await sync_payment_notification_edit(
        payment_method_slug=method_slug,
        payment_id=int(payment.id),
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        text=text,
        reply_markup=markup or empty_markup(),
    )
    log_notification_edit(
        method_slug=method_slug,
        payment_id=int(payment.id),
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        has_keyboard=markup is not None,
        keyboard_kind="ambiguous_picker" if len(candidates) > 1 else None,
        reason="refresh_unbound",
    )


async def refresh_bound_notification(
    *,
    method_slug: str,
    payment_id: int,
    group_title: str,
    notification_chat_id: int,
    notification_message_id: int,
    bound_via: str = BOUND_VIA_MANUAL_NOTIFICATION_CONFIRMED,
    actor_telegram_user_id: int | None = None,
) -> bool:
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        return False
    group_chat_url = await resolve_group_chat_url_for_payment(
        payment,
        group_title=group_title,
    )
    text = format_payment_notification(
        method_slug,
        payment,
        group_title=group_title,
        group_chat_url=group_chat_url,
    )
    return await sync_payment_notification_edit(
        payment_method_slug=method_slug,
        payment_id=payment_id,
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        text=text,
        reply_markup=empty_markup(),
        bound_via=bound_via,
        actor_telegram_user_id=actor_telegram_user_id,
        telegram_chat_id=getattr(payment, "telegram_chat_id", None),
        club_id=getattr(payment, "club_id", None),
        bound_group_title=group_title,
    )


async def confirm_bind_payment(
    *,
    method_slug: str,
    payment_id: int,
    target_chat_id: int,
    actor_telegram_user_id: int,
) -> tuple[bool, str | None]:
    _register_binders()
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        return False, "Payment not found."

    logger.info(
        "payment_bind: confirm_bind start method=%s payment_id=%s target_chat_id=%s "
        "actor_user_id=%s payment=%s",
        method_slug,
        payment_id,
        target_chat_id,
        actor_telegram_user_id,
        format_payment_row(payment),
    )

    notif_chat_id = getattr(payment, "notification_chat_id", None)
    notif_msg_id = getattr(payment, "notification_message_id", None)
    if not notif_chat_id or not notif_msg_id:
        return False, "Notification message not found."

    live_title = resolve_display_group_title(int(target_chat_id))
    if not live_title:
        return False, "Group title not found for selected chat."

    bind_scope_err = bind_scope_mismatch_error(
        payment_is_test=bool(getattr(payment, "is_test", False)),
        group_title=live_title,
    )
    if bind_scope_err:
        return False, bind_scope_err

    scope_err = crypto_scope_error(method_slug, payment, _club_id_for_chat(int(target_chat_id)))
    if scope_err:
        logger.warning(
            "payment_bind: confirm_bind scope_rejected method=%s payment_id=%s error=%r",
            method_slug,
            payment_id,
            scope_err,
        )
        return False, scope_err

    if payment.telegram_chat_id is not None and int(payment.telegram_chat_id) == int(target_chat_id):
        logger.info(
            "payment_bind: confirm_bind noop_already_bound method=%s payment_id=%s chat_id=%s",
            method_slug,
            payment_id,
            target_chat_id,
        )
        await refresh_bound_notification(
            method_slug=method_slug,
            payment_id=payment_id,
            group_title=live_title,
            notification_chat_id=int(notif_chat_id),
            notification_message_id=int(notif_msg_id),
            actor_telegram_user_id=actor_telegram_user_id,
        )
        return True, None

    binder = _BIND_BY_ID[method_slug]
    result = await binder(
        payment_id=payment_id,
        group_title_input=live_title,
        bound_by_telegram_user_id=actor_telegram_user_id,
        bound_via=BOUND_VIA_MANUAL_NOTIFICATION_CONFIRMED,
    )
    if not result.ok:
        logger.warning(
            "payment_bind: confirm_bind failed method=%s payment_id=%s error=%r",
            method_slug,
            payment_id,
            result.error,
        )
        return False, result.error
    refreshed = load_payment(method_slug, payment_id)
    logger.info(
        "payment_bind: confirm_bind ok method=%s payment_id=%s target_chat_id=%s "
        "payment_after=%s",
        method_slug,
        payment_id,
        target_chat_id,
        format_payment_row(refreshed) if refreshed else None,
    )
    return True, None


def _club_id_for_chat(chat_id: int) -> int:
    from bot.services.club import get_group_title_for_chat

    _title, club_id = get_group_title_for_chat(int(chat_id))
    if club_id is None:
        raise ValueError("club_id not found for chat")
    return int(club_id)


async def confirm_add_candidate(
    *,
    method_slug: str,
    payment_id: int,
    target_chat_id: int,
    actor_telegram_user_id: int,
) -> tuple[bool, str | None]:
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        return False, "Payment not found."

    logger.info(
        "payment_bind: confirm_add_candidate start method=%s payment_id=%s "
        "target_chat_id=%s actor_user_id=%s payment=%s",
        method_slug,
        payment_id,
        target_chat_id,
        actor_telegram_user_id,
        format_payment_row(payment),
    )
    resolved = resolve_bound_group(resolve_display_group_title(int(target_chat_id)) or "")
    if not resolved.ok or resolved.bound_group is None:
        live_title = resolve_display_group_title(int(target_chat_id))
        if not live_title:
            return False, "Could not resolve group."
        resolved = resolve_bound_group(live_title)
    if not resolved.ok or resolved.bound_group is None:
        return False, resolved.error

    group = resolved.bound_group
    if int(group.telegram_chat_id) != int(target_chat_id):
        return False, "Group chat mismatch."

    bind_scope_err = bind_scope_mismatch_error(
        payment_is_test=bool(getattr(payment, "is_test", False)),
        group_title=group.group_title,
    )
    if bind_scope_err:
        return False, bind_scope_err

    scope_err = crypto_scope_error(method_slug, payment, group.club_id)
    if scope_err:
        return False, scope_err

    with get_db() as session:
        upsert_candidate_on_bind(
            session,
            method_slug,
            payer_name=getattr(payment, "payer_name", None),
            method_handle=method_handle_for_payment(payment, method_slug),
            from_address=getattr(payment, "from_address", None),
            alert_scope=getattr(payment, "alert_scope", None),
            telegram_chat_id=group.telegram_chat_id,
            club_id=group.club_id,
            bound_group_title_at_bind=group.group_title,
            bound_by_telegram_user_id=actor_telegram_user_id,
        )

    notif_chat_id = getattr(payment, "notification_chat_id", None)
    notif_msg_id = getattr(payment, "notification_message_id", None)
    if notif_chat_id and notif_msg_id:
        with get_db() as session:
            fresh = session.query(_PAYMENT_MODELS[method_slug]).filter_by(id=payment_id).one()
            candidates = candidates_for_payment(
                session,
                fresh,
                method_slug,
                filter_alert_scope=getattr(fresh, "alert_scope", None)
                if method_slug == "crypto"
                else None,
            )
            if len(candidates) > 1 and fresh.telegram_chat_id is None:
                await refresh_unbound_notification(
                    method_slug=method_slug,
                    payment=fresh,
                    notification_chat_id=int(notif_chat_id),
                    notification_message_id=int(notif_msg_id),
                    candidates=candidates,
                )
    logger.info(
        "payment_bind: confirm_add_candidate ok method=%s payment_id=%s target_chat_id=%s "
        "payment_still_unbound=%s",
        method_slug,
        payment_id,
        target_chat_id,
        getattr(payment, "telegram_chat_id", None) is None,
    )
    return True, None


async def confirm_reset_candidates(
    *,
    method_slug: str,
    payment_id: int,
    actor_telegram_user_id: int,
) -> tuple[bool, str | None]:
    payment = load_payment(method_slug, payment_id)
    if payment is None:
        return False, "Payment not found."

    kwargs = identity_kwargs_for_payment(payment, method_slug)
    logger.info(
        "payment_bind: confirm_reset start method=%s payment_id=%s identity=%s "
        "actor_user_id=%s payment=%s",
        method_slug,
        payment_id,
        kwargs,
        actor_telegram_user_id,
        format_payment_row(payment),
    )
    deleted = 0
    with get_db() as session:
        deleted = reset_all_candidates(session, method_slug, **kwargs)

    notif_chat_id = getattr(payment, "notification_chat_id", None)
    notif_msg_id = getattr(payment, "notification_message_id", None)
    if notif_chat_id and notif_msg_id:
        group_chat_url = await resolve_group_chat_url_for_payment(payment, group_title=None)
        text = format_payment_notification(
            method_slug,
            payment,
            group_chat_url=group_chat_url,
        )
        await sync_payment_notification_edit(
            payment_method_slug=method_slug,
            payment_id=payment_id,
            notification_chat_id=int(notif_chat_id),
            notification_message_id=int(notif_msg_id),
            text=text,
            reply_markup=empty_markup(),
            actor_telegram_user_id=actor_telegram_user_id,
        )
        log_notification_edit(
            method_slug=method_slug,
            payment_id=payment_id,
            notification_chat_id=int(notif_chat_id),
            notification_message_id=int(notif_msg_id),
            has_keyboard=False,
            reason="reset_candidates",
        )
    logger.info(
        "payment_bind: confirm_reset ok method=%s payment_id=%s rows_deleted=%s",
        method_slug,
        payment_id,
        deleted,
    )
    return True, None


def bound_group_for_chat_id(chat_id: int) -> BoundGroup | None:
    live_title = resolve_display_group_title(int(chat_id))
    if not live_title:
        return None
    result = resolve_bound_group(live_title)
    if not result.ok or result.bound_group is None:
        return None
    return result.bound_group
