from telegram import Update
from telegram.ext import ContextTypes


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    await update.message.reply_text(
        "Welcome to GG Support Bot!\n\n"
        "In a group:\n"
        "• /deposit — Make a deposit\n"
        "• /cashout — Request a cashout\n"
        "• /list — View the club's list\n\n"
        "Admins:\n"
        "• /set — Create a custom command\n"
        "• /mycmds — List your custom commands\n"
        "• /delete <name> — Remove a custom command\n"
        "• /whoami — Show your user ID"
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_handler(update, context)


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        await update.message.reply_text(
            f"Your Telegram user ID: {update.effective_user.id}"
        )
