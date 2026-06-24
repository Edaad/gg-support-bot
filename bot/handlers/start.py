from telegram import Update
from telegram.ext import ContextTypes

from bot.runtime_config import use_payment_v2


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    lines = [
        "Welcome to GG Support Bot!\n",
        "In a group:",
        "• /deposit — Make a deposit",
        "• /stripe — Stripe checkout link (group chats only)",
        "• /cashout — Request a cashout",
        "• /list — View the club's list",
    ]
    if use_payment_v2():
        lines.extend(
            [
                "",
                "Staff:",
                "• /unbindmethod — Clear all payment-method links for this group",
            ]
        )
    lines.extend(
        [
            "",
            "Admins:",
            "• /set — Create a custom command",
            "• /mycmds — List your custom commands",
            "• /delete <name> — Remove a custom command",
            "• /whoami — Show your user ID",
            "• /whosnext — Next 10 GCs in migration recovery queue (DM)",
        ]
    )
    await update.message.reply_text("\n".join(lines))


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_handler(update, context)


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        await update.message.reply_text(
            f"Your Telegram user ID: {update.effective_user.id}"
        )


async def fileid_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with the file_id of a photo sent to the bot. Usage: send a photo, then /fileid as reply."""
    if not update.message:
        return

    photo = None
    target = update.message.reply_to_message or update.message
    if target.photo:
        photo = target.photo[-1]

    if not photo:
        await update.message.reply_text(
            "Send or forward a photo to this chat, then reply to it with /fileid."
        )
        return

    await update.message.reply_text(f"file_id:\n\n{photo.file_id}")


async def fileid_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When a photo is sent directly (no command), reply with its file_id."""
    if not update.message or not update.message.photo:
        return

    from bot.handlers.issue_reports import issue_report_awaiting_evidence

    if issue_report_awaiting_evidence(context):
        return

    photo = update.message.photo[-1]
    await update.message.reply_text(f"file_id:\n\n{photo.file_id}")
