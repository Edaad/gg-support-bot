"""Operational views: deposit alerts, GTO Zelle rotation, Plaid bank feed, tickets."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from api.gto_club import GTO_CLUB_NAME
from api.plaid_bank import club_sync_state, list_zelle
from bot.services.deposit_method_alerts import (
    clubs_for_destinations,
    condition_summaries,
    disable_status,
    eastern_week_bounds_utc,
    week_stats_map,
)
from bot.services.variant_pause import build_gto_zelle_cards
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentTier,
    ClubPaymentTierVariant,
    DepositMethodAlert,
    IssueReport,
    IssueReportAttachment,
    McpToolCall,
    PlaidItem,
)
from mcp_server.db import read_session, write_session
from mcp_server.auth import actor_from_context
from mcp_server.envelope import dry_run, page, write_result
from mcp_server.errors import bad_input, not_found, translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    WRITE,
    clamp_limit,
    clamp_offset,
    club_name_map,
    resolve_club,
    resolve_club_id,
)

ISSUE_STATUSES = ("open", "resolved")


def _alert_to_dict(
    row: DepositMethodAlert,
    *,
    week_id: str,
    volume_cents: int,
    tx_count: int,
    clubs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mirror of the dashboard card (api/routes/deposit_method_alerts.py)."""
    from bot.services.deposit_method_alerts import WeekStats

    stats = WeekStats(volume_cents=volume_cents, tx_count=tx_count)
    return {
        "id": int(row.id),
        "name": row.name,
        "method": row.method,
        "variant": row.variant,
        "is_active": bool(row.is_active),
        "conditions": condition_summaries(list(row.conditions or [])),
        "disable_enabled": bool(row.disable_enabled),
        "disable_inherits": bool(row.disable_inherits),
        "disable_conditions": condition_summaries(list(row.disable_conditions or [])),
        "disable_status": disable_status(row, stats, week_id),
        "clubs": clubs,
        "last_fired_week_id": row.last_fired_week_id,
        "last_fired_at": row.last_fired_at,
        "last_disable_fired_week_id": row.last_disable_fired_week_id,
        "last_disable_fired_at": row.last_disable_fired_at,
        "week_id": week_id,
        "week_volume_cents": int(volume_cents),
        "week_volume_usd": (Decimal(volume_cents) / 100).quantize(Decimal("0.01")),
        "week_tx_count": int(tx_count),
        "alerted_this_week": row.last_fired_week_id == week_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@translate_errors
def list_deposit_method_alerts(club: str | None = None) -> dict[str, Any]:
    """Weekly deposit-volume alerts per method + receiving variant, each with this
    America/New_York week's ingested volume (cents + usd), transaction count, the
    clubs whose deposit variants match, and the auto-disable status
    (on|pending_slack|off, null when disable is not configured). `club` keeps
    alerts matching that club's variants (id, key or slug)."""
    with read_session() as session:
        club_id = resolve_club_id(session, club)
        week = eastern_week_bounds_utc()
        rows = (
            session.query(DepositMethodAlert)
            .order_by(DepositMethodAlert.id.desc())
            .all()
        )
        pairs = [(r.method, r.variant) for r in rows]
        stats = week_stats_map(session, pairs, week=week)
        clubs = clubs_for_destinations(session, pairs)
        items = []
        for row in rows:
            key = (row.method, row.variant)
            matched = clubs.get(key, [])
            if club_id is not None and all(int(c["id"]) != club_id for c in matched):
                continue
            items.append(
                _alert_to_dict(
                    row,
                    week_id=week.week_id,
                    volume_cents=stats[key].volume_cents,
                    tx_count=stats[key].tx_count,
                    clubs=matched,
                )
            )
    return page(
        items,
        total=len(items),
        limit=len(items) or 1,
        offset=0,
        notes=[
            "week_volume_cents counts non-test ingested payments since Monday 00:00 ET",
            "variant is the receiving handle/address the alert watches (not PII-masked;"
            " it is the club's own account)",
        ],
    )


def _gto_zelle_rows(session) -> list[tuple[ClubPaymentTierVariant, ClubPaymentTier]]:
    """Same rows as bot.services.variant_pause.gto_zelle_rows, minus its write
    (release_expired_variant_pauses), so this stays a pure read."""
    club = session.query(Club).filter(Club.name == GTO_CLUB_NAME).first()
    if club is None:
        return []
    return (
        session.query(ClubPaymentTierVariant, ClubPaymentTier)
        .join(
            ClubPaymentMethod,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .join(ClubPaymentTier, ClubPaymentTierVariant.tier_id == ClubPaymentTier.id)
        .filter(
            ClubPaymentMethod.club_id == int(club.id),
            ClubPaymentMethod.direction == "deposit",
            func.lower(ClubPaymentMethod.slug) == "zelle",
            ClubPaymentMethod.tracks_manual_requests.is_(False),
        )
        .order_by(
            ClubPaymentTier.sort_order,
            ClubPaymentTier.id,
            ClubPaymentTierVariant.sort_order,
            ClubPaymentTierVariant.id,
        )
        .all()
    )


@translate_errors
def list_gto_zelle_cards(include_pii: bool = False) -> dict[str, Any]:
    """ClubGTO Zelle deposit variants in rotation order with tier band, pause state
    (active|paused|disabled) and paused_until. Does not release expired pauses
    (the dashboard does), so a lapsed pause may still read disabled here. The
    receiving tag is masked unless include_pii."""
    with read_session() as session:
        cards = build_gto_zelle_cards(_gto_zelle_rows(session))
    return page(
        cards,
        total=len(cards),
        limit=len(cards) or 1,
        offset=0,
        include_pii=include_pii,
        notes=[
            "tier_min/tier_max are dollars",
            "expired pauses are not released by this read; state may lag the dashboard",
        ],
    )


def _zelle_to_dict(row, institution_name: str | None) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "plaid_item_id": int(row.plaid_item_id),
        "institution_name": institution_name,
        "txn_date": row.txn_date,
        "amount": row.amount,
        "iso_currency_code": row.iso_currency_code,
        # PII keys: payer -> payer_name, payee -> counterparty_name, name -> payer_summary
        "payer_name": row.payer,
        "counterparty_name": row.payee,
        "payer_summary": row.name,
        "merchant_name": row.merchant_name,
        "memo": row.memo,
        "original_description": row.original_description,
        "payment_method": row.payment_method,
        "payment_channel": row.payment_channel,
        "pending": bool(row.pending),
        "first_seen_at": row.first_seen_at,
    }


@translate_errors
def list_plaid_zelle(
    club: str, limit: int = 50, offset: int = 0, include_pii: bool = False
) -> dict[str, Any]:
    """Zelle transactions pulled from a club's linked bank accounts (Plaid), newest
    txn_date first, with the sync state and a per-institution summary in notes.
    Amounts are dollars (positive = money out of the bank account, Plaid sign
    convention). Payer/payee/memo/description are masked unless include_pii.
    `club` is required (id, key or slug)."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        club_id = int(club_row.id)
        club_name = str(club_row.name)
        total, rows = list_zelle(session, club_id, offset, limit)
        items = [_zelle_to_dict(row, name) for row, name in rows]
        syncing, sync_error = club_sync_state(session, club_id)
        institutions = (
            session.query(
                PlaidItem.id,
                PlaidItem.institution_name,
                PlaidItem.institution_id,
                PlaidItem.sync_status,
                PlaidItem.last_synced_at,
                PlaidItem.sync_error,
                PlaidItem.created_at,
            )
            .filter(PlaidItem.club_id == club_id)
            .order_by(PlaidItem.created_at.desc(), PlaidItem.id.desc())
            .all()
        )
        summary = "; ".join(
            f"#{item_id} {inst_name} ({sync_status}"
            + (f", last sync {last.isoformat()}" if last else "")
            + (", error" if err else "")
            + ")"
            for item_id, inst_name, _inst_id, sync_status, last, err, _created in (
                institutions
            )
        )
    notes = [
        "amounts are dollars (Plaid sign: positive = outflow)",
        f"club={club_name}; syncing={'yes' if syncing else 'no'}"
        + (f"; sync_error={sync_error}" if sync_error else ""),
        f"institutions: {summary or 'none linked'}",
        "bank logins' access tokens are never returned",
    ]
    return page(
        items,
        total=int(total),
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=notes,
    )


@translate_errors
def list_issue_reports(
    status: str | None = None,
    club: str | None = None,
    limit: int = 50,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Issue-report tickets filed by account managers (/report or the API),
    newest first, with tags, category, notify tags, resolution notes and
    attachment metadata (id, filename, content_type, size_bytes; never the file
    bytes). `status` is open|resolved. Reporter names are masked unless
    include_pii. `club` is an id, key or slug; omit for all clubs."""
    if status is not None and status not in ISSUE_STATUSES:
        raise bad_input("status must be open or resolved")
    limit = clamp_limit(limit)
    with read_session() as session:
        club_id = resolve_club_id(session, club)
        names = club_name_map(session)
        query = session.query(IssueReport).options(joinedload(IssueReport.club))
        if status is not None:
            query = query.filter(IssueReport.status == status)
        if club_id is not None:
            query = query.filter(IssueReport.club_id == club_id)
        total = query.order_by(None).count()
        rows = query.order_by(IssueReport.created_at.desc()).limit(limit).all()
        report_ids = [int(r.id) for r in rows]
        attachments: dict[int, list[dict[str, Any]]] = {}
        if report_ids:
            att_rows = (
                session.query(
                    IssueReportAttachment.id,
                    IssueReportAttachment.issue_report_id,
                    IssueReportAttachment.filename,
                    IssueReportAttachment.content_type,
                    IssueReportAttachment.attachment_type,
                    func.length(IssueReportAttachment.content),
                    IssueReportAttachment.created_at,
                )
                .filter(IssueReportAttachment.issue_report_id.in_(report_ids))
                .order_by(IssueReportAttachment.id)
                .all()
            )
            for att_id, report_id, filename, ctype, att_type, size, created in att_rows:
                attachments.setdefault(int(report_id), []).append(
                    {
                        "id": int(att_id),
                        "filename": filename,
                        "content_type": ctype,
                        "attachment_type": att_type,
                        "size_bytes": int(size) if size is not None else None,
                        "created_at": created,
                    }
                )
        items = []
        for r in rows:
            club_ref_id = int(r.club_id) if r.club_id is not None else None
            items.append(
                {
                    "id": int(r.id),
                    "title": r.title,
                    "description": r.description,
                    "tags": list(r.tags or []),
                    "category": r.category,
                    "notify_tags": list(r.notify_tags or []),
                    "status": r.status,
                    "reporter_name": r.reporter_name,
                    "reporter_source": r.reporter_source,
                    "reporter_telegram_user_id": r.reporter_telegram_user_id,
                    "club_id": club_ref_id,
                    "club_name": names.get(club_ref_id) if club_ref_id else None,
                    "group_title": r.group_title,
                    "telegram_chat_id": r.telegram_chat_id,
                    "slack_message_ts": r.slack_message_ts,
                    "resolution_notes": r.resolution_notes,
                    "resolved_at": r.resolved_at,
                    "resolved_by_telegram_user_id": r.resolved_by_telegram_user_id,
                    "last_slack_reminder_at": r.last_slack_reminder_at,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                    "attachments": attachments.get(int(r.id), []),
                }
            )
    return page(
        items,
        total=total,
        limit=limit,
        offset=0,
        include_pii=include_pii,
        notes=[
            "no offset paging; narrow with status/club or lower limit",
            "attachments carry metadata only",
        ],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="list_deposit_method_alerts", annotations=READ_ONLY)(
        list_deposit_method_alerts
    )
    mcp.tool(name="list_gto_zelle_cards", annotations=READ_ONLY)(list_gto_zelle_cards)
    mcp.tool(name="list_plaid_zelle", annotations=READ_ONLY)(list_plaid_zelle)
    mcp.tool(name="list_issue_reports", annotations=READ_ONLY)(list_issue_reports)
    # write tools are added by the lead


# ── Write tools and the MCP audit log ────────────────────────────────────────


def _variant_card(variant: ClubPaymentTierVariant) -> dict[str, Any]:
    from bot.services.variant_pause import card_state

    tier = variant.tier
    method = tier.method if tier is not None else None
    return {
        "id": int(variant.id),
        "label": variant.label,
        "tier_id": int(variant.tier_id),
        "tier_label": tier.label if tier is not None else None,
        "method_id": int(variant.method_id),
        "method_name": method.name if method is not None else None,
        "method_slug": method.slug if method is not None else None,
        "club_id": int(method.club_id) if method is not None else None,
        "is_active": bool(variant.is_active),
        "paused_until": variant.paused_until,
        "state": card_state(variant),
        "tag": getattr(variant, "tag", None),
    }


def _load_variant(session, variant_id: int) -> ClubPaymentTierVariant:
    variant = (
        session.query(ClubPaymentTierVariant)
        .options(
            joinedload(ClubPaymentTierVariant.tier).joinedload(ClubPaymentTier.method)
        )
        .filter(ClubPaymentTierVariant.id == int(variant_id))
        .first()
    )
    if variant is None:
        raise not_found(f"variant {variant_id}")
    return variant


def _toggle_variant(variant_id: int, confirm: bool, ctx: Any, *, pause: bool):
    from bot.services.variant_pause import apply_pause, apply_resume

    actor = actor_from_context(ctx)
    with read_session() as session:
        before = _variant_card(_load_variant(session, variant_id))
    verb = "pause" if pause else "resume"
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"action": verb, "pause_hours": 48 if pause else None},
            message=f"dry run: would {verb} variant {variant_id}",
        )
    with write_session() as session:
        variant = _load_variant(session, variant_id)
        if pause:
            apply_pause(variant)
        else:
            apply_resume(variant)
        session.flush()
        after = _variant_card(variant)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"{verb}d variant {variant_id} ({after['label']})",
    )


@translate_errors
def pause_variant(
    variant_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """Pause a payment tier variant (receiving handle) for 48 hours so it leaves
    the rotation, as the GTO Zelle dashboard cards do. Fails if the variant is
    already off. Without confirm=true returns a dry run with the current state."""
    return _toggle_variant(variant_id, confirm, ctx, pause=True)


@translate_errors
def resume_variant(
    variant_id: int, confirm: bool = False, ctx: Context | None = None
) -> dict[str, Any]:
    """End a running pause on a payment tier variant and return it to rotation.
    Fails if the variant is not paused (a disabled variant must be re-enabled on
    the dashboard). Without confirm=true returns a dry run."""
    return _toggle_variant(variant_id, confirm, ctx, pause=False)


@translate_errors
def list_mcp_tool_calls(
    actor: str | None = None,
    tool: str | None = None,
    outcome: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Audit log of calls made through this MCP server: actor (token name), tool,
    outcome (ok|error|refused), duration and redacted arguments, newest first.
    Use it to review what an AI session changed (is_write=true rows)."""
    from mcp_server.dates import parse_range

    if outcome is not None and outcome not in ("ok", "error", "refused"):
        raise bad_input("outcome must be ok, error or refused")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    start, stop = parse_range(from_, to)
    with read_session() as session:
        query = session.query(McpToolCall)
        if actor:
            query = query.filter(McpToolCall.actor == actor.strip())
        if tool:
            query = query.filter(McpToolCall.tool == tool.strip())
        if outcome:
            query = query.filter(McpToolCall.outcome == outcome)
        if start:
            query = query.filter(McpToolCall.created_at >= start)
        if stop:
            query = query.filter(McpToolCall.created_at < stop)
        total = query.order_by(None).count()
        rows = (
            query.order_by(McpToolCall.created_at.desc(), McpToolCall.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        # Serialize while the session is open: rows expire on close.
        return page(rows, total=total, limit=limit, offset=offset)


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="pause_variant", annotations=WRITE)(pause_variant)
    mcp.tool(name="resume_variant", annotations=WRITE)(resume_variant)
    mcp.tool(name="list_mcp_tool_calls", annotations=READ_ONLY)(list_mcp_tool_calls)
