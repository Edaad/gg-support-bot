"""Patch network/DB side effects from payment ingest and group notify tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

SCHEDULE_AUTO_DEPOSIT = (
    "bot.services.payment_auto_deposit.schedule_auto_deposit_from_payment"
)
MAYBE_WARN_MULTI_PAYER = (
    "bot.services.payment_multi_payer_warning.maybe_warn_multi_payer"
)
CLEAR_DEPOSIT_CHASE = (
    "bot.services.escalation_notification.clear_deposit_chase_after_payment"
)
CANCEL_DEPOSIT_REMINDER = "bot.handlers.deposit.cancel_deposit_reminder_for_chat"
ON_PAYMENT_WINDOW_CLOSED = (
    "bot.services.popup_keyboard.on_payment_window_closed"
)
NOTIFY_PLAYER_GROUP = (
    "bot.services.payment_group_notify.notify_player_group_payment_received"
)


def start_payment_ingest_mocks() -> list[patch]:
    """Start ingest side-effect patches; stop with stop_patchers() in tearDown."""
    patchers: list[patch] = []
    for target, mock in (
        (SCHEDULE_AUTO_DEPOSIT, MagicMock()),
        (MAYBE_WARN_MULTI_PAYER, AsyncMock()),
        (NOTIFY_PLAYER_GROUP, AsyncMock(return_value=True)),
    ):
        p = patch(target, mock)
        p.start()
        patchers.append(p)
    return patchers


def start_payment_notify_mocks() -> list[patch]:
    """Start patches for post-send cleanup in notify_player_group_payment_received."""
    patchers: list[patch] = []
    for target, kwargs in (
        (CLEAR_DEPOSIT_CHASE, {"new_callable": AsyncMock}),
        (CANCEL_DEPOSIT_REMINDER, {}),
        (ON_PAYMENT_WINDOW_CLOSED, {}),
    ):
        p = patch(target, **kwargs)
        p.start()
        patchers.append(p)
    return patchers


def stop_patchers(patchers: list[patch]) -> None:
    for p in reversed(patchers):
        p.stop()
