"""GG Notifications bot — payment notification bind replies."""

from __future__ import annotations

import logging
import os
import sys
import warnings

from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler.*", category=PTBUserWarning)

from db.connection import init_engine
from db.models import Base
from notification.constants import (
    NOTIFICATION_BOT_TOKEN_ENV,
    PAYMENT_NOTIFICATION_CHAT_ID_ENV,
)
from notification.handlers.bind import payment_bind_reply_handler
from notification.handlers.bind_callbacks import (
    payment_bind_add_member_reply_handler,
    payment_bind_callback_handler,
)
from notification.handlers.report import get_report_handler

logger = logging.getLogger(__name__)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("notification bot handler failed", exc_info=context.error)
    message = getattr(update, "effective_message", None) if update else None
    if message is not None:
        try:
            await message.reply_text(
                "Something went wrong processing that message. "
                "Check notification dyno logs."
            )
        except Exception:
            logger.exception("notification bot could not send error reply")


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

    app.add_handler(get_report_handler())
    app.add_handler(CallbackQueryHandler(payment_bind_callback_handler, pattern=r"^pb:"))
    app.add_handler(
        MessageHandler(
            filters.REPLY & filters.TEXT & ~filters.COMMAND,
            payment_bind_reply_handler,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            payment_bind_add_member_reply_handler,
        )
    )
    app.add_error_handler(_error_handler)

    logger.info(
        "Notification bot starting (payment bind replies + callbacks in chat_id=%s)",
        chat_raw,
    )
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    run_notification_bot()
