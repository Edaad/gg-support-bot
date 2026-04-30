#!/usr/bin/env python
"""Entry point for the Telegram bot worker process."""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from bot.main import run_bot

if __name__ == "__main__":
    run_bot()
