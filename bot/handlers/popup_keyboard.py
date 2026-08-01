"""Group handlers for player popup reply keyboard.

Prefer ``bot.handlers.group_activity`` — this module re-exports the unified handler.
"""

from __future__ import annotations

from bot.handlers.group_activity import (
    get_group_activity_handler,
    get_popup_keyboard_activity_handler,
    group_activity_handler as popup_keyboard_activity_handler,
)

__all__ = [
    "get_group_activity_handler",
    "get_popup_keyboard_activity_handler",
    "popup_keyboard_activity_handler",
]
