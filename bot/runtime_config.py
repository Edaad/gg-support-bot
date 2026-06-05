"""Bot runtime flags (payment backend, test mode)."""

from __future__ import annotations

import os


def use_payment_v2() -> bool:
    """Return True unless BOT_USE_PAYMENT_V2 is explicitly disabled (0/false/no/off)."""
    raw = os.getenv("BOT_USE_PAYMENT_V2", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on")


def resolve_test_bot_token() -> str | None:
    for key in ("TELEGRAM_TEST_BOT_TOKEN", "TEST_BOT_TOKEN"):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return None


def is_test_bot_worker() -> bool:
    """True when running via run_test_bot.py (local TestGGSupportBot worker)."""
    return os.getenv("BOT_TEST_WORKER", "").strip().lower() in ("1", "true", "yes", "on")


def zelle_first_time_linking_enabled() -> bool:
    """Zelle first-time /deposit linking (setup flow) is test-bot-only for now."""
    return is_test_bot_worker()
