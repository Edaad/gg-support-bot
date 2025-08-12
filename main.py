# telegram_preset_bot_per_user_settable.py
# Requires: python-telegram-bot >= 20
# Install:   pip install python-telegram-bot==20.*

import os
import re
import json
import argparse
from typing import Dict, Set, Tuple, Optional, List

from telegram import Update, BotCommand
from telegram import BotCommandScopeChat
from config import ADMIN_USER_IDS
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# test-deploy
# Admin user IDs are now loaded from config.py
# Edit config.py to add/remove admin users
ALLOWED_USER_IDS: Set[int] = set(ADMIN_USER_IDS)

# Where per-user commands are stored on disk
DATA_FILE = "user_commands.json"

# Reserved command names that the bot uses internally
RESERVED_CMDS = {"start", "help", "whoami", "set", "cancel", "delete", "mycmds"}

# ──────────────────────────────────────────────────────────────────────────────
# RUNTIME STATE

# In-memory cache of per-user commands; loaded from DATA_FILE on start
# Shape: { "<user_id>": { "command": {"type": "text|photo", "content": "message", "file_id": "..."}, ... }, ... }
USER_COMMANDS: Dict[str, Dict[str, dict]] = {}

SET_NAME, SET_MESSAGE = range(2)

CMD_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")  # Telegram command naming rules

# ──────────────────────────────────────────────────────────────────────────────
# UTILITIES


def is_allowed(uid: int) -> bool:
    return not ALLOWED_USER_IDS or uid in ALLOWED_USER_IDS


# Update the Telegram menu for a specific user to show their personal commands
async def update_user_commands_menu(bot, uid: int) -> None:
    try:
        user_cmds = get_user_dict(uid)
        # System commands
        commands = [
            BotCommand("start", "What I can do"),
            BotCommand("help", "What I can do"),
            BotCommand("set", "Create your own command"),
            BotCommand("mycmds", "List your commands"),
            BotCommand("delete", "Delete a command"),
            BotCommand("whoami", "Show your user ID"),
        ]
        # Add user's custom commands
        for cmd_name, cmd_data in sorted(user_cmds.items()):
            if isinstance(cmd_data, dict):
                cmd_type = cmd_data.get("type", "text")
                if cmd_type == "photo":
                    description = "📷 Photo command"
                else:
                    content = cmd_data.get("content", "")
                    description = (
                        (
                            content.splitlines()[0][:50] + "..."
                            if len(content) > 50
                            else content.splitlines()[0]
                        )
                        if content
                        else "Custom command"
                    )
            else:
                description = (
                    (
                        cmd_data.splitlines()[0][:50] + "..."
                        if len(cmd_data) > 50
                        else cmd_data.splitlines()[0]
                    )
                    if cmd_data
                    else "Custom command"
                )
            commands.append(BotCommand(cmd_name, description))
        scope = BotCommandScopeChat(chat_id=uid)
        await bot.set_my_commands(commands, scope=scope)
    except Exception as e:
        print(f"Failed to update commands menu for user {uid}: {e}")


async def update_group_commands_menu(bot, chat_id: int, user_id: int) -> None:
    """
    Sets the command menu for a group chat, using the commands of the user who created the group (or triggered the update).
    """
    try:
        user_cmds = get_user_dict(user_id)
        # System commands
        commands = [
            BotCommand("start", "What I can do"),
            BotCommand("help", "What I can do"),
            BotCommand("set", "Create your own command"),
            BotCommand("mycmds", "List your commands"),
            BotCommand("delete", "Delete a command"),
            BotCommand("whoami", "Show your user ID"),
        ]
        # Add user's custom commands
        for cmd_name, cmd_data in sorted(user_cmds.items()):
            commands.append(BotCommand(cmd_name, "Custom command"))
        scope = BotCommandScopeChat(chat_id=chat_id)
        await bot.set_my_commands(commands, scope=scope)
    except Exception as e:
        print(f"Failed to update group commands menu for chat {chat_id}: {e}")


def load_data() -> None:
    global USER_COMMANDS
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                USER_COMMANDS = json.load(f)
        except Exception:
            USER_COMMANDS = {}
    else:
        USER_COMMANDS = {}


def save_data() -> None:
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(USER_COMMANDS, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def get_user_dict(uid: int) -> Dict[str, dict]:
    return USER_COMMANDS.setdefault(str(uid), {})


async def reply_long(update: Update, text: str) -> None:
    """Telegram messages max ~4096 chars; split if needed."""
    if not update.message:
        return
    chunk_size = 4096
    for i in range(0, len(text), chunk_size):
        await update.message.reply_text(text[i : i + chunk_size])


def parse_command_name(raw: str) -> str:
    name = raw.strip()
    if name.startswith("/"):
        name = name[1:]
    return name


# ──────────────────────────────────────────────────────────────────────────────
# CORE HANDLERS


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "I store per-user commands.\n"
        "• /set — create a new command for yourself\n"
        "• /mycmds — list your commands\n"
        "• /delete <name> — remove one of your commands\n"
        "• /whoami — show your user ID\n\n"
        "After you add a command, just type /<name> to use it."
    )
    # If in a group chat, update group menu for this user
    if update.effective_chat and update.effective_chat.type in ["group", "supergroup"]:
        await update_group_commands_menu(
            context.bot, update.effective_chat.id, update.effective_user.id
        )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_handler(update, context)


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user:
        await update.message.reply_text(
            f"Your Telegram user ID: {update.effective_user.id}"
        )


async def mycmds_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not is_allowed(uid):
        return
    cmds = get_user_dict(uid)
    if not cmds:
        await update.message.reply_text(
            "You haven't added any commands yet. Use /set to create one."
        )
        return
    lines = ["Your commands:"]
    for name, cmd_data in sorted(cmds.items()):
        if isinstance(cmd_data, dict):
            cmd_type = cmd_data.get("type", "text")
            if cmd_type == "photo":
                lines.append(f"/{name} — [Photo with caption]")
            else:
                first_line = (cmd_data.get("content", "") or "").splitlines()[0][:60]
                lines.append(f"/{name} — {first_line}")
        else:
            # Legacy text format
            first_line = (cmd_data or "").splitlines()[0][:60]
            lines.append(f"/{name} — {first_line}")
    await update.message.reply_text("\n".join(lines))


async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not is_allowed(uid):
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /delete <command_name>")
        return

    name = parse_command_name(args[0])
    user_cmds = get_user_dict(uid)
    if name in user_cmds:
        del user_cmds[name]
        save_data()
        await update.message.reply_text(f"Deleted /{name}.")
        await update_user_commands_menu(context.bot, uid)
        # If in a group chat, update group menu for this user
        if update.effective_chat and update.effective_chat.type in [
            "group",
            "supergroup",
        ]:
            await update_group_commands_menu(context.bot, update.effective_chat.id, uid)
    else:
        await update.message.reply_text(f"You don't have a /{name} command.")


# ──────────────────────────────────────────────────────────────────────────────
# /set CONVERSATION


async def set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    uid = update.effective_user.id
    if not is_allowed(uid):
        return ConversationHandler.END

    await update.message.reply_text(
        "Okay! Send the command name (without the /). Example: referral\n\n"
        "Send /cancel to abort."
    )
    return SET_NAME


async def set_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    name = parse_command_name(update.message.text)
    if not CMD_NAME_RE.match(name):
        await update.message.reply_text(
            "Invalid command name. Use only letters, numbers, or underscores (max 32). Try again."
        )
        return SET_NAME
    if name in RESERVED_CMDS:
        await update.message.reply_text(f"/{name} is reserved. Pick another name.")
        return SET_NAME

    uid = update.effective_user.id
    user_cmds = get_user_dict(uid)
    # No hard block on duplicates; we will overwrite, but warn.
    exists = name in user_cmds
    context.user_data["pending_cmd_name"] = name

    if exists:
        await update.message.reply_text(
            f"/{name} already exists for you. Send the new message to overwrite it.\n\n"
            "You can send:\n• Text message (multi-line supported)\n• Photo with optional caption\n\nSend /cancel to abort."
        )
    else:
        await update.message.reply_text(
            f"Great. Now send the message for /{name}.\n\n"
            "You can send:\n• Text message (multi-line supported)\n• Photo with optional caption\n\nSend /cancel to abort."
        )
    return SET_MESSAGE


async def set_get_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    name = context.user_data.get("pending_cmd_name")
    if not name:
        return ConversationHandler.END

    uid = update.effective_user.id
    user_cmds = get_user_dict(uid)

    # Handle photo message
    if update.message.photo:
        # Save photo command
        photo = update.message.photo[-1]
        file_id = photo.file_id
        caption = update.message.caption or ""
        user_cmds[name] = {"type": "photo", "file_id": file_id, "caption": caption}
        save_data()
        await update.message.reply_text(f"Saved /{name} (photo command).")
        await update_user_commands_menu(context.bot, uid)
    elif update.message.text:
        # Save text command
        user_cmds[name] = update.message.text
        save_data()
        await update.message.reply_text(f"Saved /{name}.")
        await update_user_commands_menu(context.bot, uid)
    else:
        await update.message.reply_text("Please send text or a photo for your command.")

    # If in a group chat, update group menu for this user
    if update.effective_chat and update.effective_chat.type in ["group", "supergroup"]:
        await update_group_commands_menu(context.bot, update.effective_chat.id, uid)

    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


async def set_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Cancelled.")
    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# CATCH-ALL COMMAND ROUTER (per-user lookup)


async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles any command not caught by reserved handlers.
    Looks up a per-user preset and replies with it, if found.
    """
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not is_allowed(uid):
        return

    # Extract the command name from the message text.
    # Examples:
    #   "/referral" -> "referral"
    #   "/referral@YourBot arg1" -> "referral"
    text = update.message.text or ""
    cmd = text.split()[0]  # "/referral@YourBot"
    cmd = cmd[1:] if cmd.startswith("/") else cmd
    cmd = cmd.split("@", 1)[0]  # strip bot username

    # Ignore reserved commands here; they should have matched their own handlers already.
    if cmd in RESERVED_CMDS:
        return

    user_cmds = get_user_dict(uid)
    cmd_data = user_cmds.get(cmd)
    if cmd_data is None:
        await update.message.reply_text(
            "I don't know that command for you. Use /mycmds or /set."
        )
        return

    # Handle different command types
    if isinstance(cmd_data, dict):
        cmd_type = cmd_data.get("type", "text")
        if cmd_type == "photo":
            # Send photo with caption
            file_id = cmd_data.get("file_id")
            caption = cmd_data.get("caption", "")
            if file_id:
                await update.message.reply_photo(photo=file_id, caption=caption)
            else:
                await update.message.reply_text("Error: Photo data is corrupted.")
        else:
            # Send text message
            content = cmd_data.get("content", "")
            await reply_long(update, content)
    else:
        # Legacy text format - handle old commands
        await reply_long(update, cmd_data)


# ──────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP


async def post_init(app):
    # Clear global commands - we'll set per-user commands instead
    await app.bot.set_my_commands([])
    # Initialize command menus for existing users
    for user_id_str in USER_COMMANDS.keys():
        try:
            user_id = int(user_id_str)
            if is_allowed(user_id):
                await update_user_commands_menu(app.bot, user_id)
        except Exception as e:
            print(f"Failed to update menu for user {user_id_str}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Telegram per-user preset bot with /set"
    )
    parser.add_argument(
        "--token", help="Telegram bot API token (or set TELEGRAM_BOT_TOKEN env var)"
    )
    args = parser.parse_args()

    token = args.token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Error: provide a token with --token or TELEGRAM_BOT_TOKEN env var."
        )

    load_data()

    application = ApplicationBuilder().token(token).post_init(post_init).build()

    # Reserved command handlers
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("whoami", whoami_handler))
    application.add_handler(CommandHandler("mycmds", mycmds_handler))
    application.add_handler(CommandHandler("delete", delete_handler))

    # /set conversation
    set_conv = ConversationHandler(
        entry_points=[CommandHandler("set", set_entry)],
        states={
            SET_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), set_get_name)],
            SET_MESSAGE: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), set_get_message),
                MessageHandler(filters.PHOTO, set_get_message),
            ],
        },
        fallbacks=[CommandHandler("cancel", set_cancel)],
        name="set_command_conv",
        persistent=False,
    )
    application.add_handler(set_conv)

    # Catch-all router for any other command (must be added last)
    application.add_handler(MessageHandler(filters.COMMAND, command_router))

    print("Bot is running. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
