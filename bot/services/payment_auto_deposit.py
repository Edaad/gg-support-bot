"""Creator Club auto-deposit: chip-add on non-ambiguous payment auto-bind."""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, ROUND_HALF_UP

from telegram import Bot

from bot.services.club import (
    get_auto_deposit_on_payment_enabled,
    get_club_by_id,
    get_group_title_for_chat,
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
from bot.services.payment_group_notify import support_bot_tokens_to_try
from bot.services.player_details import parse_group_title_parts
from club_gc_settings import get_club_gc_config_by_link_club_id

logger = logging.getLogger(__name__)

CREATOR_CLUB_NAME = "Creator Club"

CREATOR_STAFF_FOOTER_AUTO = (
    "<b>Auto-add:</b> Fully automatic — chips will load and the player will be "
    "notified. No action needed unless you receive an error alert; then /add manually."
)
CREATOR_STAFF_FOOTER_MANUAL = (
    "<b>Manual action required</b> — bind and /add as usual."
)


def _is_creator_club(club_id: int) -> bool:
    club = get_club_by_id(int(club_id))
    return (club.name or "").strip() == CREATOR_CLUB_NAME if club else False


def is_creator_club_auto_deposit_eligible(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
) -> bool:
    """True when Creator Club payment will run full auto chip-add on ingest."""
    if not auto_bound or goods_or_services:
        return False
    if club_id is None or telegram_chat_id is None:
        return False
    if not _is_creator_club(int(club_id)):
        return False
    return get_auto_deposit_on_payment_enabled(int(club_id))


def format_creator_club_staff_footer(
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
) -> str | None:
    """Return staff footer for Creator Club notifications, or None if not Creator Club."""
    if club_id is None or not _is_creator_club(int(club_id)):
        return None
    if is_creator_club_auto_deposit_eligible(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
    ):
        return CREATOR_STAFF_FOOTER_AUTO
    return CREATOR_STAFF_FOOTER_MANUAL


def append_creator_club_staff_footer(
    text: str,
    *,
    club_id: int | None,
    telegram_chat_id: int | None,
    auto_bound: bool,
    goods_or_services: bool = False,
) -> str:
    """Append Creator Club staff footer when applicable."""
    footer = format_creator_club_staff_footer(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
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
) -> None:
    """Schedule background auto-deposit if eligible (non-blocking for ingest)."""
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
        ),
        name=f"payment-auto-deposit-{payment_method_slug}-{payment_id}",
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
) -> None:
    """Creator Club: auto chip-add on auto-bound payment; confirm on success."""
    if not is_creator_club_auto_deposit_eligible(
        club_id=club_id,
        telegram_chat_id=telegram_chat_id,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
    ):
        return

    title = (group_title or "").strip()
    if not title:
        title, _ = await asyncio.to_thread(
            get_group_title_for_chat, int(telegram_chat_id)
        )
        title = (title or "").strip() or None

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
        ok = await run_auto_chip_add(
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
        return

    if not ok:
        logger.info(
            "payment_auto_deposit: chip-add failed or skipped payment_id=%s chat_id=%s",
            payment_id,
            telegram_chat_id,
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

    logger.info(
        "payment_auto_deposit: completed payment_id=%s chat_id=%s amount=%s",
        payment_id,
        telegram_chat_id,
        amount,
    )
