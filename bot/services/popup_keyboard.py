"""Player-only Deposit/Cashout/Other reply keyboard (popup keyboard)."""

from __future__ import annotations

import logging
from typing import Any

from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)
from telegram.ext import ContextTypes

from bot.runtime_config import is_test_bot_worker
from bot.services.club import get_club_for_chat, get_group_name, is_club_staff
from bot.services.player_details import gg_player_id_from_title
from bot.services.support_group_chats import (
    fetch_player_telegram_user_id_for_chat,
    fetch_support_group_chat_by_telegram_chat_id,
    update_support_group_chat_row,
)
from club_gc_settings import get_club_gc_config_by_link_club_id, get_gc_users_to_add
from config import ADMIN_USER_IDS
from db.connection import get_db
from db.models import Club

logger = logging.getLogger(__name__)

POPUP_IDLE_SECONDS = 300  # 5 minutes
BTN_DEPOSIT = "Deposit"
BTN_CASHOUT = "Cashout"
BTN_OTHER = "Other"
BUTTON_LABELS = frozenset({BTN_DEPOSIT, BTN_CASHOUT, BTN_OTHER})

INSTALL_COPY = (
    "Looks like your request was handled. Feel free to reach back out anytime!"
)
OTHER_ACK = "Got it — type your message."

CHAT_DATA_LAST_PLAYER_MSG = "popup_kb_last_player_message_id"
CHAT_DATA_LAST_PLAYER_UID = "popup_kb_last_player_user_id"
CHAT_DATA_STRIP = "popup_kb_strip"

_popup_keyboard_app: Any | None = None


def _idle_job_name(chat_id: int | str) -> str:
    return f"popup_keyboard_idle_{chat_id}"


def idle_job_name(chat_id: int | str) -> str:
    """Public name for tests."""
    return _idle_job_name(chat_id)


def register_popup_keyboard_runtime(app: Any) -> None:
    """Store Application for idle jobs outside handlers."""
    global _popup_keyboard_app
    _popup_keyboard_app = app


def popup_keyboard_enabled(club_id: int | None) -> bool:
    """True when test bot worker, or main bot club flag is on."""
    if is_test_bot_worker():
        return True
    if club_id is None:
        return False
    with get_db() as session:
        club = session.get(Club, int(club_id))
        if club is None:
            return False
        return bool(club.enable_popup_keyboard)


def group_has_gg_player_id(chat_id: int, title: str | None = None) -> bool:
    """True when ClubGG player id is present on the group title / stored name."""
    stored = get_group_name(chat_id)
    for candidate in (stored, title):
        if gg_player_id_from_title(candidate):
            return True
    return False


def popup_keyboard_eligible(
    chat_id: int,
    *,
    club_id: int | None = None,
    title: str | None = None,
) -> bool:
    cid = club_id if club_id is not None else get_club_for_chat(chat_id)
    if not popup_keyboard_enabled(cid):
        return False
    return group_has_gg_player_id(chat_id, title=title)


def is_support_sender(user: User | None, club_id: int) -> bool:
    """True for club staff, admins, and /gc invite accounts (never the player)."""
    if user is None:
        return True
    if getattr(user, "is_bot", False):
        return True
    uid = int(user.id)
    if uid in ADMIN_USER_IDS:
        return True
    if is_club_staff(uid, club_id):
        return True

    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is None:
        return False
    if cfg.command_admin_user_id and uid == int(cfg.command_admin_user_id):
        return True

    markers: list[str] = list(get_gc_users_to_add(cfg))
    if cfg.bot_account:
        markers.append(str(cfg.bot_account))

    un = (user.username or "").strip().lower().lstrip("@")
    for raw in markers:
        m = str(raw).strip()
        if not m:
            continue
        if m.isdigit() and int(m) == uid:
            return True
        if un and m.lstrip("@").lower() == un:
            return True
    return False


def upsert_player_telegram_user_id(chat_id: int, user_id: int) -> bool:
    """Overwrite SupportGroupChat.player_telegram_user_id when a row exists."""
    row = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
    if row is None:
        return False
    existing = row.player_telegram_user_id
    if existing is not None and int(existing) == int(user_id):
        return True
    ok, err = update_support_group_chat_row(
        int(row.id), player_telegram_user_id=int(user_id)
    )
    if not ok:
        logger.warning(
            "popup_keyboard: failed to upsert player tg id chat_id=%s err=%s",
            chat_id,
            err,
        )
    return ok


def keyboard_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(BTN_DEPOSIT),
                KeyboardButton(BTN_CASHOUT),
                KeyboardButton(BTN_OTHER),
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        selective=True,
    )


def remove_markup() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove(selective=True)


def remember_player_message(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    message_id: int,
) -> None:
    context.chat_data[CHAT_DATA_LAST_PLAYER_UID] = int(user_id)
    context.chat_data[CHAT_DATA_LAST_PLAYER_MSG] = int(message_id)


def cancel_popup_keyboard_idle(
    chat_id: int | str,
    *,
    job_queue: Any | None = None,
) -> None:
    queue = job_queue
    if queue is None and _popup_keyboard_app is not None:
        queue = getattr(_popup_keyboard_app, "job_queue", None)
    if queue is None:
        return
    try:
        for job in queue.get_jobs_by_name(_idle_job_name(chat_id)):
            job.schedule_removal()
    except Exception:
        logger.debug(
            "popup_keyboard: cancel idle failed chat_id=%s", chat_id, exc_info=True
        )


def schedule_popup_keyboard_idle(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    player_user_id: int | None = None,
) -> None:
    """Cancel any pending idle job and schedule install after POPUP_IDLE_SECONDS."""
    if not popup_keyboard_eligible(int(chat_id)):
        return

    last_msg = reply_to_message_id
    if last_msg is None:
        last_msg = context.chat_data.get(CHAT_DATA_LAST_PLAYER_MSG)
    last_uid = player_user_id
    if last_uid is None:
        last_uid = context.chat_data.get(CHAT_DATA_LAST_PLAYER_UID)
    if last_uid is None:
        last_uid = fetch_player_telegram_user_id_for_chat(int(chat_id))

    try:
        job_queue = getattr(context, "job_queue", None)
        if job_queue is None:
            logger.debug(
                "popup_keyboard: no job_queue; skip idle schedule chat_id=%s",
                chat_id,
            )
            return
        name = _idle_job_name(chat_id)
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()
        job_queue.run_once(
            _popup_keyboard_idle_callback,
            when=POPUP_IDLE_SECONDS,
            chat_id=int(chat_id),
            data={
                "chat_id": int(chat_id),
                "reply_to_message_id": int(last_msg) if last_msg else None,
                "player_user_id": int(last_uid) if last_uid else None,
            },
            name=name,
        )
        logger.info(
            "popup_keyboard idle scheduled chat_id=%s in %ss",
            chat_id,
            POPUP_IDLE_SECONDS,
        )
    except Exception:
        logger.warning(
            "popup_keyboard: failed to schedule idle chat_id=%s",
            chat_id,
            exc_info=True,
        )


async def _popup_keyboard_idle_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    if job is None or not job.data:
        return
    chat_id = int(job.data["chat_id"])
    reply_to = job.data.get("reply_to_message_id")
    player_uid = job.data.get("player_user_id")
    await install_popup_keyboard(
        context.bot,
        chat_id,
        reply_to_message_id=int(reply_to) if reply_to else None,
        player_user_id=int(player_uid) if player_uid else None,
        context=context,
    )


async def install_popup_keyboard(
    bot: Any,
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    player_user_id: int | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> bool:
    """Send selective install message with reply keyboard. Returns True if sent."""
    if not popup_keyboard_eligible(int(chat_id)):
        return False

    player_uid = player_user_id
    if player_uid is None and context is not None:
        player_uid = context.chat_data.get(CHAT_DATA_LAST_PLAYER_UID)
    if player_uid is None:
        player_uid = fetch_player_telegram_user_id_for_chat(int(chat_id))
    if player_uid is None:
        logger.info(
            "popup_keyboard install skipped chat_id=%s: no player_telegram_user_id",
            chat_id,
        )
        return False

    reply_to = reply_to_message_id
    if reply_to is None and context is not None:
        reply_to = context.chat_data.get(CHAT_DATA_LAST_PLAYER_MSG)

    kwargs: dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": INSTALL_COPY,
        "reply_markup": keyboard_markup(),
    }
    if reply_to:
        kwargs["reply_to_message_id"] = int(reply_to)
        kwargs["allow_sending_without_reply"] = True

    try:
        await bot.send_message(**kwargs)
        logger.info(
            "popup_keyboard installed chat_id=%s player_uid=%s reply_to=%s",
            chat_id,
            player_uid,
            reply_to,
        )
        return True
    except Exception:
        logger.warning(
            "popup_keyboard install failed chat_id=%s", chat_id, exc_info=True
        )
        return False


async def remove_popup_keyboard(
    bot: Any,
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    text: str | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> bool:
    """Send selective ReplyKeyboardRemove. Returns True if sent."""
    reply_to = reply_to_message_id
    if reply_to is None and context is not None:
        reply_to = context.chat_data.get(CHAT_DATA_LAST_PLAYER_MSG)

    kwargs: dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": text or "\u200b",  # zero-width space if no copy
        "reply_markup": remove_markup(),
    }
    if reply_to:
        kwargs["reply_to_message_id"] = int(reply_to)
        kwargs["allow_sending_without_reply"] = True

    try:
        await bot.send_message(**kwargs)
        return True
    except Exception:
        logger.warning(
            "popup_keyboard remove failed chat_id=%s", chat_id, exc_info=True
        )
        return False


def on_flow_entry_cancel_idle(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | None
) -> None:
    if chat_id is None:
        return
    cancel_popup_keyboard_idle(
        chat_id, job_queue=getattr(context, "job_queue", None)
    )


def mark_strip_keyboard(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Next bot reply in this chat should attach ReplyKeyboardRemove."""
    context.chat_data[CHAT_DATA_STRIP] = True


def pop_strip_reply_markup(context: ContextTypes.DEFAULT_TYPE):
    """Return ReplyKeyboardRemove once if strip was marked, else None."""
    if context.chat_data.pop(CHAT_DATA_STRIP, None):
        return remove_markup()
    return None


def prepare_flow_entry_keyboard(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    club_id: int | None = None,
    title: str | None = None,
) -> None:
    """Cancel idle and mark strip when feature is eligible for this group."""
    on_flow_entry_cancel_idle(context, chat_id)
    if popup_keyboard_eligible(chat_id, club_id=club_id, title=title):
        mark_strip_keyboard(context)


def on_flow_exit_schedule_idle(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | None
) -> None:
    if chat_id is None:
        return
    # Clear pending strip so a cancelled flow doesn't affect later replies.
    context.chat_data.pop(CHAT_DATA_STRIP, None)
    schedule_popup_keyboard_idle(context, int(chat_id))
