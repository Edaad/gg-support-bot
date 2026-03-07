"""Admin-only custom command management: /set, /mycmds, /delete, and the catch-all router."""

import re

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_USER_IDS
from bot.services.club import get_club_id_for_telegram_user, get_custom_command
from db.connection import get_db
from db.models import CustomCommand, Club

ALLOWED = set(ADMIN_USER_IDS)
CMD_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
RESERVED_CMDS = {
    "start", "help", "whoami", "set", "cancel", "delete",
    "mycmds", "deposit", "cashout", "list", "botwelcome",
}

SET_NAME, SET_MESSAGE = range(2)


def _is_admin(uid: int) -> bool:
    return not ALLOWED or uid in ALLOWED


# ── /set conversation ─────────────────────────────────────────────────────────

async def set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not _is_admin(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text(
        "Send the command name (without the /). Example: referral\n\nSend /cancel to abort."
    )
    return SET_NAME


async def set_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    name = (update.message.text or "").strip().lstrip("/")
    if not CMD_NAME_RE.match(name):
        await update.message.reply_text(
            "Invalid name. Use only letters, numbers, or underscores (max 32). Try again."
        )
        return SET_NAME
    if name in RESERVED_CMDS and name not in ("list", "botwelcome"):
        await update.message.reply_text(f"/{name} is reserved. Pick another name.")
        return SET_NAME

    context.user_data["pending_cmd_name"] = name
    await update.message.reply_text(
        f"Now send the message for /{name}.\n\n"
        "You can send:\n• Text (multi-line)\n• Photo with optional caption\n\nSend /cancel to abort."
    )
    return SET_MESSAGE


async def set_get_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    name = context.user_data.get("pending_cmd_name")
    if not name:
        return ConversationHandler.END

    uid = update.effective_user.id
    club_id = get_club_id_for_telegram_user(uid)
    if club_id is None:
        await update.message.reply_text("You need a club set up first. Ask the admin to create one.")
        context.user_data.pop("pending_cmd_name", None)
        return ConversationHandler.END

    with get_db() as session:
        existing = session.query(CustomCommand).filter_by(club_id=club_id, command_name=name).first()

        if update.message.photo:
            photo = update.message.photo[-1]
            if existing:
                existing.response_type = "photo"
                existing.response_file_id = photo.file_id
                existing.response_caption = update.message.caption or ""
                existing.response_text = None
            else:
                session.add(CustomCommand(
                    club_id=club_id, command_name=name,
                    response_type="photo", response_file_id=photo.file_id,
                    response_caption=update.message.caption or "",
                ))
            await update.message.reply_text(f"Saved /{name} (photo command).")
        elif update.message.text:
            if existing:
                existing.response_type = "text"
                existing.response_text = update.message.text
                existing.response_file_id = None
                existing.response_caption = None
            else:
                session.add(CustomCommand(
                    club_id=club_id, command_name=name,
                    response_type="text", response_text=update.message.text,
                ))
            await update.message.reply_text(f"Saved /{name}.")
        else:
            await update.message.reply_text("Please send text or a photo.")
            return SET_MESSAGE

    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


async def set_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Cancelled.")
    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


def get_set_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("set", set_entry)],
        states={
            SET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_get_name)],
            SET_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_get_message),
                MessageHandler(filters.PHOTO, set_get_message),
            ],
        },
        fallbacks=[CommandHandler("cancel", set_cancel)],
        name="set_command_conv",
        persistent=False,
    )


# ── /mycmds ───────────────────────────────────────────────────────────────────

async def mycmds_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    club_id = get_club_id_for_telegram_user(uid)
    if club_id is None:
        await update.message.reply_text("You don't have a club set up yet.")
        return
    with get_db() as session:
        cmds = session.query(CustomCommand).filter_by(club_id=club_id).all()
        if not cmds:
            await update.message.reply_text("No custom commands yet. Use /set to create one.")
            return
        lines = ["Your custom commands:"]
        for c in cmds:
            if c.response_type == "photo":
                lines.append(f"/{c.command_name} — [Photo]")
            else:
                preview = (c.response_text or "")[:60].split("\n")[0]
                lines.append(f"/{c.command_name} — {preview}")
        await update.message.reply_text("\n".join(lines))


# ── /delete ───────────────────────────────────────────────────────────────────

async def delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /delete <command_name>")
        return
    name = args[0].lstrip("/")
    club_id = get_club_id_for_telegram_user(uid)
    if club_id is None:
        await update.message.reply_text("You don't have a club set up yet.")
        return
    with get_db() as session:
        cmd = session.query(CustomCommand).filter_by(club_id=club_id, command_name=name).first()
        if cmd:
            session.delete(cmd)
            await update.message.reply_text(f"Deleted /{name}.")
        else:
            await update.message.reply_text(f"You don't have a /{name} command.")


# ── Catch-all command router ──────────────────────────────────────────────────

async def command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    text = update.message.text or ""
    cmd = text.split()[0].lstrip("/").split("@")[0]
    if cmd in RESERVED_CMDS:
        return

    chat = update.effective_chat
    uid = update.effective_user.id

    if chat.type in ("group", "supergroup"):
        from bot.services.club import get_club_for_chat
        club_id = get_club_for_chat(chat.id)
    else:
        club_id = get_club_id_for_telegram_user(uid)

    if club_id is None:
        return

    data = get_custom_command(club_id, cmd)
    if not data:
        if _is_admin(uid):
            await update.message.reply_text("Unknown command. Use /mycmds or /set.")
        return

    if data["response_type"] == "photo" and data.get("response_file_id"):
        await update.message.reply_photo(
            photo=data["response_file_id"],
            caption=data.get("response_caption") or None,
        )
    elif data.get("response_text"):
        chunk = 4096
        text = data["response_text"]
        for i in range(0, len(text), chunk):
            await update.message.reply_text(text[i : i + chunk])
