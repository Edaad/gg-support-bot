"""Staff issue reports: /report wizard and /reports triage."""

from __future__ import annotations

import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from club_gc_settings import gc_mtproto_operator_telegram_user_ids
from config import ADMIN_USER_IDS
from bot.handlers.flow_cancel import clear_active_flow, mark_active_flow
from bot.services.club import get_club_for_chat, is_any_club_staff, is_club_staff
from bot.services.issue_report_drafts import (
    DraftContext,
    cancel_draft,
    create_draft,
    draft_to_context,
    get_latest_pending_draft,
    get_pending_draft,
    mark_draft_submitted,
)
from bot.services.issue_report_notify import notify_staff_issue_report_draft
from bot.services.issue_reports import (
    CATEGORY_LABELS,
    ISSUE_REPORT_CATEGORIES,
    NOTIFY_LABELS,
    NOTIFY_TAGS,
    IssueReportFileInput,
    IssueReportValidationError,
    add_report_evidence,
    create_issue_report,
    default_notify_for_category,
    format_open_reports_list,
    format_report_detail,
    format_resolve_result,
    format_resolved_reports_list,
    get_issue_report,
    list_open_reports,
    list_resolved_reports,
    resolve_report,
    update_report_details,
)

from db.connection import get_db

logger = logging.getLogger(__name__)

(
    IR_CATEGORY,
    IR_NOTIFY,
    IR_TITLE,
    IR_DETAILS,
    IR_EVIDENCE,
    IR_CONFIRM,
) = range(6)

_IR_USER_KEYS = (
    "ir_draft_id",
    "ir_club_id",
    "ir_group_title",
    "ir_telegram_chat_id",
    "ir_category",
    "ir_notify_tags",
    "ir_title",
    "ir_details",
    "ir_evidence",
    "ir_admin_id",
    "ir_triage_report_id",
    "ir_triage_mode",
    "ir_resolve_notes",
    "ir_resolve_evidence",
)

_TRIAGE_MODE_EDIT = "edit"
_TRIAGE_MODE_EVIDENCE = "evidence"
_TRIAGE_MODE_RESOLVE_NOTES = "resolve_notes"
_TRIAGE_MODE_RESOLVE_EVIDENCE = "resolve_evidence"


def _can_use_issue_reports(user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    if user_id in gc_mtproto_operator_telegram_user_ids():
        return True
    return is_any_club_staff(user_id)


def _cleanup_report_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_active_flow(context)
    for key in _IR_USER_KEYS:
        context.user_data.pop(key, None)


def sync_report_conv_state(
    conversation: ConversationHandler,
    update: Update,
    new_state: int | None,
) -> None:
    """Keep ConversationHandler in sync after standalone notify button handlers."""
    key = conversation._get_key(update)
    if new_state is None or new_state == ConversationHandler.END:
        conversation._conversations.pop(key, None)
    else:
        conversation._conversations[key] = new_state


async def _reply_long(message, text: str) -> None:
    chunk = 4096
    for i in range(0, len(text), chunk):
        await message.reply_text(text[i : i + chunk])


def _category_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for key in sorted(ISSUE_REPORT_CATEGORIES):
        label = CATEGORY_LABELS.get(key, key)
        row.append(InlineKeyboardButton(label, callback_data=f"ir_cat:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("CANCEL", callback_data="ir_cancel")])
    return InlineKeyboardMarkup(rows)


def _notify_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for key in sorted(NOTIFY_TAGS):
        label = NOTIFY_LABELS.get(key, key)
        prefix = "✓ " if key in selected else ""
        rows.append(
            [InlineKeyboardButton(f"{prefix}{label}", callback_data=f"ir_notify:{key}")]
        )
    rows.append(
        [
            InlineKeyboardButton("Continue", callback_data="ir_notify_done"),
            InlineKeyboardButton("CANCEL", callback_data="ir_cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("SUBMIT", callback_data="ir_submit"),
                InlineKeyboardButton("CANCEL", callback_data="ir_cancel"),
            ]
        ]
    )


def _resolve_evidence_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Skip", callback_data="ir_resolve_skip"),
                InlineKeyboardButton("Done", callback_data="ir_resolve_done"),
            ],
            [InlineKeyboardButton("CANCEL", callback_data="ir_resolve_cancel")],
        ]
    )


async def _start_resolve_flow(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    report_id: int,
    send,
) -> None:
    with get_db() as session:
        report = get_issue_report(session, report_id)
    if not report:
        await send(f"Report #{report_id} not found.")
        return
    if report.status == "resolved":
        await send(f"Report #{report_id} is already resolved.")
        return

    context.user_data["ir_triage_report_id"] = report_id
    context.user_data["ir_triage_mode"] = _TRIAGE_MODE_RESOLVE_NOTES
    context.user_data["ir_resolve_notes"] = None
    context.user_data["ir_resolve_evidence"] = []
    await send(
        f"Resolving report #{report_id}.\n\n"
        "How was this resolved? What was the solution?"
    )


async def _finish_resolve(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reply,
) -> None:
    report_id = context.user_data.get("ir_triage_report_id")
    notes = context.user_data.get("ir_resolve_notes")
    files = list(context.user_data.get("ir_resolve_evidence") or [])
    user = update.effective_user
    if not report_id or not user:
        return

    try:
        with get_db() as session:
            result = await resolve_report(
                session,
                int(report_id),
                resolved_by_telegram_user_id=user.id,
                resolution_notes=str(notes or ""),
                resolution_files=files,
            )
    except IssueReportValidationError as exc:
        await reply(str(exc))
        return

    context.user_data.pop("ir_triage_mode", None)
    context.user_data.pop("ir_triage_report_id", None)
    context.user_data.pop("ir_resolve_notes", None)
    context.user_data.pop("ir_resolve_evidence", None)
    await reply(format_resolve_result(result))


async def resolve_evidence_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    if query.data == "ir_resolve_cancel":
        context.user_data.pop("ir_triage_mode", None)
        context.user_data.pop("ir_triage_report_id", None)
        context.user_data.pop("ir_resolve_notes", None)
        context.user_data.pop("ir_resolve_evidence", None)
        await query.edit_message_text("Resolve cancelled.")
        return

    if context.user_data.get("ir_triage_mode") != _TRIAGE_MODE_RESOLVE_EVIDENCE:
        return

    async def reply(text: str) -> None:
        await query.edit_message_text(text)

    await _finish_resolve(update, context, reply=reply)


def _detail_keyboard(report_id: int, *, status: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status == "open":
        rows.append(
            [
                InlineKeyboardButton("Resolve", callback_data=f"ir_triage:resolve:{report_id}"),
                InlineKeyboardButton(
                    "Edit details", callback_data=f"ir_triage:edit:{report_id}"
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "Add evidence", callback_data=f"ir_triage:evidence:{report_id}"
                ),
            ]
        )
    rows.append([InlineKeyboardButton("Back to list", callback_data="ir_triage:list")])
    return InlineKeyboardMarkup(rows)


def _group_context_line(context: ContextTypes.DEFAULT_TYPE) -> str:
    title = context.user_data.get("ir_group_title")
    if not title:
        return ""
    return f"Group: {title}\n\n"


def _load_draft_into_user_data(
    context: ContextTypes.DEFAULT_TYPE, draft: DraftContext
) -> None:
    context.user_data["ir_draft_id"] = draft.id
    context.user_data["ir_club_id"] = draft.club_id
    context.user_data["ir_group_title"] = draft.group_title
    context.user_data["ir_telegram_chat_id"] = draft.telegram_chat_id


async def _report_group_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message and update.effective_chat and update.effective_user

    chat = update.effective_chat
    club_id = get_club_for_chat(chat.id)
    if club_id is None:
        return

    if not is_club_staff(update.effective_user.id, club_id):
        return

    try:
        await context.bot.delete_message(
            chat_id=chat.id,
            message_id=update.message.message_id,
        )
    except Exception:
        logger.warning(
            "issue_report: could not delete /escalate command chat_id=%s message_id=%s",
            chat.id,
            update.message.message_id,
            exc_info=True,
        )

    with get_db() as session:
        draft = create_draft(
            session,
            staff_telegram_user_id=update.effective_user.id,
            club_id=int(club_id),
            group_title=chat.title,
            telegram_chat_id=chat.id,
        )
        draft_id = draft.id
        group_title = draft.group_title

    await notify_staff_issue_report_draft(
        context.bot,
        staff_user_id=update.effective_user.id,
        draft_id=draft_id,
        group_title=group_title,
    )


async def _begin_dm_report_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message and update.effective_user

    context.user_data["ir_admin_id"] = update.effective_user.id
    mark_active_flow(context, "issue_report")

    with get_db() as session:
        draft = get_latest_pending_draft(
            session, staff_telegram_user_id=update.effective_user.id
        )
        draft_ctx = draft_to_context(draft) if draft else None
    if draft_ctx:
        _load_draft_into_user_data(context, draft_ctx)

    intro = _group_context_line(context)
    await update.message.reply_text(
        f"{intro}Select category:",
        reply_markup=_category_keyboard(),
    )
    return IR_CATEGORY


async def escalate_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent group kickoff — staff sends /escalate in a support group."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    chat = update.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    if not _can_use_issue_reports(update.effective_user.id):
        return

    await _report_group_stub(update, context)


async def report_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """DM-only wizard entry — staff sends /report to GG Support."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return ConversationHandler.END

    user_id = update.effective_user.id
    chat = update.effective_chat

    if chat.type != ChatType.PRIVATE:
        return ConversationHandler.END

    if not _can_use_issue_reports(user_id):
        await update.message.reply_text("You are not allowed to file issue reports.")
        return ConversationHandler.END

    _cleanup_report_flow(context)
    return await _begin_dm_report_flow(update, context)


async def draft_continue_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return ConversationHandler.END

    await query.answer()

    try:
        draft_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.edit_message_text("Invalid draft. Send /report to start again.")
        return ConversationHandler.END

    logger.info(
        "issue_report: continue draft_id=%s user_id=%s",
        draft_id,
        update.effective_user.id,
    )

    try:
        with get_db() as session:
            draft = get_pending_draft(
                session,
                draft_id,
                staff_telegram_user_id=update.effective_user.id,
            )
            draft_ctx = draft_to_context(draft) if draft else None
    except Exception:
        logger.exception(
            "issue_report: draft lookup failed draft_id=%s user_id=%s",
            draft_id,
            update.effective_user.id,
        )
        await query.edit_message_text(
            "Could not load report draft. Send /report to start again."
        )
        return ConversationHandler.END

    if not draft_ctx:
        logger.warning(
            "issue_report: draft missing or expired draft_id=%s user_id=%s",
            draft_id,
            update.effective_user.id,
        )
        await query.edit_message_text("Report draft expired. Send /report to start again.")
        return ConversationHandler.END

    _cleanup_report_flow(context)
    context.user_data["ir_admin_id"] = update.effective_user.id
    mark_active_flow(context, "issue_report")
    _load_draft_into_user_data(context, draft_ctx)

    intro = _group_context_line(context)
    try:
        await query.edit_message_text(
            f"{intro}Select category:",
            reply_markup=_category_keyboard(),
        )
    except Exception:
        logger.exception(
            "issue_report: edit_message failed draft_id=%s user_id=%s",
            draft_id,
            update.effective_user.id,
        )
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=f"{intro}Select category:",
            reply_markup=_category_keyboard(),
        )
    return IR_CATEGORY


async def draft_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END
    await query.answer()
    try:
        draft_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    with get_db() as session:
        cancel_draft(session, draft_id)
    _cleanup_report_flow(context)
    await query.edit_message_text("Issue report cancelled.")
    return ConversationHandler.END


async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        draft_id = context.user_data.get("ir_draft_id")
        if draft_id:
            with get_db() as session:
                cancel_draft(session, int(draft_id))
        _cleanup_report_flow(context)
        await query.edit_message_text("Issue report cancelled.")
    elif update.message:
        draft_id = context.user_data.get("ir_draft_id")
        if draft_id:
            with get_db() as session:
                cancel_draft(session, int(draft_id))
        _cleanup_report_flow(context)
        await update.message.reply_text("Issue report cancelled.")
    return ConversationHandler.END


async def category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("ir_cat:"):
        return IR_CATEGORY
    await query.answer()
    category = query.data.split(":", 1)[1]
    try:
        from bot.services.issue_reports import normalize_category

        category = normalize_category(category)
    except IssueReportValidationError as exc:
        await query.edit_message_text(str(exc))
        return IR_CATEGORY

    context.user_data["ir_category"] = category
    selected = default_notify_for_category(category)
    context.user_data["ir_notify_tags"] = selected
    await query.edit_message_text(
        "Who should be notified? Tap to toggle, then Continue.",
        reply_markup=_notify_keyboard(selected),
    )
    return IR_NOTIFY


async def notify_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return IR_NOTIFY
    await query.answer()

    if query.data == "ir_notify_done":
        selected = list(context.user_data.get("ir_notify_tags") or [])
        if not selected:
            await query.edit_message_text(
                "Select at least one notify target.",
                reply_markup=_notify_keyboard([]),
            )
            return IR_NOTIFY
        intro = _group_context_line(context)
        await query.edit_message_text(f"{intro}Issue title:")
        return IR_TITLE

    if not query.data.startswith("ir_notify:"):
        return IR_NOTIFY

    key = query.data.split(":", 1)[1]
    selected = list(context.user_data.get("ir_notify_tags") or [])
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    context.user_data["ir_notify_tags"] = selected
    await query.edit_message_text(
        "Who should be notified? Tap to toggle, then Continue.",
        reply_markup=_notify_keyboard(selected),
    )
    return IR_NOTIFY


async def title_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Please enter a title.")
        return IR_TITLE
    context.user_data["ir_title"] = title
    await update.message.reply_text("Details — what happened?")
    return IR_DETAILS


async def details_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    details = (update.message.text or "").strip()
    if not details:
        await update.message.reply_text("Please enter details.")
        return IR_DETAILS
    context.user_data["ir_details"] = details
    context.user_data["ir_evidence"] = []
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Skip evidence", callback_data="ir_evidence_done"),
                InlineKeyboardButton("CANCEL", callback_data="ir_cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        "Send screenshots (optional), then tap Done — or Skip.",
        reply_markup=keyboard,
    )
    return IR_EVIDENCE


async def _download_photo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> IssueReportFileInput | None:
    if not update.message or not update.message.photo:
        return None
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    buf = BytesIO()
    await tg_file.download_to_memory(out=buf)
    return IssueReportFileInput(
        filename=f"screenshot_{photo.file_unique_id}.jpg",
        content_type="image/jpeg",
        content=buf.getvalue(),
    )


async def evidence_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    files: list = list(context.user_data.get("ir_evidence") or [])
    if len(files) >= 5:
        await update.message.reply_text("Maximum 5 screenshots. Tap Done to continue.")
        return IR_EVIDENCE
    try:
        file_input = await _download_photo(update, context)
    except Exception:
        logger.exception("issue_report: photo download failed")
        await update.message.reply_text("Could not download that image. Try again.")
        return IR_EVIDENCE
    if file_input is None:
        return IR_EVIDENCE
    files.append(file_input)
    context.user_data["ir_evidence"] = files
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Done", callback_data="ir_evidence_done"),
                InlineKeyboardButton("CANCEL", callback_data="ir_cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        f"Saved ({len(files)}/5). Send more or tap Done.",
        reply_markup=keyboard,
    )
    return IR_EVIDENCE


async def evidence_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()

    category = context.user_data.get("ir_category")
    notify_tags = context.user_data.get("ir_notify_tags") or []
    title = context.user_data.get("ir_title")
    details = context.user_data.get("ir_details")
    evidence = list(context.user_data.get("ir_evidence") or [])
    att_label = f"{len(evidence)} file(s)" if evidence else "none"

    summary = "\n".join(
        [
            "Confirm report:",
            "",
            _group_context_line(context).strip(),
            f"Category: {CATEGORY_LABELS.get(category, category)}",
            f"Notify: {', '.join(NOTIFY_LABELS.get(t, t) for t in notify_tags)}",
            f"Title: {title}",
            "",
            "Details:",
            details,
            "",
            f"Evidence: {att_label}",
        ]
    ).strip()

    if query:
        await query.edit_message_text(summary, reply_markup=_confirm_keyboard())
    elif update.message:
        await update.message.reply_text(summary, reply_markup=_confirm_keyboard())
    return IR_CONFIRM


async def submit_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query:
        return IR_CONFIRM
    await query.answer()

    admin_id = context.user_data.get("ir_admin_id")
    if admin_id is None:
        await query.edit_message_text("Report expired. Send /report to start again.")
        _cleanup_report_flow(context)
        return ConversationHandler.END

    reporter_name = None
    if update.effective_user:
        reporter_name = update.effective_user.full_name or update.effective_user.username

    try:
        with get_db() as session:
            report = await create_issue_report(
                session,
                title=str(context.user_data.get("ir_title") or ""),
                description=str(context.user_data.get("ir_details") or ""),
                category=str(context.user_data.get("ir_category") or ""),
                notify_tags=list(context.user_data.get("ir_notify_tags") or []),
                reporter_name=reporter_name,
                reporter_source="telegram",
                reporter_telegram_user_id=int(admin_id),
                club_id=context.user_data.get("ir_club_id"),
                group_title=context.user_data.get("ir_group_title"),
                telegram_chat_id=context.user_data.get("ir_telegram_chat_id"),
                files=list(context.user_data.get("ir_evidence") or []),
            )
            draft_id = context.user_data.get("ir_draft_id")
            if draft_id:
                mark_draft_submitted(session, int(draft_id))
            report_id = report.id
    except IssueReportValidationError as exc:
        await query.edit_message_text(str(exc))
        return IR_CONFIRM

    _cleanup_report_flow(context)
    await query.edit_message_text(
        f"Report #{report_id} submitted. Admins notified in Slack."
    )
    return ConversationHandler.END


async def report_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        try:
            await update.message.reply_text("Issue report timed out.")
        except Exception:
            pass
    _cleanup_report_flow(context)
    return ConversationHandler.END


async def reports_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("Use /reports in a private chat with this bot.")
        return

    if not _can_use_issue_reports(update.effective_user.id):
        await update.message.reply_text("You are not allowed to view issue reports.")
        return

    args = context.args or []

    with get_db() as session:
        if not args:
            text = format_open_reports_list(list_open_reports(session))
            await _reply_long(update.message, text)
            return

        if args[0].lower() == "resolved":
            text = format_resolved_reports_list(list_resolved_reports(session))
            await _reply_long(update.message, text)
            return

        try:
            report_id = int(args[0])
        except ValueError:
            await update.message.reply_text(
                "Usage: /reports | /reports resolved | /reports ID"
            )
            return

        if len(args) >= 2 and args[1].lower() == "resolve":
            await _start_resolve_flow(
                context=context,
                report_id=report_id,
                send=update.message.reply_text,
            )
            return

        if len(args) >= 2 and args[1].lower() == "edit":
            report = get_issue_report(session, report_id)
            if not report:
                await update.message.reply_text(f"Report #{report_id} not found.")
                return
            context.user_data["ir_triage_report_id"] = report_id
            context.user_data["ir_triage_mode"] = _TRIAGE_MODE_EDIT
            await update.message.reply_text(
                f"Editing report #{report_id}. Send new details:"
            )
            return

        if len(args) >= 2 and args[1].lower() == "evidence":
            report = get_issue_report(session, report_id)
            if not report:
                await update.message.reply_text(f"Report #{report_id} not found.")
                return
            context.user_data["ir_triage_report_id"] = report_id
            context.user_data["ir_triage_mode"] = _TRIAGE_MODE_EVIDENCE
            await update.message.reply_text(
                f"Add evidence to report #{report_id}. Send photo(s), then /done"
            )
            return

        report = get_issue_report(session, report_id)
        if not report:
            await update.message.reply_text(f"Report #{report_id} not found.")
            return
        text = format_report_detail(report, session=session)
        await update.message.reply_text(
            text,
            reply_markup=_detail_keyboard(report_id, status=report.status or "open"),
        )


async def triage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    await query.answer()

    if not _can_use_issue_reports(update.effective_user.id):
        await query.edit_message_text("You are not allowed to manage issue reports.")
        return

    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "ir_triage":
        return

    action = parts[1]

    if action == "list":
        with get_db() as session:
            text = format_open_reports_list(list_open_reports(session))
        await query.edit_message_text(text)
        return

    try:
        report_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("Invalid report.")
        return

    with get_db() as session:
        if action == "resolve":
            await _start_resolve_flow(
                context=context,
                report_id=report_id,
                send=query.edit_message_text,
            )
            return

        if action == "edit":
            report = get_issue_report(session, report_id)
            if not report:
                await query.edit_message_text(f"Report #{report_id} not found.")
                return
            context.user_data["ir_triage_report_id"] = report_id
            context.user_data["ir_triage_mode"] = _TRIAGE_MODE_EDIT
            await query.edit_message_text(
                f"Editing report #{report_id}. Send new details:"
            )
            return

        if action == "evidence":
            report = get_issue_report(session, report_id)
            if not report:
                await query.edit_message_text(f"Report #{report_id} not found.")
                return
            context.user_data["ir_triage_report_id"] = report_id
            context.user_data["ir_triage_mode"] = _TRIAGE_MODE_EVIDENCE
            await query.edit_message_text(
                f"Add evidence to report #{report_id}. Send photo(s), then /done"
            )
            return


async def triage_followup_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text/photo follow-ups for /reports ID edit|evidence."""
    if not update.message or not update.effective_user:
        return
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return

    mode = context.user_data.get("ir_triage_mode")
    report_id = context.user_data.get("ir_triage_report_id")
    if not mode or not report_id:
        return

    if not _can_use_issue_reports(update.effective_user.id):
        return

    if update.message.text and update.message.text.strip().lower() == "/done":
        if mode == _TRIAGE_MODE_EVIDENCE:
            context.user_data.pop("ir_triage_mode", None)
            context.user_data.pop("ir_triage_report_id", None)
            context.user_data.pop("ir_triage_evidence", None)
            await update.message.reply_text(f"Evidence saved for report #{report_id}.")
        return

    if mode == _TRIAGE_MODE_RESOLVE_NOTES and update.message.text:
        notes = (update.message.text or "").strip()
        if not notes:
            await update.message.reply_text("Please describe how the issue was resolved.")
            return
        context.user_data["ir_resolve_notes"] = notes
        context.user_data["ir_triage_mode"] = _TRIAGE_MODE_RESOLVE_EVIDENCE
        await update.message.reply_text(
            "Send resolution screenshots (optional), then tap Done — or Skip.",
            reply_markup=_resolve_evidence_keyboard(),
        )
        return

    if mode == _TRIAGE_MODE_RESOLVE_EVIDENCE and update.message.photo:
        files: list = list(context.user_data.get("ir_resolve_evidence") or [])
        if len(files) >= 5:
            await update.message.reply_text(
                "Maximum 5 screenshots. Tap Done to finish.",
                reply_markup=_resolve_evidence_keyboard(),
            )
            return
        try:
            file_input = await _download_photo(update, context)
        except Exception:
            logger.exception("issue_report: resolution photo download failed")
            await update.message.reply_text("Could not download that image. Try again.")
            return
        if file_input is None:
            return
        files.append(file_input)
        context.user_data["ir_resolve_evidence"] = files
        await update.message.reply_text(
            f"Saved ({len(files)}/5). Send more or tap Done.",
            reply_markup=_resolve_evidence_keyboard(),
        )
        return

    if mode == _TRIAGE_MODE_EDIT and update.message.text:
        try:
            with get_db() as session:
                update_report_details(
                    session, int(report_id), description=update.message.text
                )
        except IssueReportValidationError as exc:
            await update.message.reply_text(str(exc))
            return
        context.user_data.pop("ir_triage_mode", None)
        context.user_data.pop("ir_triage_report_id", None)
        await update.message.reply_text(f"Report #{report_id} details updated.")
        return

    if mode == _TRIAGE_MODE_EVIDENCE and update.message.photo:
        try:
            file_input = await _download_photo(update, context)
            if file_input is None:
                return
            with get_db() as session:
                await add_report_evidence(session, int(report_id), [file_input])
        except IssueReportValidationError as exc:
            await update.message.reply_text(str(exc))
            return
        await update.message.reply_text(
            f"Evidence added to report #{report_id}. Send more or /done."
        )


def issue_report_flow_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return any(k in context.user_data for k in _IR_USER_KEYS if k.startswith("ir_"))


async def issue_report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await report_cancel(update, context)


_REPORT_CANCEL = CommandHandler("cancel", issue_report_cancel)
_REPORT_CANCEL_CB = CallbackQueryHandler(report_cancel, pattern=r"^ir_cancel$")


def get_report_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("report", report_entry, filters=filters.ChatType.PRIVATE),
        ],
        states={
            IR_CATEGORY: [
                CallbackQueryHandler(category_chosen, pattern=r"^ir_cat:"),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            IR_NOTIFY: [
                CallbackQueryHandler(notify_toggle, pattern=r"^ir_notify"),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            IR_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, title_received),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            IR_DETAILS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, details_received),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            IR_EVIDENCE: [
                MessageHandler(filters.PHOTO, evidence_photo),
                CallbackQueryHandler(evidence_done, pattern=r"^ir_evidence_done$"),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            IR_CONFIRM: [
                CallbackQueryHandler(submit_report, pattern=r"^ir_submit$"),
                _REPORT_CANCEL_CB,
                _REPORT_CANCEL,
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, report_timeout),
            ],
        },
        fallbacks=[_REPORT_CANCEL_CB, _REPORT_CANCEL],
        conversation_timeout=600,
        name="issue_report_conv",
        per_chat=False,
        per_user=True,
        allow_reentry=True,
    )


def register_issue_report_handlers(app) -> None:
    """Register escalate, draft cancel, wizard, triage, and follow-up handlers."""
    conv = get_report_conversation_handler()

    async def on_draft_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            result = await draft_continue_entry(update, context)
            sync_report_conv_state(conv, update, result)
        except Exception:
            logger.exception("issue_report: on_draft_continue failed")
            query = update.callback_query
            if query:
                try:
                    await query.edit_message_text(
                        "Could not continue report. Send /report to try again."
                    )
                except Exception:
                    pass

    app.add_handler(CommandHandler("escalate", escalate_entry))
    app.add_handler(CallbackQueryHandler(on_draft_continue, pattern=r"^ir_draft:\d+$"))
    app.add_handler(
        CallbackQueryHandler(draft_cancel_callback, pattern=r"^ir_draft_cancel:\d+$")
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("reports", reports_handler))
    app.add_handler(CallbackQueryHandler(triage_callback, pattern=r"^ir_triage:"))
    app.add_handler(
        CallbackQueryHandler(
            resolve_evidence_callback,
            pattern=r"^ir_resolve_(skip|done|cancel)$",
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
            triage_followup_message,
            block=False,
        )
    )
