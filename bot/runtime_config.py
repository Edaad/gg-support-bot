"""Bot runtime flags (test mode)."""

from __future__ import annotations

import os


def resolve_test_bot_token() -> str | None:
    for key in ("TELEGRAM_TEST_BOT_TOKEN", "TEST_BOT_TOKEN"):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return None


def is_test_bot_worker() -> bool:
    """True when running via run_test_bot.py (local TestGGSupportBot worker)."""
    return os.getenv("BOT_TEST_WORKER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
