"""Shared helpers for the payment notification chat."""

from __future__ import annotations

import os

from notification.constants import PAYMENT_NOTIFICATION_CHAT_ID_ENV


def notification_chat_id() -> int | None:
    raw = (os.getenv(PAYMENT_NOTIFICATION_CHAT_ID_ENV) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
