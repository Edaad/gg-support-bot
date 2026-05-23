"""GGCashier Telegram bot entry point."""

import logging
import os
import sys
import warnings

from telegram import Update
from telegram.warnings import PTBUserWarning
from telegram.ext import ApplicationBuilder, CommandHandler

warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler.*", category=PTBUserWarning)

from db.connection import init_engine
from db.models import Base

logger = logging.getLogger(__name__)


def _configure_worker_logging() -> None:
    root = logging.getLogger()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    if root.handlers:
        root.setLevel(level)
        for h in root.handlers:
            try:
                h.setLevel(level)
            except Exception:
                pass
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


def run_cashier(token: str | None = None):
    token = token or os.getenv("TELEGRAM_CASHIER_BOT_TOKEN")
    if not token:
        raise SystemExit("Provide TELEGRAM_CASHIER_BOT_TOKEN env var")

    _configure_worker_logging()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    logger.info("GGCashier starting (LOG_LEVEL=%s)", level_name)

    engine = init_engine()
    Base.metadata.create_all(engine)

    from cashier.handlers.wizard import get_cashier_wizard_handler

    app = ApplicationBuilder().token(token).build()

    app.add_handler(get_cashier_wizard_handler())
    logger.info("GGCashier handlers registered")

    async def start_handler(update, context):
        if update.message:
            await update.message.reply_text(
                "GGCashier — staff cashout wizard.\n"
                "Commands:\n"
                "/cashout — start a cashout (paste group title)\n"
                "/cancel — cancel current wizard"
            )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", start_handler))

    logger.info("GGCashier polling started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
