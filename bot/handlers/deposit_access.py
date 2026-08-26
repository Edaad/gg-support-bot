"""DM staff flow: /depositaccess and /cashoutaccess for per-group method access."""

from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.handlers.flow_cancel import (
    block_if_dm_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.services.deposit_method_access import (
    MethodDirection,
    can_manage_deposit_access,
    can_use_deposit_access_commands,
    delete_access,
    format_access_list,
    list_access_entries,
    methods_for_action,
    upsert_access,
)
from bot.services.venmo_payments import resolve_bound_group

logger = logging.getLogger(__name__)

_GROUP_TITLE_PROMPT = (
    "Enter group title (e.g. RT / 6485-8168 / Angus Mcgoon):"
)

_PREFIX: dict[MethodDirection, str] = {"deposit": "da", "cashout": "ca"}
_FLOW: dict[MethodDirection, str] = {
    "deposit": "deposit_access",
    "cashout": "cashout_access",
}
_COMMAND: dict[MethodDirection, str] = {
    "deposit": "/depositaccess",
    "cashout": "/cashoutaccess",
}
_LIST_COMMAND: dict[MethodDirection, str] = {
    "deposit": "/listdepositaccess",
    "cashout": "/listcashoutaccess",
}

_UD_SUFFIXES = (
    "step",
    "admin_id",
    "chat_id",
    "club_id",
    "group_title",
    "action",
    "method_id",
    "method_name",
    "method_slug",
    "existing_type",
)


def _ud(direction: MethodDirection, suffix: str) -> str:
    return f"{_FLOW[direction]}_{suffix}"


def _active_keys(direction: MethodDirection) -> tuple[str, ...]:
    return tuple(_ud(direction, s) for s in _UD_SUFFIXES)


def deposit_access_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(_ud("deposit", "step")))


def cashout_access_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(_ud("cashout", "step")))


def _active_direction(context: ContextTypes.DEFAULT_TYPE) -> Optional[MethodDirection]:
    if cashout_access_flow_active(context):
        return "cashout"
    if deposit_access_flow_active(context):
        return "deposit"
    return None


def _cleanup(
    context: ContextTypes.DEFAULT_TYPE,
    direction: Optional[MethodDirection] = None,
) -> None:
    clear_active_flow(context)
    dirs: tuple[MethodDirection, ...]
    if direction is None:
        dirs = ("deposit", "cashout")
    else:
        dirs = (direction,)
    for d in dirs:
        for key in _active_keys(d):
            context.user_data.pop(key, None)


def _is_actor(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    direction: MethodDirection,
) -> bool:
    if not update.effective_user:
        return False
    admin_id = context.user_data.get(_ud(direction, "admin_id"))
    return admin_id is not None and update.effective_user.id == admin_id


def _action_keyboard(direction: MethodDirection) -> InlineKeyboardMarkup:
    p = _PREFIX[direction]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Blacklist", callback_data=f"{p}:act:blacklist"),
                InlineKeyboardButton("Whitelist", callback_data=f"{p}:act:whitelist"),
            ],
            [InlineKeyboardButton("Remove", callback_data=f"{p}:act:remove")],
            [InlineKeyboardButton("Cancel", callback_data=f"{p}:cancel")],
        ]
    )


def _method_keyboard(
    methods: list[dict], direction: MethodDirection
) -> InlineKeyboardMarkup:
    p = _PREFIX[direction]
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for m in methods:
        label = m["slug"]
        if m.get("access_type"):
            label = f"{m['slug']} ({m['access_type']})"
        row.append(InlineKeyboardButton(label, callback_data=f"{p}:m:{m['id']}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Cancel", callback_data=f"{p}:cancel")])
    return InlineKeyboardMarkup(buttons)


def _confirm_keyboard(direction: MethodDirection) -> InlineKeyboardMarkup:
    p = _PREFIX[direction]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=f"{p}:confirm"),
                InlineKeyboardButton("Cancel", callback_data=f"{p}:cancel"),
            ]
        ]
    )


async def _access_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    direction: MethodDirection,
) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    cmd = _COMMAND[direction]
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(f"Use {cmd} in a private chat with this bot.")
        raise ApplicationHandlerStop()

    uid = int(update.effective_user.id)
    if not can_use_deposit_access_commands(uid):
        await update.message.reply_text(
            f"You are not allowed to manage {direction} method access."
        )
        raise ApplicationHandlerStop()

    flow = _FLOW[direction]
    if await block_if_dm_flow_active(update, context, starting=flow):  # type: ignore[arg-type]
        raise ApplicationHandlerStop()

    _cleanup(context, direction)
    mark_active_flow(context, flow)  # type: ignore[arg-type]
    context.user_data[_ud(direction, "step")] = "group_title"
    context.user_data[_ud(direction, "admin_id")] = uid
    await update.message.reply_text(_GROUP_TITLE_PROMPT)
    raise ApplicationHandlerStop()


async def depositaccess_entry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _access_entry(update, context, "deposit")


async def cashoutaccess_entry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _access_entry(update, context, "cashout")


async def _list_access_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    direction: MethodDirection,
) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    cmd = _LIST_COMMAND[direction]
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(f"Use {cmd} in a private chat with this bot.")
        raise ApplicationHandlerStop()

    uid = int(update.effective_user.id)
    if not can_use_deposit_access_commands(uid):
        await update.message.reply_text(
            f"You are not allowed to view {direction} method access."
        )
        raise ApplicationHandlerStop()

    text = format_access_list(
        list_access_entries(uid, direction=direction), direction=direction
    )
    if len(text) <= 4000:
        await update.message.reply_text(text)
        raise ApplicationHandlerStop()
    chunk = ""
    for line in text.splitlines(keepends=True):
        if len(chunk) + len(line) > 4000:
            await update.message.reply_text(chunk)
            chunk = line
        else:
            chunk += line
    if chunk:
        await update.message.reply_text(chunk)
    raise ApplicationHandlerStop()


async def listdepositaccess_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _list_access_handler(update, context, "deposit")


async def listcashoutaccess_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _list_access_handler(update, context, "cashout")


async def depositaccess_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    direction = _active_direction(context)
    if direction is None:
        return
    if not update.message or not update.effective_user:
        return
    if not _is_actor(update, context, direction):
        return
    if context.user_data.get(_ud(direction, "step")) != "group_title":
        return

    title = (update.message.text or "").strip()
    logger.info(
        "%saccess group_title user_id=%s title=%r step=%s",
        direction,
        update.effective_user.id,
        title[:80],
        context.user_data.get(_ud(direction, "step")),
    )
    resolved = resolve_bound_group(title)
    if not resolved.ok or not resolved.bound_group:
        logger.info(
            "%saccess resolve failed user_id=%s error=%s",
            direction,
            update.effective_user.id,
            resolved.error,
        )
        await update.message.reply_text(
            resolved.error or "Could not resolve that group title."
        )
        raise ApplicationHandlerStop()

    group = resolved.bound_group
    uid = int(update.effective_user.id)
    if not can_manage_deposit_access(uid, int(group.club_id)):
        await update.message.reply_text("You are not staff for this group's club.")
        _cleanup(context, direction)
        raise ApplicationHandlerStop()

    context.user_data[_ud(direction, "chat_id")] = int(group.telegram_chat_id)
    context.user_data[_ud(direction, "club_id")] = int(group.club_id)
    context.user_data[_ud(direction, "group_title")] = group.group_title
    context.user_data[_ud(direction, "step")] = "action"
    logger.info(
        "%saccess resolved user_id=%s chat_id=%s club_id=%s",
        direction,
        uid,
        group.telegram_chat_id,
        group.club_id,
    )
    await update.message.reply_text(
        f"Group: {group.group_title}\nWhat would you like to do?",
        reply_markup=_action_keyboard(direction),
    )
    raise ApplicationHandlerStop()


def _direction_from_callback(data: str) -> Optional[MethodDirection]:
    if data.startswith("ca:"):
        return "cashout"
    if data.startswith("da:"):
        return "deposit"
    return None


async def depositaccess_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.callback_query or not update.effective_user:
        return
    direction = _active_direction(context)
    if direction is None:
        return
    if not _is_actor(update, context, direction):
        return

    query = update.callback_query
    data = query.data or ""
    cb_direction = _direction_from_callback(data)
    if cb_direction != direction:
        return
    await query.answer()

    p = _PREFIX[direction]
    cmd = _COMMAND[direction]
    label = direction

    if data == f"{p}:cancel":
        await query.edit_message_text(f"{label.capitalize()} access update cancelled.")
        _cleanup(context, direction)
        return

    if data.startswith(f"{p}:act:"):
        action = data.split(":", 2)[2]
        if action not in ("blacklist", "whitelist", "remove"):
            return
        chat_id = context.user_data.get(_ud(direction, "chat_id"))
        club_id = context.user_data.get(_ud(direction, "club_id"))
        if chat_id is None or club_id is None:
            await query.edit_message_text(f"Session expired. Use {cmd} again.")
            _cleanup(context, direction)
            return
        methods = methods_for_action(
            int(club_id), int(chat_id), action, direction=direction  # type: ignore[arg-type]
        )
        if not methods:
            empty_msgs = {
                "blacklist": (
                    f"No public {label} methods left to blacklist for this group."
                ),
                "whitelist": (
                    f"No private {label} methods left to whitelist for this group."
                ),
                "remove": "This group has no blacklist or whitelist entries.",
            }
            await query.edit_message_text(empty_msgs[action])
            _cleanup(context, direction)
            return
        context.user_data[_ud(direction, "action")] = action
        context.user_data[_ud(direction, "step")] = "method"
        await query.edit_message_text(
            f"Select a method to {action}:",
            reply_markup=_method_keyboard(methods, direction),
        )
        return

    if data.startswith(f"{p}:m:"):
        if context.user_data.get(_ud(direction, "step")) != "method":
            return
        method_id = int(data.split(":")[2])
        action = context.user_data.get(_ud(direction, "action"))
        club_id = context.user_data.get(_ud(direction, "club_id"))
        chat_id = context.user_data.get(_ud(direction, "chat_id"))
        if not action or club_id is None or chat_id is None:
            await query.edit_message_text(f"Session expired. Use {cmd} again.")
            _cleanup(context, direction)
            return
        methods = methods_for_action(
            int(club_id), int(chat_id), action, direction=direction  # type: ignore[arg-type]
        )
        chosen = next((m for m in methods if int(m["id"]) == method_id), None)
        if not chosen:
            await query.edit_message_text(
                "That method is no longer available for this action."
            )
            _cleanup(context, direction)
            return
        context.user_data[_ud(direction, "method_id")] = method_id
        context.user_data[_ud(direction, "method_name")] = chosen["name"]
        context.user_data[_ud(direction, "method_slug")] = chosen["slug"]
        context.user_data[_ud(direction, "existing_type")] = chosen.get("access_type")
        context.user_data[_ud(direction, "step")] = "confirm"
        title = context.user_data.get(_ud(direction, "group_title"), "?")
        slug = chosen["slug"]
        if action == "remove":
            existing = chosen.get("access_type", "entry")
            summary = (
                f"Remove {existing} for {slug} "
                f"on group:\n{title}?"
            )
        else:
            summary = (
                f"{action.capitalize()} {slug} "
                f"for group:\n{title}?"
            )
        await query.edit_message_text(
            summary, reply_markup=_confirm_keyboard(direction)
        )
        return

    if data == f"{p}:confirm":
        if context.user_data.get(_ud(direction, "step")) != "confirm":
            return
        action = context.user_data.get(_ud(direction, "action"))
        method_id = context.user_data.get(_ud(direction, "method_id"))
        chat_id = context.user_data.get(_ud(direction, "chat_id"))
        club_id = context.user_data.get(_ud(direction, "club_id"))
        title = context.user_data.get(_ud(direction, "group_title"), "?")
        uid = context.user_data.get(_ud(direction, "admin_id"))
        if (
            action is None
            or method_id is None
            or chat_id is None
            or club_id is None
        ):
            await query.edit_message_text(f"Session expired. Use {cmd} again.")
            _cleanup(context, direction)
            return
        try:
            if action == "remove":
                deleted = delete_access(
                    telegram_chat_id=int(chat_id),
                    club_payment_method_id=int(method_id),
                )
                if not deleted:
                    await query.edit_message_text("Nothing to remove (already gone).")
                else:
                    await query.edit_message_text(
                        f"Removed {deleted.access_type} for {deleted.method_slug} "
                        f"on:\n{title}"
                    )
            else:
                entry = upsert_access(
                    telegram_chat_id=int(chat_id),
                    club_id=int(club_id),
                    club_payment_method_id=int(method_id),
                    access_type=action,  # type: ignore[arg-type]
                    created_by_telegram_user_id=int(uid) if uid else None,
                    direction=direction,
                )
                await query.edit_message_text(
                    f"{entry.access_type.capitalize()} set for {entry.method_slug} "
                    f"on:\n{title}"
                )
        except ValueError as e:
            await query.edit_message_text(str(e))
        except Exception:
            logger.exception("%saccess confirm failed", direction)
            await query.edit_message_text(
                f"Could not update {label} method access. Check logs."
            )
        _cleanup(context, direction)


async def _access_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    direction: Optional[MethodDirection] = None,
) -> None:
    d = direction or _active_direction(context) or "deposit"
    _cleanup(context, d)
    msg = f"{d.capitalize()} access update cancelled."
    if update.message:
        await update.message.reply_text(msg)
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg)


async def depositaccess_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _access_cancel(update, context, "deposit")


async def cashoutaccess_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await _access_cancel(update, context, "cashout")
