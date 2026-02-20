# telegram_preset_bot_per_user_settable.py
# Requires: python-telegram-bot >= 20
# Install:   pip install python-telegram-bot==20.*

import os
import warnings
import re
import json
import argparse
import psycopg2
from urllib.parse import urlparse
from typing import Dict, Set, Tuple, Optional, List

from telegram import (
    Update,
    BotCommand,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram import BotCommandScopeChat
from config import ADMIN_USER_IDS
from telegram.warnings import PTBUserWarning

warnings.filterwarnings(
    "ignore", message=r".*CallbackQueryHandler.*", category=PTBUserWarning
)

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    ChatMemberHandler,
)

# ──────────────────────────────────────────────────────────────────────────────
# DATABASE FUNCTIONS


def get_db_connection():
    """Get database connection from Heroku DATABASE_URL or fallback to local"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Parse Heroku DATABASE_URL
        url = urlparse(database_url)
        return psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
        )
    else:
        # Fallback to local database or create in-memory storage
        print("No DATABASE_URL found, using JSON file fallback")
        return None


def init_database():
    """Initialize the database tables"""
    try:
        conn = get_db_connection()
        if not conn:
            return  # Will use JSON fallback

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_commands (
                        user_id BIGINT NOT NULL,
                        command_name VARCHAR(32) NOT NULL,
                        command_type VARCHAR(10) DEFAULT 'text',
                        content TEXT,
                        file_id TEXT,
                        caption TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, command_name)
                    )
                """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS group_club (
                        chat_id BIGINT PRIMARY KEY,
                        club_user_id BIGINT NOT NULL
                    )
                """
                )
        conn.close()
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization failed: {e}")


def load_user_commands_from_db():
    """Load all user commands from database into USER_COMMANDS dict"""
    global USER_COMMANDS
    try:
        conn = get_db_connection()
        if not conn:
            # Fallback to JSON file
            load_data_from_file()
            return

        USER_COMMANDS = {}
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, command_name, command_type, content, file_id, caption FROM user_commands"
                )
                for row in cur.fetchall():
                    user_id, cmd_name, cmd_type, content, file_id, caption = row
                    user_id_str = str(user_id)

                    if user_id_str not in USER_COMMANDS:
                        USER_COMMANDS[user_id_str] = {}

                    if cmd_type == "photo":
                        USER_COMMANDS[user_id_str][cmd_name] = {
                            "type": "photo",
                            "file_id": file_id,
                            "caption": caption or "",
                        }
                    else:
                        USER_COMMANDS[user_id_str][cmd_name] = content
        conn.close()
        print(f"Loaded commands for {len(USER_COMMANDS)} users from database")
    except Exception as e:
        print(f"Failed to load from database: {e}")
        # Fallback to JSON file
        load_data_from_file()


def save_user_command_to_db(user_id: int, command_name: str, command_data):
    """Save a single user command to database"""
    try:
        conn = get_db_connection()
        if not conn:
            # Fallback to JSON file
            save_data_to_file()
            return

        with conn:
            with conn.cursor() as cur:
                if (
                    isinstance(command_data, dict)
                    and command_data.get("type") == "photo"
                ):
                    # Photo command
                    cur.execute(
                        """
                        INSERT INTO user_commands (user_id, command_name, command_type, file_id, caption)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, command_name) 
                        DO UPDATE SET command_type = %s, file_id = %s, caption = %s
                    """,
                        (
                            user_id,
                            command_name,
                            "photo",
                            command_data.get("file_id"),
                            command_data.get("caption", ""),
                            "photo",
                            command_data.get("file_id"),
                            command_data.get("caption", ""),
                        ),
                    )
                else:
                    # Text command
                    cur.execute(
                        """
                        INSERT INTO user_commands (user_id, command_name, command_type, content)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id, command_name) 
                        DO UPDATE SET command_type = %s, content = %s
                    """,
                        (
                            user_id,
                            command_name,
                            "text",
                            command_data,
                            "text",
                            command_data,
                        ),
                    )
        conn.close()
        print(f"Saved command /{command_name} for user {user_id}")
    except Exception as e:
        print(f"Failed to save to database: {e}")
        # Fallback to JSON file
        save_data_to_file()


def load_club_command_from_db(club_user_id: int, command_name: str):
    """Load a single club command from DB (for Heroku multi-dyno / restarts).
    Returns command data (str or dict) or None. Updates USER_COMMANDS if found."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT command_type, content, file_id, caption FROM user_commands "
                    "WHERE user_id = %s AND command_name = %s",
                    (club_user_id, command_name),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cmd_type, content, file_id, caption = row
                club_str = str(club_user_id)
                if club_str not in USER_COMMANDS:
                    USER_COMMANDS[club_str] = {}
                if cmd_type == "photo":
                    data = {
                        "type": "photo",
                        "file_id": file_id or "",
                        "caption": caption or "",
                    }
                else:
                    data = content or ""
                USER_COMMANDS[club_str][command_name] = data
                return data
    except Exception as e:
        print(f"[deposit] load_club_command_from_db failed: {e}")
        return None
    finally:
        conn.close()


def delete_user_command_from_db(user_id: int, command_name: str):
    """Delete a user command from database"""
    try:
        conn = get_db_connection()
        if not conn:
            # Fallback to JSON file
            save_data_to_file()
            return

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_commands WHERE user_id = %s AND command_name = %s",
                    (user_id, command_name),
                )
        conn.close()
        print(f"Deleted command /{command_name} for user {user_id}")
    except Exception as e:
        print(f"Failed to delete from database: {e}")
        # Fallback to JSON file
        save_data_to_file()


# ──────────────────────────────────────────────────────────────────────────────
# GROUP -> CLUB MAPPING (for deposit flow)


def load_group_club_from_db() -> None:
    """Load group -> club mapping from database into GROUP_TO_CLUB"""
    global GROUP_TO_CLUB
    try:
        conn = get_db_connection()
        if not conn:
            load_group_club_from_file()
            return

        GROUP_TO_CLUB = {}
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id, club_user_id FROM group_club")
                for row in cur.fetchall():
                    chat_id, club_user_id = row
                    GROUP_TO_CLUB[int(chat_id)] = int(club_user_id)
        conn.close()
        print(f"Loaded group_club mapping for {len(GROUP_TO_CLUB)} groups")
    except Exception as e:
        print(f"Failed to load group_club from database: {e}")
        load_group_club_from_file()


def save_group_club_to_db(chat_id: int, club_user_id: int) -> None:
    """Save or update group -> club mapping"""
    GROUP_TO_CLUB[chat_id] = club_user_id
    try:
        conn = get_db_connection()
        if not conn:
            save_group_club_to_file()
            return

        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO group_club (chat_id, club_user_id)
                    VALUES (%s, %s)
                    ON CONFLICT (chat_id) DO UPDATE SET club_user_id = EXCLUDED.club_user_id
                    """,
                    (chat_id, club_user_id),
                )
        conn.close()
        print(f"Linked group {chat_id} to club {club_user_id}")
    except Exception as e:
        print(f"Failed to save group_club: {e}")
        save_group_club_to_file()


def load_group_club_from_file() -> None:
    """Fallback: Load group_club from JSON file"""
    global GROUP_TO_CLUB
    if os.path.exists(GROUP_CLUB_FILE):
        try:
            with open(GROUP_CLUB_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            GROUP_TO_CLUB = {int(k): int(v) for k, v in raw.items()}
        except Exception:
            GROUP_TO_CLUB = {}
    else:
        GROUP_TO_CLUB = {}


def save_group_club_to_file() -> None:
    """Fallback: Save GROUP_TO_CLUB to JSON file"""
    raw = {str(k): v for k, v in GROUP_TO_CLUB.items()}
    tmp = GROUP_CLUB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
    os.replace(tmp, GROUP_CLUB_FILE)


def get_club_for_chat(chat_id: int) -> Optional[int]:
    """Return the club user_id for this group, or None if not linked.
    Falls back to DB if not in cache (fixes Heroku multi-dyno / restarts)."""
    if chat_id in GROUP_TO_CLUB:
        return GROUP_TO_CLUB[chat_id]
    conn = get_db_connection()
    if conn:
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT club_user_id FROM group_club WHERE chat_id = %s",
                        (chat_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        club_id = int(row[0])
                        GROUP_TO_CLUB[chat_id] = club_id
                        return club_id
        except Exception as e:
            print(f"[deposit] get_club_for_chat DB fallback failed: {e}")
        finally:
            conn.close()
    return None


def set_group_club(chat_id: int, club_user_id: int) -> None:
    """Link a group to a club (in-memory + DB/file)."""
    GROUP_TO_CLUB[chat_id] = club_user_id
    save_group_club_to_db(chat_id, club_user_id)


# ──────────────────────────────────────────────────────────────────────────────
# FALLBACK JSON FILE FUNCTIONS (for local development)


def load_data_from_file() -> None:
    """Fallback: Load data from JSON file"""
    global USER_COMMANDS
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                USER_COMMANDS = json.load(f)
        except Exception:
            USER_COMMANDS = {}
    else:
        USER_COMMANDS = {}


def save_data_to_file() -> None:
    """Fallback: Save data to JSON file"""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(USER_COMMANDS, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG

# Admin user IDs are now loaded from config.py
# Edit config.py to add/remove admin users
ALLOWED_USER_IDS: Set[int] = set(ADMIN_USER_IDS)

# Where per-user commands are stored on disk
DATA_FILE = "user_commands.json"
# Where group -> club mapping is stored (JSON fallback when no DB)
GROUP_CLUB_FILE = "group_club.json"

# Reserved command names that the bot uses internally
RESERVED_CMDS = {
    "start",
    "help",
    "whoami",
    "set",
    "cancel",
    "delete",
    "mycmds",
    "deposit",
}

# ──────────────────────────────────────────────────────────────────────────────
# RUNTIME STATE

# In-memory cache of per-user commands; loaded from DATA_FILE on start
# Shape: { "<user_id>": { "command": {"type": "text|photo", "content": "message", "file_id": "..."}, ... }, ... }
USER_COMMANDS: Dict[str, Dict[str, dict]] = {}

# Group -> club mapping: chat_id -> club user_id (who added the bot)
GROUP_TO_CLUB: Dict[int, int] = {}

SET_NAME, SET_MESSAGE = range(2)
# Deposit conversation states
DEPOSIT_CHOOSE, DEPOSIT_AMOUNT = range(2, 4)

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


def load_data() -> None:
    """Load user commands and group_club mapping from database (or JSON file as fallback)"""
    load_user_commands_from_db()
    load_group_club_from_db()


def save_data() -> None:
    """This function is kept for compatibility but individual saves are now handled by save_user_command_to_db"""
    pass


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
        delete_user_command_from_db(uid, name)
        await update.message.reply_text(f"Deleted /{name}.")
        await update_user_commands_menu(context.bot, uid)
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
        command_data = {"type": "photo", "file_id": file_id, "caption": caption}
        user_cmds[name] = command_data
        save_user_command_to_db(uid, name, command_data)
        await update.message.reply_text(f"Saved /{name} (photo command).")
        await update_user_commands_menu(context.bot, uid)
    elif update.message.text:
        # Save text command
        user_cmds[name] = update.message.text
        save_user_command_to_db(uid, name, update.message.text)
        await update.message.reply_text(f"Saved /{name}.")
        await update_user_commands_menu(context.bot, uid)
    else:
        await update.message.reply_text("Please send text or a photo for your command.")

    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


async def set_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("Cancelled.")
    context.user_data.pop("pending_cmd_name", None)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
# /deposit FLOW (groups only; uses club's bot* commands)


def _deposit_method_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for deposit method selection."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Venmo", callback_data="deposit:botvenmo"),
                InlineKeyboardButton("Zelle", callback_data="deposit:botzelle"),
            ],
            [
                InlineKeyboardButton("Apple Pay", callback_data="deposit:botstripe"),
                InlineKeyboardButton("Debit Card", callback_data="deposit:botstripe"),
            ],
            [
                InlineKeyboardButton("Cashapp", callback_data="deposit:botcashapp"),
                InlineKeyboardButton("Crypto", callback_data="deposit:botcrypto"),
            ],
        ]
    )


async def deposit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start /deposit: only in groups; show method keyboard."""
    print("[deposit] entry: /deposit received")
    if not update.message or not update.effective_chat or not update.effective_user:
        return ConversationHandler.END
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Use /deposit in a club group.")
        return ConversationHandler.END
    chat_id = chat.id
    club_id = get_club_for_chat(chat_id)
    if club_id is None:
        await update.message.reply_text(
            "This group isn't linked to a club. The club account must add the bot to this group."
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "What method would you like to make a deposit with?",
        reply_markup=_deposit_method_keyboard(),
    )
    return DEPOSIT_CHOOSE


# Display names for deposit methods (for admin notification)
_DEPOSIT_METHOD_DISPLAY = {
    "botvenmo": "Venmo",
    "botzelle": "Zelle",
    "botstripe": "Stripe (Apple Pay/Debit Card)",
    "botcashapp": "Cashapp",
    "botcrypto": "Crypto",
}


async def deposit_method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped a method button; ask for amount (send new msg, keep selection visible)."""
    print("[deposit] method_chosen: button tapped")
    if (
        not update.callback_query
        or not update.effective_user
        or not update.effective_chat
    ):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if not data.startswith("deposit:"):
        return ConversationHandler.END
    cmd_name = data.split(":", 1)[1]
    context.user_data["pending_deposit_method"] = cmd_name
    context.user_data["pending_deposit_chat_id"] = update.effective_chat.id
    method_display = _DEPOSIT_METHOD_DISPLAY.get(cmd_name, cmd_name)
    context.user_data["pending_deposit_method_display"] = method_display
    await query.edit_message_text(
        f"You selected {method_display}. How much would you like to deposit? Please select this message and hit reply to reply correctly",
        reply_markup=InlineKeyboardMarkup([]),
    )
    return DEPOSIT_AMOUNT


async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sent amount; reply with club's payment content + amount."""
    print("[deposit] amount_received: amount message received")
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    if context.user_data.get("pending_deposit_chat_id") != chat_id:
        return ConversationHandler.END
    cmd_name = context.user_data.pop("pending_deposit_method", None)
    context.user_data.pop("pending_deposit_chat_id", None)
    method_display = context.user_data.pop(
        "pending_deposit_method_display", cmd_name or "?"
    )
    if not cmd_name:
        return ConversationHandler.END
    club_id = get_club_for_chat(chat_id)
    if club_id is None:
        print(f"[deposit] chat_id={chat_id} has no club linked")
        await update.message.reply_text("This group is no longer linked to a club.")
        return ConversationHandler.END
    amount_text = (update.message.text or "").strip()
    club_cmds = get_user_dict(club_id)
    cmd_data = club_cmds.get(cmd_name)
    if cmd_data is None:
        cmd_data = load_club_command_from_db(club_id, cmd_name)
    if cmd_data is None:
        print(
            f"[deposit] club_id={club_id} cmd={cmd_name} not found. Keys: {list(club_cmds.keys())}"
        )
        await update.message.reply_text(
            f"This club hasn't set up that payment method yet."
        )
        return ConversationHandler.END
    amount_line = f"Amount: {amount_text}\n\n" if amount_text else ""
    if isinstance(cmd_data, dict):
        cmd_type = cmd_data.get("type", "text")
        if cmd_type == "photo":
            file_id = cmd_data.get("file_id")
            caption = cmd_data.get("caption", "")
            if amount_line:
                caption = amount_line.rstrip() + "\n\n" + (caption or "")
            if file_id:
                await update.message.reply_photo(photo=file_id, caption=caption or None)
            else:
                await update.message.reply_text("Error: Photo data is corrupted.")
        else:
            content = cmd_data.get("content", "")
            await update.message.reply_text(amount_line + content)
    else:
        await update.message.reply_text(amount_line + (cmd_data or ""))

    user_name = update.effective_user.full_name or "Customer"
    try:
        await update.effective_chat.send_message(
            f"Deposit request for {amount_text or '(not specified)'} on {method_display}"
        )
    except Exception:
        pass
    return ConversationHandler.END


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel deposit flow."""
    if update.message:
        await update.message.reply_text("Cancelled.")
    context.user_data.pop("pending_deposit_method", None)
    context.user_data.pop("pending_deposit_chat_id", None)
    context.user_data.pop("pending_deposit_method_display", None)
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
# BOT ADDED TO GROUP (link group to club)


def _bot_was_added_to_chat(update: Update) -> bool:
    """True if this update indicates the bot was just added to the chat."""
    if not update.my_chat_member:
        return False
    old_status = update.my_chat_member.old_chat_member.status
    new_status = update.my_chat_member.new_chat_member.status
    return new_status == "member" and old_status in ("left", "kicked")


async def on_my_chat_member_updated(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """When the bot is added to a group, link that group to the user who added it (club)."""
    if not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        return
    if not _bot_was_added_to_chat(update):
        return
    chat_id = update.effective_chat.id
    club_user_id = update.effective_user.id
    set_group_club(chat_id, club_user_id)


# ──────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP


async def post_init(app):
    # Clear global commands - we'll set per-user commands instead
    await app.bot.set_my_commands([])

    # Clear per-user command menus for all admin users (to remove old cached commands)
    for user_id in ADMIN_USER_IDS:
        try:
            scope = BotCommandScopeChat(chat_id=user_id)
            await app.bot.set_my_commands([], scope=scope)
            print(f"Cleared command menu for user {user_id}")
        except Exception as e:
            print(f"Failed to clear menu for user {user_id}: {e}")

    # Initialize command menus for existing users with actual commands
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

    # Initialize database and load data
    init_database()
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

    # /deposit flow (groups only; any user)
    deposit_conv = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_entry)],
        states={
            DEPOSIT_CHOOSE: [
                CallbackQueryHandler(deposit_method_chosen, pattern="^deposit:"),
            ],
            DEPOSIT_AMOUNT: [
                MessageHandler(
                    filters.TEXT & (~filters.COMMAND),
                    deposit_amount_received,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)],
        name="deposit_conv",
        per_chat=True,
        per_user=True,
    )
    application.add_handler(deposit_conv)

    # Bot added to group -> link group to club
    application.add_handler(
        ChatMemberHandler(on_my_chat_member_updated, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # Catch-all router for any other command (must be added last)
    application.add_handler(MessageHandler(filters.COMMAND, command_router))

    print("Bot is running. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
