"""Telegram bot entry point — registers all handlers and starts polling."""

import os
import warnings

from telegram import Update
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
)

warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler.*", category=PTBUserWarning)

from db.connection import init_engine
from db.models import Base


def run_bot(token: str | None = None):
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Provide a token via TELEGRAM_BOT_TOKEN env var")

    engine = init_engine()
    Base.metadata.create_all(engine)

    from bot.handlers.start import start_handler, help_handler, whoami_handler
    from bot.handlers.commands import (
        get_set_handler,
        mycmds_handler,
        delete_handler,
        command_router,
    )
    from bot.handlers.deposit import get_deposit_handler
    from bot.handlers.cashout import get_cashout_handler
    from bot.handlers.list_cmd import list_handler
    from bot.handlers.groups import on_my_chat_member_updated, auto_link_group
    from bot.handlers.bypass import bypass_handler, bypass_permanent_handler
    from bot.handlers.track import on_new_chat_title, track_handler, info_handler

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))
    app.add_handler(CommandHandler("mycmds", mycmds_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("bypass", bypass_handler))
    app.add_handler(CommandHandler("bypasspermanent", bypass_permanent_handler))
    app.add_handler(CommandHandler("track", track_handler))
    app.add_handler(CommandHandler("info", info_handler))

    app.add_handler(get_set_handler())
    app.add_handler(get_deposit_handler())
    app.add_handler(get_cashout_handler())

    app.add_handler(
        ChatMemberHandler(on_my_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, on_new_chat_title))
    app.add_handler(CommandHandler("list", list_handler))

    # Catch-all for custom commands (must be last among command handlers)
    app.add_handler(MessageHandler(filters.COMMAND, command_router))

    # Auto-link unlinked groups on any message (group=1 so it doesn't block other handlers)
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.ALL, auto_link_group),
        group=1,
    )

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
