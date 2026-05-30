#!/usr/bin/env python
"""Run the support bot with a separate test BotFather token (local / staging).

Uses club_payment_* (v2) config — same default as production (BOT_USE_PAYMENT_V2=1).
Does not start the MTProto dm_gc listener — safe to run beside production worker.

Set in .env:
  TELEGRAM_TEST_BOT_TOKEN=...   (or TEST_BOT_TOKEN)

Usage:
  python run_test_bot.py

In groups, BotFather privacy mode blocks plain text — after /deposit you must
Reply to the bot's amount prompt (or run /setprivacy → Disable for this bot).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

# Test bot reads v2 payment tables unless explicitly overridden in .env
os.environ.setdefault("BOT_USE_PAYMENT_V2", "1")
os.environ.setdefault("BOT_TEST_WORKER", "1")

from bot.main import run_bot

if __name__ == "__main__":
    run_bot(test_mode=True)
