"""Player-facing payment confirmation messages in linked support group chats."""

from __future__ import annotations

import logging
import os
from decimal import ROUND_HALF_UP, Decimal

from telegram import Bot

from bot.runtime_config import resolve_test_bot_token

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


def _format_amount_dollars(amount_cents: int) -> str:
    dollars = int(
        (Decimal(amount_cents) / Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    return f"${dollars:,}"


def format_payment_received_message(amount_cents: int) -> str:
    """Whole-dollar confirmation text for the player's support group."""
    amount = _format_amount_dollars(amount_cents)
    return (
        f"We have received your payment for {amount}, "
        "chips will be loaded to your account shortly!!"
    )


def resolve_support_bot_token(*, is_test: bool = False) -> str:
    """Return the preferred support-bot token for the payment context."""
    tokens = support_bot_tokens_to_try(is_test=is_test)
    return tokens[0] if tokens else ""


def support_bot_tokens_to_try(*, is_test: bool = False) -> list[str]:
    """Return deduped support-bot tokens to try, primary first."""
    prod = (os.getenv(TELEGRAM_BOT_TOKEN_ENV) or "").strip()
    test = (resolve_test_bot_token() or "").strip()
    if is_test and not test:
        logger.warning(
            "payment_group_notify: is_test=True but TELEGRAM_TEST_BOT_TOKEN is not set; "
            "only %s will be tried",
            TELEGRAM_BOT_TOKEN_ENV,
        )
    order = (test, prod) if is_test else (prod, test)
    seen: set[str] = set()
    tokens: list[str] = []
    for token in order:
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


async def notify_player_group_payment_received(
    *,
    telegram_chat_id: int,
    amount_cents: int,
    is_test: bool = False,
) -> bool:
    """Post payment confirmation in the linked GC via the support bot."""
    tokens = support_bot_tokens_to_try(is_test=is_test)
    if not tokens:
        logger.warning(
            "payment_group_notify: no bot token; skipping chat_id=%s is_test=%s",
            telegram_chat_id,
            is_test,
        )
        return False

    text = format_payment_received_message(amount_cents)
    last_error: Exception | None = None
    for token in tokens:
        try:
            bot = Bot(token=token)
            await bot.send_message(chat_id=int(telegram_chat_id), text=text)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "payment_group_notify: send failed chat_id=%s amount_cents=%s "
                "is_test=%s token_suffix=%s",
                telegram_chat_id,
                amount_cents,
                is_test,
                token[-8:] if len(token) >= 8 else token,
            )
            continue

        logger.info(
            "payment_group_notify: sent chat_id=%s amount_cents=%s is_test=%s "
            "token_suffix=%s",
            telegram_chat_id,
            amount_cents,
            is_test,
            token[-8:] if len(token) >= 8 else token,
        )
        from bot.handlers.deposit import cancel_deposit_reminder_for_chat

        cancel_deposit_reminder_for_chat(int(telegram_chat_id))
        return True

    logger.exception(
        "payment_group_notify: all tokens failed chat_id=%s amount_cents=%s is_test=%s",
        telegram_chat_id,
        amount_cents,
        is_test,
        exc_info=last_error,
    )
    return False


async def maybe_notify_player_on_auto_bound(
    *,
    telegram_chat_id: int | None,
    amount_cents: int,
    auto_bound: bool,
    is_test: bool = False,
) -> None:
    """Notify the player's GC when ingest auto-bound the payment."""
    if not auto_bound or telegram_chat_id is None:
        return
    await notify_player_group_payment_received(
        telegram_chat_id=int(telegram_chat_id),
        amount_cents=amount_cents,
        is_test=is_test,
    )
