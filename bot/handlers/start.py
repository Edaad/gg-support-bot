from telegram import Update
from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker


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
    if is_test_bot_worker():
        lines.extend(
            [
                "",
                "Test bot (staff):",
                "• /unbindmethod [venmo] — Clear payment-method link for this group",
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
