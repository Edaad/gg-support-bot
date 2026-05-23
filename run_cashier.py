#!/usr/bin/env python
"""Entry point for the GGCashier Telegram bot worker process."""

from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from cashier.main import run_cashier

if __name__ == "__main__":
    run_cashier()
