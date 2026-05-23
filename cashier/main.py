"""GGCashier Telegram bot entry point."""

import logging
import os
import sys
import warnings

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)
from telegram.warnings import PTBUserWarning

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


async def _log_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Verbose-only: log every callback_query before other handlers run."""
    from cashier.debug_log import is_cashier_verbose, log_update

    if not is_cashier_verbose() or not update.callback_query:
        return
    log_update(update, "callback_received", level=logging.DEBUG)
    wizard = context.application.bot_data.get("cashier_wizard")
    if wizard:
        try:
            key = wizard._get_key(update)
            state = wizard._conversations.get(key)
            from cashier.debug_log import state_label

            logger.debug(
                "cashier_debug [pre_handler] conv_key=%s state=%s data=%r",
                key,
                state_label(state),
                update.callback_query.data,
            )
        except Exception:
            logger.debug("cashier_debug [pre_handler] could not read conv state")


async def _on_job_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from cashier.debug_log import log_conversation_state, log_update
    from cashier.handlers.wizard import (
        job_callback_entry,
        sync_wizard_state,
    )

    wizard = context.application.bot_data["cashier_wizard"]
    log_update(update, "handler_gc_job_continue")
    log_conversation_state(wizard, update, "before_gc_job_continue")

    try:
        result = await job_callback_entry(update, context)
        sync_wizard_state(wizard, update, result)
        log_conversation_state(wizard, update, "after_gc_job_continue", new_state=result)
        logger.info("gc_job handled new_state=%s", result)
    except Exception:
        logger.exception("gc_job handler failed")
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "Something went wrong. Try again.", show_alert=True
                )
            except Exception:
                pass


async def _on_job_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from cashier.debug_log import log_conversation_state, log_update
    from cashier.handlers.wizard import (
        job_cancel_from_notify,
        sync_wizard_state,
    )

    wizard = context.application.bot_data["cashier_wizard"]
    log_update(update, "handler_gc_job_cancel")
    log_conversation_state(wizard, update, "before_gc_job_cancel")

    try:
        result = await job_cancel_from_notify(update, context)
        sync_wizard_state(wizard, update, result)
        log_conversation_state(wizard, update, "after_gc_job_cancel", new_state=result)
        logger.info("gc_job_cancel handled new_state=%s", result)
    except Exception:
        logger.exception("gc_job_cancel handler failed")
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "Something went wrong. Try again.", show_alert=True
                )
            except Exception:
                pass


def run_cashier(token: str | None = None):
    token = token or os.getenv("TELEGRAM_CASHIER_BOT_TOKEN")
    if not token:
        raise SystemExit("Provide TELEGRAM_CASHIER_BOT_TOKEN env var")

    _configure_worker_logging()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    from cashier.debug_log import is_cashier_verbose

    verbose = is_cashier_verbose()
    logger.info(
        "GGCashier starting LOG_LEVEL=%s CASHIER_VERBOSE_LOGS=%s token_set=%s",
        level_name,
        verbose,
        bool(token),
    )

    engine = init_engine()
    Base.metadata.create_all(engine)

    from cashier.handlers.wizard import get_cashier_wizard_handler

    wizard_handler = get_cashier_wizard_handler()

    app = ApplicationBuilder().token(token).build()
    app.bot_data["cashier_wizard"] = wizard_handler

    if verbose:
        app.add_handler(
            TypeHandler(Update, _log_all_callbacks), group=-1
        )
        logger.info("GGCashier verbose callback logging enabled (group=-1)")

    # Notify DM buttons — registered before ConversationHandler so they always fire.
    app.add_handler(CallbackQueryHandler(_on_job_continue, pattern=r"^gc_job:\d+$"))
    app.add_handler(
        CallbackQueryHandler(_on_job_cancel, pattern=r"^gc_job_cancel:\d+$")
    )
    app.add_handler(wizard_handler)
    logger.info(
        "GGCashier handlers: gc_job, gc_job_cancel, cashier_wizard (name=%s)",
        wizard_handler.name,
    )

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

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception(
            "GGCashier unhandled error update=%s",
            update,
            exc_info=context.error,
        )

    app.add_error_handler(on_error)

    logger.info("GGCashier polling started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
