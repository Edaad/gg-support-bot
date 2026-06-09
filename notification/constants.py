"""Shared env keys for the payment notification bot (all payment types)."""

import os

NOTIFICATION_BOT_TOKEN_ENV = "TELEGRAM_NOTIFICATION_BOT_TOKEN"
PAYMENT_NOTIFICATION_CHAT_ID_ENV = "PAYMENT_NOTIFICATION_CHAT_ID"
DEBUG_NOTIFICATION_ENV = "DEBUG_NOTIFICATION"
LINKED_GROUP_CHAT_HYPERLINKS_ENV = "LINKED_GROUP_CHAT_HYPERLINKS_ENABLED"


def debug_notification_enabled() -> bool:
    """True when DEBUG_NOTIFICATION is 1, true, yes, or on."""
    raw = (os.getenv(DEBUG_NOTIFICATION_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def linked_group_chat_hyperlinks_enabled() -> bool:
    """True when LINKED_GROUP_CHAT_HYPERLINKS_ENABLED is unset or truthy (default on)."""
    raw = (os.getenv(LINKED_GROUP_CHAT_HYPERLINKS_ENV) or "true").strip().lower()
    return raw in ("1", "true", "yes", "on")

