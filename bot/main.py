"""Telegram bot entry point — registers all handlers and starts polling."""

import logging
import os
import sys
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


def _configure_worker_logging() -> None:
    """Send application ``logging`` to stderr so dynos (e.g. Heroku) show ``INFO`` lines.

    Honors ``LOG_LEVEL`` (default ``INFO``). Skips installing a duplicate handler when the root
    logger is already configured (e.g. tests, embedded runs).
    """

    root = logging.getLogger()
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)

    if root.handlers:
        # Something else configured handlers; still widen level so INFO propagates from app loggers.
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


async def _post_init_dm_gc_listener(app, *, test_mode: bool = False):
    from bot.services.mtproto_track_contact import set_contact_save_notify_bot

    set_contact_save_notify_bot(app.bot)

    if test_mode:
        logging.getLogger(__name__).info(
            "Test bot: MTProto dm_gc listener disabled (use production worker for /gc auto-DM)."
        )
        return

    from club_gc_settings import is_dm_gc_listener_enabled

    if is_dm_gc_listener_enabled():
        from bot.services.mtproto_dm_gc_listener import start_listener_background

        start_listener_background(app.bot.token)


async def _post_shutdown_dm_gc_listener(app, *, test_mode: bool = False):
    if test_mode:
        return

    from club_gc_settings import is_dm_gc_listener_enabled

    if is_dm_gc_listener_enabled():
        from bot.services.mtproto_dm_gc_listener import stop_listener_background

        stop_listener_background()


def run_bot(token: str | None = None, *, test_mode: bool = False):
    from bot.runtime_config import resolve_test_bot_token, use_payment_v2

    if test_mode:
        token = token or resolve_test_bot_token()
        if not token:
            raise SystemExit(
                "Test bot: set TELEGRAM_TEST_BOT_TOKEN (or TEST_BOT_TOKEN) in .env"
            )
    else:
        token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise SystemExit("Provide a token via TELEGRAM_BOT_TOKEN env var")

    _configure_worker_logging()

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
    from bot.handlers.flow_cancel import flow_cancel_handler
    from bot.handlers.list_cmd import list_handler
    from bot.handlers.groups import (
        on_my_chat_member_updated,
        on_new_chat_members,
        on_other_chat_member_join,
        auto_link_group,
    )
    from bot.handlers.bypass import bypass_handler, bypass_permanent_handler
    from bot.handlers.add import add_handler
    from bot.handlers.cash import cash_handler
    from bot.handlers.track import on_new_chat_title, track_handler, info_handler, override_handler
    from bot.handlers.telemsg import telemsg_handler
    from bot.handlers.lookup import lookup_handler
    from bot.handlers.findgc import findgc_handler
    from bot.handlers.refresh import refresh_handler
    from bot.handlers.checkplayer import checkplayer_handler
    from bot.handlers.group_create import get_gc_handler
    from bot.handlers.bonus import get_bonus_handler
    from bot.handlers.stripe import stripe_handler
    from bot.handlers.stripe import stripe_handler
    from bot.handlers.teststripe import teststripe_handler

    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(False)
        .post_init(lambda app: _post_init_dm_gc_listener(app, test_mode=test_mode))
        .post_shutdown(lambda app: _post_shutdown_dm_gc_listener(app, test_mode=test_mode))
        .build()
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))
    app.add_handler(CommandHandler("mycmds", mycmds_handler))
    app.add_handler(CommandHandler("delete", delete_handler))
    app.add_handler(CommandHandler("bypass", bypass_handler))
    app.add_handler(CommandHandler("bypasspermanent", bypass_permanent_handler))
    app.add_handler(CommandHandler("add", add_handler))
    app.add_handler(CommandHandler("cash", cash_handler))
    app.add_handler(CommandHandler("track", track_handler))
    app.add_handler(CommandHandler("override", override_handler))
    app.add_handler(CommandHandler("info", info_handler))
    app.add_handler(CommandHandler("telemsg", telemsg_handler))
    app.add_handler(CommandHandler("lookup", lookup_handler))
    app.add_handler(CommandHandler("findgc", findgc_handler))
    app.add_handler(CommandHandler("refresh", refresh_handler))
    app.add_handler(CommandHandler("checkplayer", checkplayer_handler))
    app.add_handler(CommandHandler("stripe", stripe_handler))
    app.add_handler(CommandHandler("teststripe", teststripe_handler))

    from bot.handlers.unbind_method import unbindmethod_handler

    app.add_handler(CommandHandler("unbindmethod", unbindmethod_handler))

    if test_mode:
        from bot.handlers.deposit import deposit_amount_priority_handler

        app.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS & ~filters.COMMAND,
                deposit_amount_priority_handler,
                block=False,
            )
        )

    app.add_handler(get_set_handler())
    app.add_handler(get_deposit_handler())
    app.add_handler(get_cashout_handler())
    app.add_handler(CommandHandler("cancel", flow_cancel_handler))
    app.add_handler(get_gc_handler())
    app.add_handler(get_bonus_handler())

    app.add_handler(
        ChatMemberHandler(on_my_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    app.add_handler(
        ChatMemberHandler(on_other_chat_member_join, ChatMemberHandler.CHAT_MEMBER),
    )
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, on_new_chat_title))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            on_new_chat_members,
        )
    )
    app.add_handler(CommandHandler("list", list_handler))

    # Catch-all for custom commands (must be last among command handlers)
    app.add_handler(MessageHandler(filters.COMMAND, command_router))

    # Auto-link unlinked groups on any message (group=1 so it doesn't block other handlers)
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.ALL, auto_link_group),
        group=1,
    )

    # Cancel deposit follow-up reminders when the depositing customer responds
    from bot.handlers.deposit import cancel_deposit_reminder_on_customer_msg

    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL,
            cancel_deposit_reminder_on_customer_msg,
        ),
        group=2,
    )

    from bot.runtime_config import is_test_bot_worker, use_payment_v2

    if test_mode:
        print(
            "Test bot is running (BOT_USE_PAYMENT_V2=%s, BOT_TEST_WORKER=%s). Press Ctrl+C to stop."
            % ("on" if use_payment_v2() else "off", "on" if is_test_bot_worker() else "off")
        )
        print(
            "Tip: after /deposit, use Reply on the bot message to enter the amount "
            "(or disable privacy mode in @BotFather → /setprivacy → Disable)."
        )
    print("Staff: /unbindmethod in a group clears all payment-method bindings.")
        from bot.services.stripe_deposit import stripe_configured

        if not stripe_configured():
            print(
                "Warning: Stripe not configured — Apple Pay / card checkout will fail. "
                "Add STRIPE_TEST_SECRET_KEY=sk_test_... (or STRIPE_SECRET_KEY) to .env."
            )
    else:
        print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
