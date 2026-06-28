"""Telegram bot entry point — registers all handlers and starts polling."""

import logging
import os
import sys
import warnings
from types import SimpleNamespace

from telegram import Update
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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
    from bot.handlers.deposit import register_deposit_reminder_runtime
    from bot.services.mtproto_track_contact import set_contact_save_notify_bot

    register_deposit_reminder_runtime(app)
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

    if not test_mode:
        from club_gc_settings import is_migration_recovery_slack_summary_enabled
        from bot.services.migration_recovery import setup_migration_recovery_jobs

        setup_migration_recovery_jobs(app)

        if is_migration_recovery_slack_summary_enabled():
            from bot.services.migration_recovery import (
                schedule_migration_recovery_slack_summary_job,
            )

            schedule_migration_recovery_slack_summary_job(app)

        from bot.services.issue_report_reminders import schedule_issue_report_reminder_job

        schedule_issue_report_reminder_job(app)


async def _post_shutdown_dm_gc_listener(app, *, test_mode: bool = False):
    if test_mode:
        return

    from club_gc_settings import is_dm_gc_listener_enabled

    if is_dm_gc_listener_enabled():
        from bot.services.mtproto_dm_gc_listener import stop_listener_background

        stop_listener_background()


def import_worker_handlers(*, test_mode: bool = False) -> SimpleNamespace:
    """Import all handler modules used by ``run_bot``.

    Raises ``ImportError`` / ``ModuleNotFoundError`` if any handler module is missing.
    Used by deploy import smoke tests.
    """
    from bot.handlers.start import start_handler, help_handler, whoami_handler, fileid_handler, fileid_photo_handler
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
        on_chat_migrate_from,
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
    from bot.handlers.bonus import (
        bonus_callback_handler,
        bonus_entry,
        bonus_message_handler,
    )
    from bot.handlers.cashapp import cashapp_handler
    from bot.handlers.stripe import stripe_handler
    from bot.handlers.teststripe import teststripe_handler
    from bot.handlers.issue_reports import register_issue_report_handlers
    from bot.handlers.whosnext import whosnext_handler
    from bot.handlers.unbind_method import unbindmethod_handler
    from bot.handlers.deposit import (
        cancel_deposit_reminder_on_customer_msg,
        cancel_deposit_reminder_on_group_activity,
    )

    deposit_amount_priority_handler = None
    if test_mode:
        from bot.handlers.deposit import deposit_amount_priority_handler

    return SimpleNamespace(
        start_handler=start_handler,
        help_handler=help_handler,
        whoami_handler=whoami_handler,
        fileid_handler=fileid_handler,
        fileid_photo_handler=fileid_photo_handler,
        get_set_handler=get_set_handler,
        mycmds_handler=mycmds_handler,
        delete_handler=delete_handler,
        command_router=command_router,
        get_deposit_handler=get_deposit_handler,
        get_cashout_handler=get_cashout_handler,
        flow_cancel_handler=flow_cancel_handler,
        list_handler=list_handler,
        on_chat_migrate_from=on_chat_migrate_from,
        on_my_chat_member_updated=on_my_chat_member_updated,
        on_new_chat_members=on_new_chat_members,
        on_other_chat_member_join=on_other_chat_member_join,
        auto_link_group=auto_link_group,
        bypass_handler=bypass_handler,
        bypass_permanent_handler=bypass_permanent_handler,
        add_handler=add_handler,
        cash_handler=cash_handler,
        on_new_chat_title=on_new_chat_title,
        track_handler=track_handler,
        info_handler=info_handler,
        override_handler=override_handler,
        telemsg_handler=telemsg_handler,
        lookup_handler=lookup_handler,
        findgc_handler=findgc_handler,
        refresh_handler=refresh_handler,
        checkplayer_handler=checkplayer_handler,
        get_gc_handler=get_gc_handler,
        bonus_entry=bonus_entry,
        bonus_message_handler=bonus_message_handler,
        bonus_callback_handler=bonus_callback_handler,
        cashapp_handler=cashapp_handler,
        stripe_handler=stripe_handler,
        teststripe_handler=teststripe_handler,
        register_issue_report_handlers=register_issue_report_handlers,
        whosnext_handler=whosnext_handler,
        unbindmethod_handler=unbindmethod_handler,
        deposit_amount_priority_handler=deposit_amount_priority_handler,
        cancel_deposit_reminder_on_customer_msg=cancel_deposit_reminder_on_customer_msg,
        cancel_deposit_reminder_on_group_activity=cancel_deposit_reminder_on_group_activity,
    )


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

    h = import_worker_handlers(test_mode=test_mode)

    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(False)
        .post_init(lambda app: _post_init_dm_gc_listener(app, test_mode=test_mode))
        .post_shutdown(lambda app: _post_shutdown_dm_gc_listener(app, test_mode=test_mode))
        .build()
    )

    # /bonus — register first (group -1) so other flows cannot swallow follow-up messages
    app.add_handler(CommandHandler("bonus", h.bonus_entry), group=-1)
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            h.bonus_message_handler,
        ),
        group=-1,
    )
    app.add_handler(
        CallbackQueryHandler(h.bonus_callback_handler, pattern=r"^b(type:|club:)"),
        group=-1,
    )

    app.add_handler(CommandHandler("start", h.start_handler))
    app.add_handler(CommandHandler("help", h.help_handler))
    app.add_handler(CommandHandler("whoami", h.whoami_handler))
    app.add_handler(CommandHandler("fileid", h.fileid_handler))
    h.register_issue_report_handlers(app)
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            h.fileid_photo_handler,
            block=False,
        )
    )

    app.add_handler(
        CommandHandler("whosnext", h.whosnext_handler, filters=filters.ChatType.PRIVATE)
    )
    app.add_handler(CommandHandler("mycmds", h.mycmds_handler))
    app.add_handler(CommandHandler("delete", h.delete_handler))
    app.add_handler(CommandHandler("bypass", h.bypass_handler))
    app.add_handler(CommandHandler("bypasspermanent", h.bypass_permanent_handler))
    app.add_handler(CommandHandler("add", h.add_handler))
    app.add_handler(CommandHandler("cash", h.cash_handler))
    app.add_handler(CommandHandler("track", h.track_handler))
    app.add_handler(CommandHandler("override", h.override_handler))
    app.add_handler(CommandHandler("info", h.info_handler))
    app.add_handler(CommandHandler("telemsg", h.telemsg_handler))
    app.add_handler(CommandHandler("lookup", h.lookup_handler))
    app.add_handler(CommandHandler("findgc", h.findgc_handler))
    app.add_handler(CommandHandler("refresh", h.refresh_handler))
    app.add_handler(CommandHandler("checkplayer", h.checkplayer_handler))
    app.add_handler(CommandHandler("cashapp", h.cashapp_handler))
    app.add_handler(CommandHandler("stripe", h.stripe_handler))
    app.add_handler(CommandHandler("teststripe", h.teststripe_handler))

    app.add_handler(CommandHandler("unbindmethod", h.unbindmethod_handler))

    if test_mode and h.deposit_amount_priority_handler is not None:
        app.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS & ~filters.COMMAND,
                h.deposit_amount_priority_handler,
                block=False,
            )
        )

    app.add_handler(h.get_set_handler())
    app.add_handler(h.get_deposit_handler())
    app.add_handler(h.get_cashout_handler())
    app.add_handler(CommandHandler("cancel", h.flow_cancel_handler))
    app.add_handler(h.get_gc_handler())

    app.add_handler(MessageHandler(filters.StatusUpdate.MIGRATE, h.on_chat_migrate_from))
    app.add_handler(
        ChatMemberHandler(h.on_my_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    app.add_handler(
        ChatMemberHandler(h.on_other_chat_member_join, ChatMemberHandler.CHAT_MEMBER),
    )
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_TITLE, h.on_new_chat_title))
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            h.on_new_chat_members,
        )
    )
    app.add_handler(CommandHandler("list", h.list_handler))

    # Catch-all for custom commands (must be last among command handlers)
    app.add_handler(MessageHandler(filters.COMMAND, h.command_router))

    # Auto-link unlinked groups on any message (group=1 so it doesn't block other handlers)
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.ALL, h.auto_link_group),
        group=1,
    )

    # Cancel deposit follow-up reminders when the depositing customer responds
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL,
            h.cancel_deposit_reminder_on_customer_msg,
        ),
        group=2,
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL,
            h.cancel_deposit_reminder_on_group_activity,
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
        from bot.services.stripe_deposit import stripe_configured

        if not stripe_configured():
            print(
                "Warning: Stripe not configured — Apple Pay / card checkout will fail. "
                "Add STRIPE_TEST_SECRET_KEY=sk_test_... (or STRIPE_SECRET_KEY) to .env."
            )
    else:
        print("Bot is running. Press Ctrl+C to stop.")
    print("Staff: /unbindmethod in a group clears all payment-method bindings.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
