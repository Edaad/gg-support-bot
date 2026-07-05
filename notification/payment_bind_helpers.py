"""Shared helpers for payment notification text and ingest auto-bind."""

from __future__ import annotations

import html
from typing import Callable, Optional

from bot.services.payment_bind_candidates import CandidateGroup
from notification.formatting import AMBIGUOUS_GROUP_CHAT_LINE


def format_ambiguous_candidate_lines(candidates: list[CandidateGroup]) -> list[str]:
    lines: list[str] = []
    for candidate in candidates:
        lines.append(f"• {html.escape(candidate.group_title, quote=False)}")
    return lines


def inject_ambiguous_group_chat_line(text: str, candidates: list[CandidateGroup]) -> str:
    """Replace unbound Group Chat line with ambiguous line + candidate list."""
    if not candidates:
        return text
    lines = text.split("\n")
    out: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.startswith("Group Chat:"):
            out.append(AMBIGUOUS_GROUP_CHAT_LINE)
            out.extend(format_ambiguous_candidate_lines(candidates))
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(2, AMBIGUOUS_GROUP_CHAT_LINE)
        out[3:3] = format_ambiguous_candidate_lines(candidates)
    return "\n".join(out)


def format_pending_confirm_group_chat_line(group_title: str) -> str:
    safe = html.escape((group_title or "").strip(), quote=False)
    return f"Group Chat: {safe} — confirm below"


def inject_pending_confirm_group_line(text: str, group_title: str) -> str:
    """Replace ambiguous picker section with the selected group pending confirm."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    pending_line = format_pending_confirm_group_chat_line(group_title)
    while i < len(lines):
        line = lines[i]
        if line.startswith("Group Chat:") and (
            "select group below" in line or "confirm below" in line
        ):
            out.append(pending_line)
            i += 1
            while i < len(lines) and lines[i].startswith("• "):
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def auto_bind_from_candidates(
    candidates: list[CandidateGroup],
) -> CandidateGroup | None:
    if len(candidates) == 1:
        return candidates[0]
    return None


FORMATters: dict[str, Callable[..., str]] = {}


def register_formatters() -> None:
    if FORMATters:
        return
    from bot.services.cashapp_payments import format_notification_text as cashapp_fmt
    from bot.services.crypto_payments import format_notification_text as crypto_fmt
    from bot.services.paypal_payments import format_notification_text as paypal_fmt
    from bot.services.venmo_payments import format_notification_text as venmo_fmt
    from bot.services.zelle_payments import format_notification_text as zelle_fmt

    FORMATters.update(
        {
            "venmo": venmo_fmt,
            "zelle": zelle_fmt,
            "cashapp": cashapp_fmt,
            "paypal": paypal_fmt,
            "crypto": crypto_fmt,
        }
    )


def format_payment_notification(
    method_slug: str,
    payment: object,
    *,
    group_title: Optional[str] = None,
    group_chat_url: Optional[str] = None,
    ambiguous_candidates: list[CandidateGroup] | None = None,
    club_id: Optional[int] = None,
    telegram_chat_id: Optional[int] = None,
    auto_bound: bool = False,
    goods_or_services: bool = False,
) -> str:
    register_formatters()
    fmt = FORMATters[method_slug]
    text = fmt(
        payment,
        group_title=group_title,
        group_chat_url=group_chat_url,
    )
    if ambiguous_candidates and len(ambiguous_candidates) > 1 and not group_title:
        text = inject_ambiguous_group_chat_line(text, ambiguous_candidates)
    from bot.services.payment_auto_deposit import append_creator_club_staff_footer

    resolved_club_id = club_id
    if resolved_club_id is None:
        resolved_club_id = getattr(payment, "club_id", None)
    resolved_chat_id = telegram_chat_id
    if resolved_chat_id is None:
        resolved_chat_id = getattr(payment, "telegram_chat_id", None)
    return append_creator_club_staff_footer(
        text,
        club_id=int(resolved_club_id) if resolved_club_id is not None else None,
        telegram_chat_id=int(resolved_chat_id) if resolved_chat_id is not None else None,
        auto_bound=auto_bound,
        goods_or_services=goods_or_services,
    )


def notification_keyboard_kind(
    *,
    notif_markup: dict | None,
    setup_blocked: bool = False,
    ambiguous_candidate_count: int = 0,
) -> str | None:
    if not notif_markup:
        return None
    if setup_blocked:
        return "setup_blocked_reassign_add"
    if ambiguous_candidate_count > 1:
        return "ambiguous_picker"
    return "inline_keyboard"


def log_ingest_bind_delivery(
    *,
    method_slug: str,
    payment_id: int,
    identity_label: str,
    candidate_count: int,
    auto_bound: bool,
    bound_chat_id: int | None,
    bound_title: str | None,
    setup_blocked: bool,
    setup_target_chat_id: int | None,
    notif_markup: dict | None,
    notification_chat_id: int,
    notification_message_id: int,
) -> None:
    from bot.services.payment_bind_logging import log_ingest_outcome, log_notification_post

    keyboard_kind = notification_keyboard_kind(
        notif_markup=notif_markup,
        setup_blocked=setup_blocked,
        ambiguous_candidate_count=candidate_count,
    )
    log_ingest_outcome(
        method_slug=method_slug,
        payment_id=payment_id,
        identity_label=identity_label,
        candidate_count=candidate_count,
        auto_bound=auto_bound,
        bound_chat_id=bound_chat_id,
        bound_title=bound_title,
        setup_blocked=setup_blocked,
        setup_target_chat_id=setup_target_chat_id,
        notification_keyboard=keyboard_kind,
    )
    log_notification_post(
        method_slug=method_slug,
        payment_id=payment_id,
        notification_chat_id=notification_chat_id,
        notification_message_id=notification_message_id,
        has_keyboard=notif_markup is not None,
        keyboard_kind=keyboard_kind,
    )
