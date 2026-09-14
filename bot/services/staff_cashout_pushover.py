"""Method-filtered Pushover fan-out for staff cashout create / overdue alerts."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from sqlalchemy.orm import joinedload

from bot.services.staff_cashout_records import compute_ledger
from bot.services.staff_cashout_slack_reminders import (
    cashout_record_dashboard_url,
    format_cashout_pushover_create,
    format_cashout_pushover_reminder,
    get_slack_reminder_enabled,
)
from db.connection import get_db
from db.models import StaffCashoutNotifyRecipient, StaffCashoutRecord

logger = logging.getLogger(__name__)

FIXED_RAILS: tuple[str, ...] = ("venmo", "zelle", "crypto", "cashapp", "paypal")
FIXED_RAIL_SET = frozenset(FIXED_RAILS)
RAIL_LABELS: dict[str, str] = {
    "venmo": "Venmo",
    "zelle": "Zelle",
    "crypto": "Crypto",
    "cashapp": "Cash App",
    "paypal": "PayPal",
    "other": "Other",
}
OTHER_RAIL = "other"

SOURCE_CREATE = "cashout_create_pushover"
SOURCE_OVERDUE = "cashout_slack_reminder"

CREATE_TITLE_TMPL = "New {method} Cashout"
OVERDUE_TITLE = "URGENT CASHOUT"


def _alnum_lower(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def rails_for_display_names(names: Iterable[str | None]) -> set[str]:
    """Map method_display_name values to fixed rails; unmatched/empty → other."""
    cleaned = [(n or "").strip() for n in names]
    if not cleaned or all(not n for n in cleaned):
        return {OTHER_RAIL}

    found: set[str] = set()
    matched_any = False
    for name in cleaned:
        if not name:
            found.add(OTHER_RAIL)
            continue
        norm = _alnum_lower(name)
        hit = False
        for rail in FIXED_RAILS:
            if rail in norm:
                found.add(rail)
                hit = True
                matched_any = True
        if not hit:
            found.add(OTHER_RAIL)
    if not matched_any and not found:
        return {OTHER_RAIL}
    return found or {OTHER_RAIL}


def _normalize_methods(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        slug = str(item or "").strip().lower()
        if slug not in FIXED_RAIL_SET or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def recipients_for_rails(rails: set[str]) -> list[dict[str, Any]]:
    """Active recipients with a key who should receive for these rails."""
    with get_db() as session:
        rows = (
            session.query(StaffCashoutNotifyRecipient)
            .order_by(StaffCashoutNotifyRecipient.id.asc())
            .all()
        )
        result: list[dict[str, Any]] = []
        notify_all = OTHER_RAIL in rails
        for row in rows:
            key = (row.pushover_user_key or "").strip()
            if not key:
                continue
            methods = _normalize_methods(row.methods)
            if not notify_all and not (set(methods) & rails):
                continue
            result.append(
                {
                    "id": int(row.id),
                    "name": row.name,
                    "pushover_user_key": key,
                    "methods": methods,
                }
            )
        return result


def list_notify_recipients() -> list[dict[str, Any]]:
    with get_db() as session:
        rows = (
            session.query(StaffCashoutNotifyRecipient)
            .order_by(StaffCashoutNotifyRecipient.id.asc())
            .all()
        )
        return [
            {
                "id": int(row.id),
                "name": row.name,
                "pushover_user_key": row.pushover_user_key,
                "methods": _normalize_methods(row.methods),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ]


def create_notify_recipient(
    *,
    name: str,
    pushover_user_key: str,
    methods: list[str] | None = None,
) -> dict[str, Any]:
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Name is required")
    key = (pushover_user_key or "").strip()
    if not key:
        raise ValueError("Pushover user key is required")
    method_list = _normalize_methods(methods or [])
    with get_db() as session:
        row = StaffCashoutNotifyRecipient(
            name=clean_name[:100],
            pushover_user_key=key[:64],
            methods=method_list,
        )
        session.add(row)
        session.flush()
        return {
            "id": int(row.id),
            "name": row.name,
            "pushover_user_key": row.pushover_user_key,
            "methods": list(method_list),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def update_notify_recipient(
    recipient_id: int,
    *,
    name: str | None = None,
    pushover_user_key: str | None = None,
    methods: list[str] | None = None,
) -> dict[str, Any] | None:
    with get_db() as session:
        row = session.get(StaffCashoutNotifyRecipient, int(recipient_id))
        if row is None:
            return None
        if name is not None:
            clean_name = (name or "").strip()
            if not clean_name:
                raise ValueError("Name is required")
            row.name = clean_name[:100]
        if pushover_user_key is not None:
            key = (pushover_user_key or "").strip()
            if not key:
                raise ValueError("Pushover user key is required")
            row.pushover_user_key = key[:64]
        if methods is not None:
            row.methods = _normalize_methods(methods)
        session.flush()
        return {
            "id": int(row.id),
            "name": row.name,
            "pushover_user_key": row.pushover_user_key,
            "methods": _normalize_methods(row.methods),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def delete_notify_recipient(recipient_id: int) -> bool:
    with get_db() as session:
        row = session.get(StaffCashoutNotifyRecipient, int(recipient_id))
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True


def primary_method_label(method_names: Iterable[str | None]) -> str:
    """Human method label for titles/tags from the first payment display name."""
    for raw in method_names:
        name = (raw or "").strip()
        if not name:
            continue
        rails = rails_for_display_names([name])
        known = sorted(rails & FIXED_RAIL_SET)
        if len(known) == 1:
            return RAIL_LABELS[known[0]]
        if known:
            return RAIL_LABELS[known[0]]
        return name
    return RAIL_LABELS[OTHER_RAIL]


def _load_record_notify_context(record_id: int) -> dict[str, Any] | None:
    with get_db() as session:
        record = (
            session.query(StaffCashoutRecord)
            .options(
                joinedload(StaffCashoutRecord.payments),
                joinedload(StaffCashoutRecord.money_sends),
            )
            .filter(StaffCashoutRecord.id == int(record_id))
            .first()
        )
        if record is None:
            return None
        names = [
            (p.method_display_name or "").strip()
            for p in (record.payments or [])
        ]
        sends = [
            {"amount": s.amount, "created_at": s.created_at}
            for s in (record.money_sends or [])
        ]
        ledger = compute_ledger(True, record.amount, sends)
        method_label = primary_method_label(names)
        return {
            "id": int(record.id),
            "group_title": record.group_title or "",
            "amount": record.amount,
            "remaining": ledger["remaining"],
            "method_names": names,
            "method_label": method_label,
            "rails": rails_for_display_names(names),
        }


def _title_and_message_for_context(
    ctx: dict[str, Any], *, source: str
) -> tuple[str, str]:
    method_label = str(ctx.get("method_label") or RAIL_LABELS[OTHER_RAIL])
    group_title = str(ctx.get("group_title") or "")
    if source == SOURCE_CREATE:
        return (
            CREATE_TITLE_TMPL.format(method=method_label),
            format_cashout_pushover_create(
                group_title=group_title,
                amount=ctx.get("amount"),
                method_label=method_label,
            ),
        )
    return (
        OVERDUE_TITLE,
        format_cashout_pushover_reminder(
            group_title=group_title,
            remaining=ctx.get("remaining"),
            method_label=method_label,
        ),
    )


def notify_cashout_pushover_sync(
    record_id: int,
    *,
    title: str | None = None,
    source: str = SOURCE_CREATE,
    require_master_toggle: bool = True,
) -> int:
    """Fan-out sync Pushover. Returns count of successful sends. Never raises."""
    try:
        if require_master_toggle and not get_slack_reminder_enabled():
            return 0
        ctx = _load_record_notify_context(record_id)
        if ctx is None:
            return 0
        targets = recipients_for_rails(ctx["rails"])
        if not targets:
            return 0
        from bot.services.pushover_notify import notify_pushover_sync

        computed_title, message = _title_and_message_for_context(ctx, source=source)
        push_title = (title or "").strip() or computed_title
        url = cashout_record_dashboard_url(int(record_id))
        sent = 0
        for person in targets:
            ok = notify_pushover_sync(
                message,
                user=person["pushover_user_key"],
                title=push_title,
                url=url,
                url_title="Open cashout",
                priority=1,
                source=source,
            )
            if ok:
                sent += 1
            else:
                logger.warning(
                    "cashout_pushover: sync failed record_id=%s recipient_id=%s",
                    record_id,
                    person.get("id"),
                )
        return sent
    except Exception:
        logger.exception(
            "cashout_pushover: sync fan-out failed record_id=%s", record_id
        )
        return 0


async def notify_cashout_pushover_async(
    record_id: int,
    *,
    title: str | None = None,
    source: str = SOURCE_OVERDUE,
    require_master_toggle: bool = False,
) -> int:
    """Fan-out async Pushover. Returns count of successful sends. Never raises."""
    try:
        if require_master_toggle and not get_slack_reminder_enabled():
            return 0
        ctx = _load_record_notify_context(record_id)
        if ctx is None:
            return 0
        targets = recipients_for_rails(ctx["rails"])
        if not targets:
            return 0
        from bot.services.pushover_notify import notify_pushover

        computed_title, message = _title_and_message_for_context(ctx, source=source)
        push_title = (title or "").strip() or computed_title
        url = cashout_record_dashboard_url(int(record_id))
        sent = 0
        for person in targets:
            ok = await notify_pushover(
                message,
                user=person["pushover_user_key"],
                title=push_title,
                url=url,
                url_title="Open cashout",
                priority=1,
                source=source,
            )
            if ok:
                sent += 1
            else:
                logger.warning(
                    "cashout_pushover: async failed record_id=%s recipient_id=%s",
                    record_id,
                    person.get("id"),
                )
        return sent
    except Exception:
        logger.exception(
            "cashout_pushover: async fan-out failed record_id=%s", record_id
        )
        return 0

