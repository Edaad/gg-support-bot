"""One-call operational overview composed from the other domains."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func

from db.models import (
    CashAppPayment,
    ClubPaymentTierVariant,
    CryptoPayment,
    DepositMethodAlert,
    EscalationEvent,
    Expense,
    GroupChatTicket,
    ManualDepositRequest,
    PayPalPayment,
    VenmoPayment,
    ZellePayment,
)
from mcp_server.dates import today_et, utc_now
from mcp_server.db import read_session
from mcp_server.envelope import single
from mcp_server.errors import translate_errors
from mcp_server.tools._common import READ_ONLY, club_ref, resolve_club

_RAILS = {
    "venmo": VenmoPayment,
    "zelle": ZellePayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "crypto": CryptoPayment,
}


def _safe(notes: list[str], label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one slow/broken part must not fail all
        notes.append(f"{label}: unavailable ({exc.__class__.__name__})")
        return None


@translate_errors
def get_dashboard_overview(
    club: str | None = None, days: int = 7, include_pii: bool = False
) -> dict[str, Any]:
    """Headline counts an operator checks first: unbound ingested payments per
    rail (last `days` days, test rows excluded), active cashouts, Pool Pay
    requests awaiting trade-record checks, pending expenses, active deposit
    alerts, paused variants, yesterday's tickets by category and escalations in
    the last 24 h. Each section degrades to a note if it fails; drill down with
    the domain tools."""
    days = max(1, min(int(days), 90))
    notes: list[str] = []
    since = utc_now() - timedelta(days=days)
    yesterday = today_et() - timedelta(days=1)
    with read_session() as session:
        club_row = resolve_club(session, club)
        club_id = int(club_row.id) if club_row is not None else None

        def unbound() -> dict[str, int]:
            out: dict[str, int] = {}
            for rail, model in _RAILS.items():
                q = session.query(func.count(model.id)).filter(
                    model.telegram_chat_id.is_(None),
                    model.is_test == False,  # noqa: E712
                    model.created_at >= since,
                )
                if club_id is not None:
                    q = q.filter(model.club_id == club_id)
                out[rail] = int(q.scalar() or 0)
            return out

        def active_cashouts() -> dict[str, Any]:
            from bot.services.staff_cashout_records import list_staff_cashout_records

            _items, total = list_staff_cashout_records(
                club_id=club_id, status="active", limit=1, offset=0
            )
            return {"count": int(total)}

        def unchecked_pool_pay() -> dict[str, Any]:
            q = session.query(
                func.count(ManualDepositRequest.id),
                func.coalesce(func.sum(ManualDepositRequest.amount), 0),
            ).filter(ManualDepositRequest.trade_record_checked == False)  # noqa: E712
            if club_id is not None:
                q = q.filter(ManualDepositRequest.club_id == club_id)
            count, total = q.one()
            return {"count": int(count or 0), "total_amount": total}

        def pending_expenses() -> dict[str, Any]:
            q = session.query(
                func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0)
            ).filter(Expense.pending == True)  # noqa: E712
            if club_id is not None:
                q = q.filter(Expense.club_id == club_id)
            count, total = q.one()
            return {"count": int(count or 0), "total_amount": total}

        def active_alerts() -> int:
            return int(
                session.query(func.count(DepositMethodAlert.id))
                .filter(DepositMethodAlert.is_active == True)  # noqa: E712
                .scalar()
                or 0
            )

        def paused_variants() -> int:
            return int(
                session.query(func.count(ClubPaymentTierVariant.id))
                .filter(ClubPaymentTierVariant.paused_until > utc_now())
                .scalar()
                or 0
            )

        def tickets_yesterday() -> dict[str, Any]:
            q = session.query(
                GroupChatTicket.category, func.count(GroupChatTicket.id)
            ).filter(GroupChatTicket.activity_date == yesterday)
            if club_id is not None:
                q = q.filter(GroupChatTicket.club_id == club_id)
            rows = q.group_by(GroupChatTicket.category).all()
            return {
                "activity_date": yesterday,
                "by_category": {str(c): int(n) for c, n in rows},
                "total": int(sum(n for _c, n in rows)),
            }

        def escalations_24h() -> int:
            q = session.query(func.count(EscalationEvent.id)).filter(
                EscalationEvent.created_at >= utc_now() - timedelta(hours=24)
            )
            if club_id is not None:
                q = q.filter(EscalationEvent.club_id == club_id)
            return int(q.scalar() or 0)

        overview = {
            "club": club_ref(club_row),
            "window_days": days,
            "unbound_payments": _safe(notes, "unbound_payments", unbound),
            "active_cashouts": _safe(notes, "active_cashouts", active_cashouts),
            "unchecked_pool_pay": _safe(
                notes, "unchecked_pool_pay", unchecked_pool_pay
            ),
            "pending_expenses": _safe(notes, "pending_expenses", pending_expenses),
            "active_deposit_alerts": _safe(
                notes, "active_deposit_alerts", active_alerts
            ),
            "paused_variants": _safe(notes, "paused_variants", paused_variants),
            "tickets_yesterday": _safe(notes, "tickets_yesterday", tickets_yesterday),
            "escalations_last_24h": _safe(
                notes, "escalations_last_24h", escalations_24h
            ),
        }
    notes.extend(
        [
            "unbound_payments counts ingested rows with no group in the window",
            "cashout/pool-pay/expense amounts are dollars",
        ]
    )
    return single(overview, include_pii=include_pii, notes=notes)


def register(mcp: MCPServer) -> None:
    mcp.tool(name="get_dashboard_overview", annotations=READ_ONLY)(
        get_dashboard_overview
    )
