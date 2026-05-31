#!/usr/bin/env python
"""Entry point for the GG Notifications Telegram bot (payment bind replies)."""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from notification.main import run_notification_bot

if __name__ == "__main__":
    run_notification_bot()
