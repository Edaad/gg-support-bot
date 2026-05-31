"""GG Notifications bot — payment notification bind replies."""

from __future__ import annotations

import logging
import os
import sys
import warnings

from telegram.ext import ApplicationBuilder, MessageHandler, filters
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler.*", category=PTBUserWarning)

from db.connection import init_engine
from db.models import Base
from notification.constants import (
    NOTIFICATION_BOT_TOKEN_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_ENV,
)
from notification.handlers.bind import venmo_bind_reply_handler

logger = logging.getLogger(__name__)


def _configure_worker_logging() -> None:
    root = logging.getLogger()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    if root.handlers:
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


def run_notification_bot(token: str | None = None) -> None:
    token = token or (os.getenv(NOTIFICATION_BOT_TOKEN_ENV) or "").strip()
    if not token:
        raise SystemExit(f"Provide {NOTIFICATION_BOT_TOKEN_ENV} env var")

    chat_raw = (os.getenv(PAYMENT_NOTIFICATION_CHAT_ID_ENV) or "").strip()
    if not chat_raw:
        raise SystemExit(f"Provide {PAYMENT_NOTIFICATION_CHAT_ID_ENV} env var")

    _configure_worker_logging()
    engine = init_engine()
    Base.metadata.create_all(engine)

    app = (
        ApplicationBuilder()
        .token(token)
        .build()
    )

    app.add_handler(
        MessageHandler(
            filters.Chat(int(chat_raw)) & filters.REPLY & filters.TEXT & ~filters.COMMAND,
            venmo_bind_reply_handler,
        )
    )

    logger.info(
        "Notification bot starting (payment bind replies in chat_id=%s)",
        chat_raw,
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    run_notification_bot()
