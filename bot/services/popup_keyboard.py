"""Player-only /deposit /cashout reply keyboard (popup keyboard)."""

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
from bot.services.club import (
    cashout_shown_on_popup_keyboard,
    get_club_for_chat,
    get_group_name,
    is_club_staff,
)
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

POPUP_IDLE_SECONDS = 300  # 5 minutes (main bot)
POPUP_IDLE_SECONDS_TEST = 30  # faster restore for TestGGSupportBot
BTN_DEPOSIT = "/deposit"
BTN_CASHOUT = "/cashout"
BUTTON_LABELS = frozenset({BTN_DEPOSIT, BTN_CASHOUT})
# Typed cashout alias — never strip when player sends this (starts cashout flow).
FLOW_COMMAND_TEXTS = frozenset({BTN_DEPOSIT, BTN_CASHOUT, "/withdraw"})

INSTALL_COPY = (
    "Looks like your request was handled. Feel free to reach back out anytime!"
)
STRIP_COPY = "We'll be with you in just a second."

CHAT_DATA_LAST_PLAYER_MSG = "popup_kb_last_player_message_id"
CHAT_DATA_LAST_PLAYER_UID = "popup_kb_last_player_user_id"
CHAT_DATA_STRIP = "popup_kb_strip"

_popup_keyboard_app: Any | None = None
# Test bot only: durable DB flag skipped; survives within one worker process.
_installed_memory: dict[int, bool] = {}


def popup_idle_seconds() -> int:
    """Quiet period before installing/restoring the reply keyboard."""
    if is_test_bot_worker():
        return POPUP_IDLE_SECONDS_TEST
    return POPUP_IDLE_SECONDS


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


def is_flow_command_text(text: str | None) -> bool:
    """True when message text is a deposit/cashout command (including bot_command form)."""
    if not text:
        return False
    raw = text.strip()
    if not raw:
        return False
    # "/deposit@BotName" → "/deposit"
    cmd = raw.split()[0].split("@", 1)[0].lower()
    return cmd in {c.lower() for c in FLOW_COMMAND_TEXTS}


def upsert_player_telegram_user_id(
    chat_id: int,
    user_id: int,
    *,
    username: str | None = None,
) -> bool:
    """Overwrite SupportGroupChat.player_telegram_user_id when a row exists.

    Test bot: no-op (always True). Personal test accounts often already own another
    row under the same club_key unique constraint; targeting uses chat_data instead.
    """
    if is_test_bot_worker():
        return True

    row = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
    if row is None:
        return False
    existing = row.player_telegram_user_id
    uname = (username or "").strip().lstrip("@") or None
    same_id = existing is not None and int(existing) == int(user_id)
    same_un = (
        uname is None
        or (row.player_username or "").strip().lstrip("@").lower() == uname.lower()
    )
    if same_id and same_un:
        return True
    kwargs: dict[str, Any] = {}
    if not same_id:
        kwargs["player_telegram_user_id"] = int(user_id)
    if uname is not None:
        kwargs["player_username"] = uname
    if not kwargs:
        return True
    ok, err = update_support_group_chat_row(int(row.id), **kwargs)
    if not ok:
        logger.warning(
            "popup_keyboard: failed to upsert player tg id chat_id=%s err=%s",
            chat_id,
            err,
        )
    return ok


def get_popup_keyboard_installed(chat_id: int) -> bool:
    if is_test_bot_worker():
        return bool(_installed_memory.get(int(chat_id), False))
    row = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
    if row is None:
        return False
    return bool(getattr(row, "popup_keyboard_installed", False))


def set_popup_keyboard_installed(chat_id: int, installed: bool) -> bool:
    """Set installed flag. Test bot: in-memory only. Main bot: support_group_chats."""
    cid = int(chat_id)
    if is_test_bot_worker():
        if installed:
            _installed_memory[cid] = True
        else:
            _installed_memory.pop(cid, None)
        return True

    row = fetch_support_group_chat_by_telegram_chat_id(cid)
    if row is None:
        return False
    if bool(getattr(row, "popup_keyboard_installed", False)) == bool(installed):
        return True
    ok, err = update_support_group_chat_row(
        int(row.id), popup_keyboard_installed=bool(installed)
    )
    if not ok:
        logger.warning(
            "popup_keyboard: failed to set installed=%s chat_id=%s err=%s",
            installed,
            chat_id,
            err,
        )
    return ok


def clear_installed_memory_for_tests() -> None:
    """Test helper."""
    _installed_memory.clear()


def keyboard_markup(*, include_cashout: bool = True) -> ReplyKeyboardMarkup:
    row = [KeyboardButton(BTN_DEPOSIT)]
    if include_cashout:
        row.append(KeyboardButton(BTN_CASHOUT))
    return ReplyKeyboardMarkup(
        [row],
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
    username: str | None = None,
) -> None:
    context.chat_data[CHAT_DATA_LAST_PLAYER_UID] = int(user_id)
    context.chat_data[CHAT_DATA_LAST_PLAYER_MSG] = int(message_id)
    if username:
        context.chat_data["popup_kb_last_player_username"] = (
            str(username).strip().lstrip("@")
        )


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
    """Cancel any pending idle job and schedule install after the quiet period."""
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

    idle_when = popup_idle_seconds()
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
            when=idle_when,
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
            idle_when,
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


def _resolve_player_targeting(
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    player_user_id: int | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> tuple[int | None, int | None]:
    """Return (player_uid, reply_to) for selective reply-to targeting."""
    player_uid = player_user_id
    if player_uid is None and context is not None:
        player_uid = context.chat_data.get(CHAT_DATA_LAST_PLAYER_UID)
    if player_uid is None:
        player_uid = fetch_player_telegram_user_id_for_chat(int(chat_id))

    reply_to = reply_to_message_id
    if reply_to is None and context is not None:
        reply_to = context.chat_data.get(CHAT_DATA_LAST_PLAYER_MSG)

    return (
        int(player_uid) if player_uid else None,
        int(reply_to) if reply_to else None,
    )


async def _send_silent_markup(
    bot: Any,
    *,
    chat_id: int,
    text: str,
    reply_markup: Any,
    reply_to_message_id: int | None = None,
) -> bool:
    """Send selective markup (reply-to player when possible). Keep the message — delete clears the keyboard."""
    kwargs: dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": text,
        "reply_markup": reply_markup,
    }
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = int(reply_to_message_id)
        kwargs["allow_sending_without_reply"] = True

    try:
        await bot.send_message(**kwargs)
        return True
    except Exception:
        logger.warning(
            "popup_keyboard send failed chat_id=%s", chat_id, exc_info=True
        )
        return False


async def install_popup_keyboard(
    bot: Any,
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    player_user_id: int | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> bool:
    """Send selective install with player-facing copy; set installed flag."""
    if not popup_keyboard_eligible(int(chat_id)):
        return False

    row = fetch_support_group_chat_by_telegram_chat_id(int(chat_id))
    if row is None and not is_test_bot_worker():
        logger.info(
            "popup_keyboard install skipped chat_id=%s: no support_group_chats row",
            chat_id,
        )
        return False

    player_uid, reply_to = _resolve_player_targeting(
        int(chat_id),
        reply_to_message_id=reply_to_message_id,
        player_user_id=player_user_id,
        context=context,
    )
    if player_uid is None:
        logger.info(
            "popup_keyboard install skipped chat_id=%s: no player_telegram_user_id",
            chat_id,
        )
        return False

    club_id = None
    include_cashout = True
    try:
        club_id = get_club_for_chat(int(chat_id))
        if club_id is not None:
            include_cashout = cashout_shown_on_popup_keyboard(
                int(club_id), int(chat_id)
            )
    except Exception:
        logger.warning(
            "popup_keyboard: cashout button check failed chat_id=%s; fail open",
            chat_id,
            exc_info=True,
        )
        include_cashout = True
    if not include_cashout:
        logger.info(
            "popup_keyboard deposit-only install chat_id=%s club_id=%s",
            chat_id,
            club_id,
        )

    ok = await _send_silent_markup(
        bot,
        chat_id=int(chat_id),
        text=INSTALL_COPY,
        reply_markup=keyboard_markup(include_cashout=include_cashout),
        reply_to_message_id=reply_to,
    )
    if not ok:
        return False

    set_popup_keyboard_installed(int(chat_id), True)
    logger.info(
        "popup_keyboard installed chat_id=%s player_uid=%s reply_to=%s include_cashout=%s",
        chat_id,
        player_uid,
        reply_to,
        include_cashout,
    )
    return True


async def remove_popup_keyboard(
    bot: Any,
    chat_id: int,
    *,
    reply_to_message_id: int | None = None,
    text: str | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
    silent: bool = False,
) -> bool:
    """Send selective ReplyKeyboardRemove. Returns True if sent.

    When silent=True (or text is None), use STRIP_COPY and clear installed flag.
    """
    _player_uid, reply_to = _resolve_player_targeting(
        int(chat_id),
        reply_to_message_id=reply_to_message_id,
        context=context,
    )

    use_default_strip = silent or text is None
    body = STRIP_COPY if use_default_strip else text
    ok = await _send_silent_markup(
        bot,
        chat_id=int(chat_id),
        text=body or STRIP_COPY,
        reply_markup=remove_markup(),
        reply_to_message_id=reply_to,
    )
    if ok:
        set_popup_keyboard_installed(int(chat_id), False)
    return ok


async def silent_strip_if_installed(
    bot: Any,
    chat_id: int,
    *,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> bool:
    """If durable flag is set, silently remove keyboard and clear flag."""
    if not get_popup_keyboard_installed(int(chat_id)):
        return False
    return await remove_popup_keyboard(
        bot, int(chat_id), context=context, silent=True
    )


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
    """Cancel idle, clear durable flag, mark strip when feature is eligible."""
    on_flow_entry_cancel_idle(context, chat_id)
    if popup_keyboard_eligible(chat_id, club_id=club_id, title=title):
        set_popup_keyboard_installed(int(chat_id), False)
        mark_strip_keyboard(context)


def on_flow_exit_schedule_idle(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | None
) -> None:
    if chat_id is None:
        return
    # Clear pending strip so a cancelled flow doesn't affect later replies.
    context.chat_data.pop(CHAT_DATA_STRIP, None)
    schedule_popup_keyboard_idle(context, int(chat_id))
