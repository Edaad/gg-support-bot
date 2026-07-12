"""DM staff flow: /depositaccess and /listdepositaccess for per-group method access."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.handlers.flow_cancel import (
    block_if_dm_flow_active,
    clear_active_flow,
    mark_active_flow,
)
from bot.services.deposit_method_access import (
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

STEP_KEY = "deposit_access_step"
_GROUP_TITLE_PROMPT = (
    "Enter group title (e.g. RT / 6485-8168 / Angus Mcgoon):"
)
_ACTIVE_KEYS = (
    STEP_KEY,
    "deposit_access_admin_id",
    "deposit_access_chat_id",
    "deposit_access_club_id",
    "deposit_access_group_title",
    "deposit_access_action",
    "deposit_access_method_id",
    "deposit_access_method_name",
    "deposit_access_method_slug",
    "deposit_access_existing_type",
)


def deposit_access_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(STEP_KEY))


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_active_flow(context)
    for key in _ACTIVE_KEYS:
        context.user_data.pop(key, None)


def _is_actor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    admin_id = context.user_data.get("deposit_access_admin_id")
    return admin_id is not None and update.effective_user.id == admin_id


def _action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Blacklist", callback_data="da:act:blacklist"),
                InlineKeyboardButton("Whitelist", callback_data="da:act:whitelist"),
            ],
            [InlineKeyboardButton("Remove", callback_data="da:act:remove")],
            [InlineKeyboardButton("Cancel", callback_data="da:cancel")],
        ]
    )


def _method_keyboard(methods: list[dict]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for m in methods:
        label = m["name"]
        if m.get("access_type"):
            label = f"{m['name']} ({m['access_type']})"
        row.append(
            InlineKeyboardButton(label, callback_data=f"da:m:{m['id']}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Cancel", callback_data="da:cancel")])
    return InlineKeyboardMarkup(buttons)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data="da:confirm"),
                InlineKeyboardButton("Cancel", callback_data="da:cancel"),
            ]
        ]
    )


async def depositaccess_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            "Use /depositaccess in a private chat with this bot."
        )
        raise ApplicationHandlerStop()

    uid = int(update.effective_user.id)
    if not can_use_deposit_access_commands(uid):
        await update.message.reply_text(
            "You are not allowed to manage deposit method access."
        )
        raise ApplicationHandlerStop()

    if await block_if_dm_flow_active(update, context, starting="deposit_access"):
        raise ApplicationHandlerStop()

    _cleanup(context)
    mark_active_flow(context, "deposit_access")
    context.user_data[STEP_KEY] = "group_title"
    context.user_data["deposit_access_admin_id"] = uid
    await update.message.reply_text(_GROUP_TITLE_PROMPT)
    raise ApplicationHandlerStop()


async def listdepositaccess_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            "Use /listdepositaccess in a private chat with this bot."
        )
        raise ApplicationHandlerStop()

    uid = int(update.effective_user.id)
    if not can_use_deposit_access_commands(uid):
        await update.message.reply_text(
            "You are not allowed to view deposit method access."
        )
        raise ApplicationHandlerStop()

    text = format_access_list(list_access_entries(uid))
    # Telegram message limit ~4096
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


async def depositaccess_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not deposit_access_flow_active(context):
        return
    if not update.message or not update.effective_user:
        return
    if not _is_actor(update, context):
        return
    if context.user_data.get(STEP_KEY) != "group_title":
        return

    title = (update.message.text or "").strip()
    logger.info(
        "depositaccess group_title user_id=%s title=%r",
        update.effective_user.id,
        title[:80],
    )
    resolved = resolve_bound_group(title)
    if not resolved.ok or not resolved.bound_group:
        logger.info(
            "depositaccess resolve failed user_id=%s error=%s",
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
        await update.message.reply_text(
            "You are not staff for this group's club."
        )
        _cleanup(context)
        raise ApplicationHandlerStop()

    context.user_data["deposit_access_chat_id"] = int(group.telegram_chat_id)
    context.user_data["deposit_access_club_id"] = int(group.club_id)
    context.user_data["deposit_access_group_title"] = group.group_title
    context.user_data[STEP_KEY] = "action"
    logger.info(
        "depositaccess resolved user_id=%s chat_id=%s club_id=%s",
        uid,
        group.telegram_chat_id,
        group.club_id,
    )
    await update.message.reply_text(
        f"Group: {group.group_title}\nWhat would you like to do?",
        reply_markup=_action_keyboard(),
    )
    raise ApplicationHandlerStop()


async def depositaccess_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.callback_query or not update.effective_user:
        return
    if not deposit_access_flow_active(context):
        return
    if not _is_actor(update, context):
        return

    query = update.callback_query
    data = query.data or ""
    await query.answer()

    if data == "da:cancel":
        await query.edit_message_text("Deposit access update cancelled.")
        _cleanup(context)
        return

    if data.startswith("da:act:"):
        action = data.split(":", 2)[2]
        if action not in ("blacklist", "whitelist", "remove"):
            return
        chat_id = context.user_data.get("deposit_access_chat_id")
        club_id = context.user_data.get("deposit_access_club_id")
        if chat_id is None or club_id is None:
            await query.edit_message_text("Session expired. Use /depositaccess again.")
            _cleanup(context)
            return
        methods = methods_for_action(int(club_id), int(chat_id), action)  # type: ignore[arg-type]
        if not methods:
            empty_msgs = {
                "blacklist": "No public deposit methods left to blacklist for this group.",
                "whitelist": "No private deposit methods left to whitelist for this group.",
                "remove": "This group has no blacklist or whitelist entries.",
            }
            await query.edit_message_text(empty_msgs[action])
            _cleanup(context)
            return
        context.user_data["deposit_access_action"] = action
        context.user_data[STEP_KEY] = "method"
        await query.edit_message_text(
            f"Select a method to {action}:",
            reply_markup=_method_keyboard(methods),
        )
        return

    if data.startswith("da:m:"):
        if context.user_data.get(STEP_KEY) != "method":
            return
        method_id = int(data.split(":")[2])
        action = context.user_data.get("deposit_access_action")
        club_id = context.user_data.get("deposit_access_club_id")
        chat_id = context.user_data.get("deposit_access_chat_id")
        if not action or club_id is None or chat_id is None:
            await query.edit_message_text("Session expired. Use /depositaccess again.")
            _cleanup(context)
            return
        methods = methods_for_action(int(club_id), int(chat_id), action)  # type: ignore[arg-type]
        chosen = next((m for m in methods if int(m["id"]) == method_id), None)
        if not chosen:
            await query.edit_message_text(
                "That method is no longer available for this action."
            )
            _cleanup(context)
            return
        context.user_data["deposit_access_method_id"] = method_id
        context.user_data["deposit_access_method_name"] = chosen["name"]
        context.user_data["deposit_access_method_slug"] = chosen["slug"]
        context.user_data["deposit_access_existing_type"] = chosen.get("access_type")
        context.user_data[STEP_KEY] = "confirm"
        title = context.user_data.get("deposit_access_group_title", "?")
        if action == "remove":
            existing = chosen.get("access_type", "entry")
            summary = (
                f"Remove {existing} for {chosen['name']} "
                f"on group:\n{title}?"
            )
        else:
            summary = (
                f"{action.capitalize()} {chosen['name']} "
                f"for group:\n{title}?"
            )
        await query.edit_message_text(summary, reply_markup=_confirm_keyboard())
        return

    if data == "da:confirm":
        if context.user_data.get(STEP_KEY) != "confirm":
            return
        action = context.user_data.get("deposit_access_action")
        method_id = context.user_data.get("deposit_access_method_id")
        chat_id = context.user_data.get("deposit_access_chat_id")
        club_id = context.user_data.get("deposit_access_club_id")
        method_name = context.user_data.get("deposit_access_method_name", "?")
        title = context.user_data.get("deposit_access_group_title", "?")
        uid = context.user_data.get("deposit_access_admin_id")
        if (
            action is None
            or method_id is None
            or chat_id is None
            or club_id is None
        ):
            await query.edit_message_text("Session expired. Use /depositaccess again.")
            _cleanup(context)
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
                        f"Removed {deleted.access_type} for {deleted.method_name} "
                        f"on:\n{title}"
                    )
            else:
                entry = upsert_access(
                    telegram_chat_id=int(chat_id),
                    club_id=int(club_id),
                    club_payment_method_id=int(method_id),
                    access_type=action,  # type: ignore[arg-type]
                    created_by_telegram_user_id=int(uid) if uid else None,
                )
                await query.edit_message_text(
                    f"{entry.access_type.capitalize()} set for {entry.method_name} "
                    f"on:\n{title}"
                )
        except ValueError as e:
            await query.edit_message_text(str(e))
        except Exception:
            logger.exception("depositaccess confirm failed")
            await query.edit_message_text(
                "Could not update deposit method access. Check logs."
            )
        _cleanup(context)


async def depositaccess_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    _cleanup(context)
    if update.message:
        await update.message.reply_text("Deposit access update cancelled.")
    elif update.callback_query:
        await update.callback_query.edit_message_text("Deposit access update cancelled.")
