"""Refund-required gates for Venmo and Zelle ingest (G&S, cents, banned memos)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from db.connection import get_db
from db.models import PaymentMethodBindAttempt, VenmoPayment, ZellePayment

logger = logging.getLogger(__name__)

REASON_GOODS_SERVICES = "goods_and_services"
REASON_FRACTIONAL_AMOUNT = "fractional_amount"
REASON_BANNED_MEMO = "banned_memo"

_GATED_METHODS = frozenset({"venmo", "zelle"})

# Longer phrases first so "Club GG" is reported alongside the short tokens it contains.
_BANNED_MEMO_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("Club GG", r"club\s*gg"),
        ("Round Table", r"round\s*table"),
        ("Pure Poker", r"pure\s*poker"),
        ("buy-in", r"\bbuy[\s\-]?ins?\b"),
        ("gambling", r"\bgambling\b"),
        ("poker", r"\bpoker\b"),
        ("chips", r"\bchips\b"),
        ("club", r"\bclub\b"),
        ("GG", r"\bgg\b"),
        ("RT", r"\brt\b"),
    )
)


@dataclass(frozen=True)
class RefundGate:
    reasons: tuple[str, ...] = ()
    banned_hits: tuple[str, ...] = ()

    @property
    def requires_refund(self) -> bool:
        return bool(self.reasons)


def format_amount_exact(amount_cents: int) -> str:
    cents = int(amount_cents)
    dollars = Decimal(cents) / Decimal(100)
    if cents % 100 == 0:
        return f"${int(dollars):,}"
    return f"${dollars:,.2f}"


def find_banned_memo_hits(memo: str | None) -> tuple[str, ...]:
    text = (memo or "").strip()
    if not text:
        return ()
    hits: list[str] = []
    seen: set[str] = set()
    for label, pattern in _BANNED_MEMO_RULES:
        if label in seen:
            continue
        if pattern.search(text):
            hits.append(label)
            seen.add(label)
    return tuple(hits)


def evaluate_refund_gate(
    *,
    amount_cents: int,
    memo: str | None = None,
    goods_or_services: bool = False,
    is_first_time_setup_bind: bool = False,
    method_slug: str = "venmo",
) -> RefundGate:
    """Return refund reasons for a Venmo/Zelle payment.

    Fractional (non-whole-dollar) amounts are allowed only when this payment
    completed first-time method setup (special amount or memo code).
    Banned memos always require a refund, including first-time setup.
    """
    slug = (method_slug or "").strip().lower()
    if slug not in _GATED_METHODS:
        return RefundGate()

    reasons: list[str] = []
    banned_hits = find_banned_memo_hits(memo)
    if goods_or_services and slug == "venmo":
        reasons.append(REASON_GOODS_SERVICES)
    if int(amount_cents) % 100 != 0 and not is_first_time_setup_bind:
        reasons.append(REASON_FRACTIONAL_AMOUNT)
    if banned_hits:
        reasons.append(REASON_BANNED_MEMO)
    return RefundGate(reasons=tuple(reasons), banned_hits=banned_hits)


def completed_first_time_setup_bind(
    method_slug: str,
    payment_id: int | None,
) -> bool:
    slug = (method_slug or "").strip().lower()
    if slug not in _GATED_METHODS or payment_id is None:
        return False
    column = (
        PaymentMethodBindAttempt.venmo_payment_id
        if slug == "venmo"
        else PaymentMethodBindAttempt.zelle_payment_id
    )
    try:
        with get_db() as session:
            row = (
                session.query(PaymentMethodBindAttempt.id)
                .filter(
                    column == int(payment_id),
                    PaymentMethodBindAttempt.status == "succeeded",
                )
                .first()
            )
        return row is not None
    except Exception:
        logger.exception(
            "payment_refund_gate: setup-bind lookup failed method=%s payment_id=%s",
            slug,
            payment_id,
        )
        return False


def refund_gate_for_payment(
    method_slug: str,
    payment: object,
    *,
    is_first_time_setup_bind: bool | None = None,
) -> RefundGate:
    slug = (method_slug or "").strip().lower()
    payment_id = getattr(payment, "id", None)
    first_time = is_first_time_setup_bind
    if first_time is None:
        first_time = completed_first_time_setup_bind(
            slug,
            int(payment_id) if payment_id is not None else None,
        )
    return evaluate_refund_gate(
        amount_cents=int(getattr(payment, "amount_cents", 0) or 0),
        memo=getattr(payment, "memo", None),
        goods_or_services=bool(getattr(payment, "goods_or_services", False)),
        is_first_time_setup_bind=bool(first_time),
        method_slug=slug,
    )


def inject_refund_banner(text: str, gate: RefundGate) -> str:
    extra = staff_refund_banner_lines(gate)
    if not extra:
        return text
    lines = text.split("\n")
    stripped = [
        line
        for line in lines
        if "DO NOT ADD" not in line and not line.startswith("• Goods")
        and not line.startswith("• Non-whole")
        and not line.startswith("• Banned memo")
        and line != "⚠️ <b>DO NOT ADD</b> — refund required"
    ]
    insert_at = 0
    for i, line in enumerate(stripped):
        if "Payment Notification" in line:
            insert_at = i + 1
            break
    stripped[insert_at:insert_at] = extra
    return "\n".join(stripped)


def staff_refund_banner_lines(gate: RefundGate) -> list[str]:
    if not gate.requires_refund:
        return []
    lines = ["⚠️ <b>DO NOT ADD</b> — refund required"]
    for reason in gate.reasons:
        if reason == REASON_GOODS_SERVICES:
            lines.append("• Goods & Services")
        elif reason == REASON_FRACTIONAL_AMOUNT:
            lines.append("• Non-whole-dollar amount")
        elif reason == REASON_BANNED_MEMO:
            hits = ", ".join(gate.banned_hits)
            lines.append(f"• Banned memo ({hits})" if hits else "• Banned memo")
    return lines


def _format_player_dollars(amount_cents: int) -> str:
    dollars = int(
        (Decimal(amount_cents) / Decimal(100)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    return f"${dollars:,}"


def format_player_refund_message(
    amount_cents: int,
    gate: RefundGate,
    *,
    method_slug: str = "venmo",
) -> str:
    amount = _format_player_dollars(amount_cents)
    slug = (method_slug or "").strip().lower()
    if gate.reasons == (REASON_GOODS_SERVICES,):
        return (
            f"We have received your payment for {amount}. "
            "Since it was sent as Goods & Services, we will refund it — "
            "please resend as Friends & Family."
        )

    parts = [
        f"We have received your payment for {amount}. We will refund it.",
    ]
    please: list[str] = []
    if slug == "venmo":
        please.append("resend as Friends & Family")
    if REASON_FRACTIONAL_AMOUNT in gate.reasons:
        please.append("use a whole-dollar amount")
    if REASON_BANNED_MEMO in gate.reasons:
        please.append(
            "leave the memo blank or unrelated (no poker, chips, or club names)"
        )
    if please:
        if len(please) == 1:
            parts.append(f"Please {please[0]}.")
        elif len(please) == 2:
            parts.append(f"Please {please[0]} and {please[1]}.")
        else:
            parts.append(
                "Please " + ", ".join(please[:-1]) + f", and {please[-1]}."
            )
    return " ".join(parts)


def _method_label(method_slug: str) -> str:
    slug = (method_slug or "").strip().lower()
    return {"venmo": "Venmo", "zelle": "Zelle"}.get(slug, slug.title() or "Payment")


def _method_handle(payment: object, method_slug: str) -> str:
    slug = (method_slug or "").strip().lower()
    if slug == "zelle":
        return str(getattr(payment, "zelle_recipient", "") or "")
    handle = str(getattr(payment, "venmo_handle", "") or "")
    if handle and not handle.startswith("@"):
        return f"@{handle.lstrip('@')}"
    return handle


def format_refund_issue_report_title(
    payment: object,
    gate: RefundGate,
    *,
    method_slug: str,
) -> str:
    amount = format_amount_exact(int(payment.amount_cents))
    payer = getattr(payment, "payer_name", "") or ""
    bits: list[str] = []
    if REASON_GOODS_SERVICES in gate.reasons:
        bits.append("G&S")
    if REASON_FRACTIONAL_AMOUNT in gate.reasons:
        bits.append("non-whole amount")
    if REASON_BANNED_MEMO in gate.reasons:
        bits.append("banned memo")
    reason_label = ", ".join(bits) if bits else "refund"
    return f"{_method_label(method_slug)} {reason_label} — {payer} {amount}"


def format_refund_issue_report_description(
    payment: object,
    gate: RefundGate,
    *,
    method_slug: str,
    group_title: Optional[str] = None,
    notification_chat_id: Optional[int] = None,
    notification_message_id: Optional[int] = None,
) -> str:
    amount = format_amount_exact(int(payment.amount_cents))
    bound_label = group_title or "(unbound — bind via notification reply)"
    lines = [
        "DO NOT ADD — refund required.",
        "",
    ]
    if REASON_GOODS_SERVICES in gate.reasons:
        lines.append("Venmo payment was sent as Goods & Services.")
    if REASON_FRACTIONAL_AMOUNT in gate.reasons:
        lines.append("Amount is not a whole-dollar value.")
    if REASON_BANNED_MEMO in gate.reasons:
        hits = ", ".join(gate.banned_hits) or "banned keyword"
        lines.append(f"Memo contains banned keyword(s): {hits}.")
    lines.extend(
        [
            "",
            f"Payment ID: {payment.id}",
            f"Payer: {getattr(payment, 'payer_name', '')}",
            f"Amount: {amount}",
            f"Method: {_method_handle(payment, method_slug)}",
            f"Group: {bound_label}",
        ]
    )
    memo = (getattr(payment, "memo", None) or "").strip()
    if memo:
        lines.append(f"Memo: {memo}")
    if notification_chat_id is not None and notification_message_id is not None:
        lines.append(
            f"Staff notification: chat_id={notification_chat_id} "
            f"message_id={notification_message_id}"
        )
    return "\n".join(lines)


async def maybe_create_payment_refund_issue_report(
    method_slug: str,
    payment: object | int,
    gate: RefundGate | None = None,
    *,
    group_title: Optional[str] = None,
    notification_chat_id: Optional[int] = None,
    notification_message_id: Optional[int] = None,
    is_first_time_setup_bind: bool | None = None,
) -> None:
    slug = (method_slug or "").strip().lower()
    if slug not in _GATED_METHODS:
        return
    model = VenmoPayment if slug == "venmo" else ZellePayment
    payment_id = int(payment if isinstance(payment, int) else payment.id)
    reporter_source = "venmo_ingest" if slug == "venmo" else "zelle_ingest"
    try:
        from bot.services.issue_reports import create_issue_report

        with get_db() as session:
            row = session.query(model).filter_by(id=payment_id).one()
            resolved = gate or refund_gate_for_payment(
                slug,
                row,
                is_first_time_setup_bind=is_first_time_setup_bind,
            )
            if not resolved.requires_refund:
                return
            await create_issue_report(
                session,
                title=format_refund_issue_report_title(
                    row, resolved, method_slug=slug
                ),
                description=format_refund_issue_report_description(
                    row,
                    resolved,
                    method_slug=slug,
                    group_title=group_title,
                    notification_chat_id=notification_chat_id,
                    notification_message_id=notification_message_id,
                ),
                category="deposit",
                notify_tags=["head_admin"],
                reporter_name="GG Support Bot",
                reporter_source=reporter_source,
                club_id=int(row.club_id) if row.club_id is not None else None,
                group_title=group_title,
                telegram_chat_id=int(row.telegram_chat_id)
                if row.telegram_chat_id is not None
                else None,
            )
        logger.info(
            "payment_refund_gate: issue report created method=%s payment_id=%s "
            "reasons=%s",
            slug,
            payment_id,
            resolved.reasons,
        )
    except Exception:
        logger.exception(
            "payment_refund_gate: issue report failed method=%s payment_id=%s",
            slug,
            payment_id,
        )
