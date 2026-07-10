"""E2e auto-deposit: chip-add on non-ambiguous payment auto-bind when club toggle is on."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, ROUND_HALF_UP

from telegram import Bot

from bot.services.club import (
    get_auto_deposit_on_payment_enabled,
    get_group_title_for_chat,
    has_recent_add_command_in_chat,
    has_recent_deposit_command_in_chat,
    invalidate_pending_one_time_bypasses,
    record_activity_for_chat,
)
from bot.services.clubgg_deposit_api import (
    request_id_for_payment,
    run_auto_chip_add,
)
from bot.services.mtproto_group_add import (
    _send_add_confirmation_once,
    format_add_confirmation,
)
from bot.services.payment_auto_deposit_events import record_auto_deposit_event
from bot.services.deposit_funnel_events import (
    record_chips_credited_funnel,
    record_payment_funnel_from_ingest,
)
from bot.services.payment_group_notify import support_bot_tokens_to_try
from bot.services.player_details import gg_player_id_from_title, parse_group_title_parts
from club_gc_settings import get_club_gc_config_by_link_club_id

logger = logging.getLogger(__name__)

DEPOSIT_COMMAND_WINDOW_MINUTES = 10

CREATOR_STAFF_FOOTER_AUTO = (
    "<b>Auto-add:</b> Fully automatic — chips will load and the player will be "
    "notified. No action needed unless you receive an error alert; then /add manually."
)
CREATOR_STAFF_FOOTER_MANUAL = (
    "<b>Manual action required</b> — bind and /add as usual."
)
CREATOR_STAFF_FOOTER_NO_RECENT_DEPOSIT = (
    "<b>Manual action required</b> — did not see /deposit in this group in the "
    f"last {DEPOSIT_COMMAND_WINDOW_MINUTES} minutes. Bind and /add as usual."
)
CREATOR_STAFF_FOOTER_RECENT_ADD = (
    "<b>Auto-add skipped</b> — /add was used in this group in the "
    f"last {DEPOSIT_COMMAND_WINDOW_MINUTES} minutes. Chips may already have been "
    "added for this payment; verify before acting."
)


def auto_deposit_ineligible_reason(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
    group_title: str | None = None,
) -> str | None:
    """Return why e2e auto-deposit was skipped, or None when eligible."""
    if not auto_bound:
        return "auto_bound_false"
    if goods_or_services:
        return "venmo_goods_and_services"
    if club_id is None or telegram_chat_id is None:
        return "missing_club_or_chat"
    if not get_auto_deposit_on_payment_enabled(int(club_id)):
        return "auto_deposit_on_payment_disabled"
    if group_title is not None and not gg_player_id_from_title(group_title):
        return "no_player_id_in_title"
    if not has_recent_deposit_command_in_chat(
        int(telegram_chat_id),
        within_minutes=DEPOSIT_COMMAND_WINDOW_MINUTES,
    ):
        return "no_recent_deposit_command"
    if has_recent_add_command_in_chat(
        int(telegram_chat_id),
        within_minutes=DEPOSIT_COMMAND_WINDOW_MINUTES,
    ):
        return "recent_add_command"
    return None


def is_creator_club_auto_deposit_eligible(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
    group_title: str | None = None,
) -> bool:
    """True when payment will run full auto chip-add on ingest."""
    return (
        auto_deposit_ineligible_reason(
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            auto_bound=auto_bound,
            goods_or_services=goods_or_services,
            group_title=group_title,
        )
        is None
    )


def format_creator_club_staff_footer(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
    group_title: str | None = None,
) -> str | None:
    """Return staff footer when e2e auto-deposit is enabled for the club."""
    if club_id is None or not get_auto_deposit_on_payment_enabled(int(club_id)):
        return None
    reason = auto_deposit_ineligible_reason(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
        group_title=group_title,
    )
    if reason is None:
        return CREATOR_STAFF_FOOTER_AUTO
    if reason == "no_recent_deposit_command":
        return CREATOR_STAFF_FOOTER_NO_RECENT_DEPOSIT
    if reason == "recent_add_command":
        return CREATOR_STAFF_FOOTER_RECENT_ADD
    return CREATOR_STAFF_FOOTER_MANUAL


def append_creator_club_staff_footer(
    text: str,
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
    group_title: str | None = None,
) -> str:
    """Append e2e auto-deposit staff footer when applicable."""
    footer = format_creator_club_staff_footer(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
        group_title=group_title,
    )
    if not footer:
        return text
    return f"{text.rstrip()}\n\n{footer}"


def _amount_from_cents(amount_cents: int) -> Decimal:
    return (Decimal(int(amount_cents)) / Decimal(100)).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )


def _player_label_from_title(group_title: str | None) -> str | None:
    parsed = parse_group_title_parts(group_title)
    if not parsed or not parsed.tail:
        return None
    return parsed.tail.strip() or None


async def _send_add_confirmation(
    *,
    club_id: int,
    telegram_chat_id: int,
    amount: Decimal,
    group_title: str | None,
) -> None:
    """Post /add-style confirmation from club MTProto user, with bot fallback."""
    name = _player_label_from_title(group_title)
    text = format_add_confirmation(amount, bonus=None, name=name)

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg:
        try:
            await _send_add_confirmation_once(cfg, int(telegram_chat_id), text)
            logger.info(
                "payment_auto_deposit: MTProto confirmation sent chat_id=%s club_id=%s",
                telegram_chat_id,
                club_id,
            )
            return
        except Exception:
            logger.warning(
                "payment_auto_deposit: MTProto confirmation failed chat_id=%s; "
                "falling back to support bot",
                telegram_chat_id,
                exc_info=True,
            )

    tokens = support_bot_tokens_to_try(is_test=False)
    for token in tokens:
        try:
            bot = Bot(token=token)
            await bot.send_message(chat_id=int(telegram_chat_id), text=text)
            logger.info(
                "payment_auto_deposit: bot confirmation sent chat_id=%s club_id=%s",
                telegram_chat_id,
                club_id,
            )
            return
        except Exception:
            logger.warning(
                "payment_auto_deposit: bot send failed chat_id=%s",
                telegram_chat_id,
                exc_info=True,
            )
    logger.error(
        "payment_auto_deposit: all confirmation sends failed chat_id=%s club_id=%s",
        telegram_chat_id,
        club_id,
    )


def schedule_auto_deposit_from_payment(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    amount_cents: int,
    auto_bound: bool,
    payment_method_slug: str,
    payment_id: int,
    group_title: str | None = None,
    goods_or_services: bool = False,
    bind_attempt_id: int | None = None,
    stripe_checkout_session_id: str | None = None,
) -> None:
    """Schedule background auto-deposit if eligible (non-blocking for ingest)."""
    try:
        record_payment_funnel_from_ingest(
            telegram_chat_id=telegram_chat_id,
            club_id=club_id,
            amount_cents=amount_cents,
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            auto_bound=auto_bound,
            bind_attempt_id=bind_attempt_id,
            stripe_checkout_session_id=stripe_checkout_session_id,
        )
    except Exception:
        logger.debug(
            "payment_auto_deposit: funnel ingest record failed payment_id=%s",
            payment_id,
            exc_info=True,
        )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "payment_auto_deposit: no running loop; skipping payment_id=%s",
            payment_id,
        )
        return
    loop.create_task(
        maybe_auto_deposit_from_payment(
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            amount_cents=amount_cents,
            auto_bound=auto_bound,
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            group_title=group_title,
            goods_or_services=goods_or_services,
            bind_attempt_id=bind_attempt_id,
            stripe_checkout_session_id=stripe_checkout_session_id,
        ),
        name=f"payment-auto-deposit-{payment_method_slug}-{payment_id}",
    )


def _record_skip_event(
    *,
    payment_method_slug: str,
    payment_id: int,
    club_id: int | None,
    telegram_chat_id: int | None,
    amount_cents: int,
    auto_bound: bool,
    goods_or_services: bool,
    group_title: str | None,
    skip_reason: str,
) -> None:
    record_auto_deposit_event(
        payment_method_slug=payment_method_slug,
        payment_id=payment_id,
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        amount_cents=amount_cents,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
        group_title=group_title,
        status="skipped",
        skip_reason=skip_reason,
    )


async def maybe_auto_deposit_from_payment(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    amount_cents: int,
    auto_bound: bool,
    payment_method_slug: str,
    payment_id: int,
    group_title: str | None = None,
    goods_or_services: bool = False,
    bind_attempt_id: int | None = None,
    stripe_checkout_session_id: str | None = None,
) -> None:
    """Auto chip-add on auto-bound payment when club e2e toggle is on."""
    if club_id is not None and not get_auto_deposit_on_payment_enabled(int(club_id)):
        return

    title = (group_title or "").strip() or None

    pre_reason = auto_deposit_ineligible_reason(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
        group_title=title,
    )
    if pre_reason is not None:
        logger.info(
            "payment_auto_deposit: skipped method=%s payment_id=%s "
            "chat_id=%s club_id=%s auto_bound=%s reason=%s",
            payment_method_slug,
            payment_id,
            telegram_chat_id,
            club_id,
            auto_bound,
            pre_reason,
        )
        _record_skip_event(
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            amount_cents=amount_cents,
            auto_bound=auto_bound,
            goods_or_services=goods_or_services,
            group_title=title,
            skip_reason=pre_reason,
        )
        return

    if not title:
        title, _ = await asyncio.to_thread(
            get_group_title_for_chat, int(telegram_chat_id)
        )
        title = (title or "").strip() or None

        skip_reason = auto_deposit_ineligible_reason(
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            auto_bound=auto_bound,
            goods_or_services=goods_or_services,
            group_title=title,
        )
        if skip_reason is not None:
            logger.info(
                "payment_auto_deposit: skipped method=%s payment_id=%s "
                "chat_id=%s club_id=%s auto_bound=%s reason=%s",
                payment_method_slug,
                payment_id,
                telegram_chat_id,
                club_id,
                auto_bound,
                skip_reason,
            )
            _record_skip_event(
                payment_method_slug=payment_method_slug,
                payment_id=payment_id,
                club_id=club_id,
                telegram_chat_id=telegram_chat_id,
                amount_cents=amount_cents,
                auto_bound=auto_bound,
                goods_or_services=goods_or_services,
                group_title=title,
                skip_reason=skip_reason,
            )
            return

    amount = _amount_from_cents(amount_cents)
    request_id = request_id_for_payment(payment_method_slug, payment_id)

    logger.info(
        "payment_auto_deposit: starting method=%s payment_id=%s chat_id=%s "
        "club_id=%s amount=%s",
        payment_method_slug,
        payment_id,
        telegram_chat_id,
        club_id,
        amount,
    )

    try:
        ok, chip_status = await run_auto_chip_add(
            club_id=int(club_id),
            chat_id=int(telegram_chat_id),
            amount=amount,
            request_id=request_id,
            bonus=None,
            group_title=title,
            ptb_bot=None,
        )
    except Exception:
        logger.exception(
            "payment_auto_deposit: unexpected error payment_id=%s chat_id=%s",
            payment_id,
            telegram_chat_id,
        )
        record_auto_deposit_event(
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            amount_cents=amount_cents,
            auto_bound=auto_bound,
            goods_or_services=goods_or_services,
            group_title=title,
            status="failed",
            skip_reason="chip_add_failed",
            chip_add_status="unexpected_error",
        )
        return

    if not ok:
        logger.info(
            "payment_auto_deposit: chip-add failed or skipped payment_id=%s chat_id=%s",
            payment_id,
            telegram_chat_id,
        )
        record_auto_deposit_event(
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            club_id=club_id,
            telegram_chat_id=telegram_chat_id,
            amount_cents=amount_cents,
            auto_bound=auto_bound,
            goods_or_services=goods_or_services,
            group_title=title,
            status="failed",
            skip_reason="chip_add_failed",
            chip_add_status=chip_status,
        )
        return

    try:
        await asyncio.to_thread(
            record_activity_for_chat,
            int(club_id),
            int(telegram_chat_id),
            "deposit",
        )
        await asyncio.to_thread(
            invalidate_pending_one_time_bypasses,
            int(club_id),
            int(telegram_chat_id),
        )
    except Exception:
        logger.exception(
            "payment_auto_deposit: record_activity failed club_id=%s chat_id=%s",
            club_id,
            telegram_chat_id,
        )

    await _send_add_confirmation(
        club_id=int(club_id),
        telegram_chat_id=int(telegram_chat_id),
        amount=amount,
        group_title=title,
    )

    record_auto_deposit_event(
        payment_method_slug=payment_method_slug,
        payment_id=payment_id,
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        amount_cents=amount_cents,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
        group_title=title,
        status="succeeded",
        chip_add_status=chip_status,
    )
    try:
        record_chips_credited_funnel(
            telegram_chat_id=int(telegram_chat_id),
            club_id=club_id,
            amount_cents=amount_cents,
            payment_method_slug=payment_method_slug,
            payment_id=payment_id,
            chip_add_status=chip_status,
            path="e2e_auto_deposit",
            bind_attempt_id=bind_attempt_id,
            stripe_checkout_session_id=stripe_checkout_session_id,
        )
    except Exception:
        logger.debug(
            "payment_auto_deposit: funnel chips_credited failed payment_id=%s",
            payment_id,
            exc_info=True,
        )

    logger.info(
        "payment_auto_deposit: completed payment_id=%s chat_id=%s amount=%s",
        payment_id,
        telegram_chat_id,
        amount,
    )
