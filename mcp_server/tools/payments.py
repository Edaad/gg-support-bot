"""Payment tools: ingested rail payments, Stripe sessions, bindings and auto-deposits.

Read tools only. Write tools (bind_payment_to_group) are added by the lead.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.payments_helpers import (
    OWNER_INGEST_METHODS,
    OWNER_VARIANT_COLUMNS,
    apply_analytics_chat_exclusion,
    apply_cashapp_payment_filters,
    apply_crypto_payment_filters,
    apply_paypal_payment_filters,
    apply_session_filters,
    apply_venmo_payment_filters,
    apply_zelle_payment_filters,
    build_cashapp_payment_read,
    build_crypto_payment_read,
    build_paypal_payment_read,
    build_venmo_payment_read,
    build_zelle_payment_read,
    cents_to_usd,
    list_cashapp_payer_aggregates,
    list_paypal_payer_aggregates,
    list_venmo_payer_aggregates,
    list_zelle_payer_aggregates,
    lookup_gg_nickname,
    resolve_group_title,
    resolve_method_display,
    stripe_dashboard_payment_url,
    stripe_dashboard_session_url,
    stripe_fee_usd,
)
from api.routes.payments import (
    _FIRST_TIME_BOUND_VIA,
    _apply_bound_via_filter,
    _auto_deposit_event_read,
    _auto_deposit_events_query,
    _auto_deposit_funnel_from_counts,
    _resolve_bound_via_filter,
)
from api.unified_payments import UnifiedPaymentFilters, fetch_unified_page
from db.models import (
    CashAppPayment,
    CryptoPayment,
    GroupPaymentMethodBinding,
    PaymentAutoDepositEvent,
    PaymentMethodBindAttempt,
    PayPalPayment,
    StripeCheckoutSession,
    StripeCustomer,
    VenmoPayment,
    ZellePayment,
)
from mcp_server.dates import parse_range
from mcp_server.db import read_session
from mcp_server.auth import actor_from_context
from mcp_server.envelope import dry_run, page, single, write_result
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

RAILS: tuple[str, ...] = ("venmo", "zelle", "cashapp", "paypal", "crypto")
_RAIL_MODELS = {
    "venmo": VenmoPayment,
    "zelle": ZellePayment,
    "cashapp": CashAppPayment,
    "paypal": PayPalPayment,
    "crypto": CryptoPayment,
}
SEARCH_METHODS: tuple[str, ...] = ("all", *RAILS, "stripe", "union")
STATUSES: tuple[str, ...] = ("bound", "unbound", "all")
AUTO_DEPOSIT_STATUSES: tuple[str, ...] = (
    "succeeded",
    "failed",
    "skipped",
    "eligible",
    "all",
)

_APPLY_FILTERS_BY_RAIL = {
    "venmo": apply_venmo_payment_filters,
    "zelle": apply_zelle_payment_filters,
    "cashapp": apply_cashapp_payment_filters,
    "paypal": apply_paypal_payment_filters,
    "crypto": apply_crypto_payment_filters,
}
_BUILD_READ_BY_RAIL = {
    "venmo": build_venmo_payment_read,
    "zelle": build_zelle_payment_read,
    "cashapp": build_cashapp_payment_read,
    "paypal": build_paypal_payment_read,
    "crypto": build_crypto_payment_read,
}
_PAYER_AGGREGATES_BY_RAIL = {
    "venmo": list_venmo_payer_aggregates,
    "zelle": list_zelle_payer_aggregates,
    "cashapp": list_cashapp_payer_aggregates,
    "paypal": list_paypal_payer_aggregates,
}
# payment_method_bind_attempts has one nullable FK per rail (crypto has none).
_BIND_ATTEMPT_FK_BY_RAIL = {
    "venmo": PaymentMethodBindAttempt.venmo_payment_id,
    "zelle": PaymentMethodBindAttempt.zelle_payment_id,
    "cashapp": PaymentMethodBindAttempt.cashapp_payment_id,
    "paypal": PaymentMethodBindAttempt.paypal_payment_id,
}


def _norm(value: str | None, default: str) -> str:
    return (value or default).strip().lower() or default


def _rail(method: str | None) -> str:
    slug = _norm(method, "")
    if slug not in RAILS:
        raise bad_input(f"method must be one of {', '.join(RAILS)}")
    return slug


def _summary_note(count: int, amount_usd: Any) -> str:
    return f"summary: {int(count)} payments, ${float(amount_usd or 0):,.2f}"


def _rail_rows(
    session: Session,
    *,
    rail: str,
    club_id: int,
    status: str,
    from_dt,
    to_dt,
    q: str | None,
    include_test: bool,
    variant: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Per-rail listing (mirrors the dashboard's /payments/<rail>/payments)."""
    payment_cls = OWNER_INGEST_METHODS[rail]
    apply_filters = _APPLY_FILTERS_BY_RAIL[rail]
    kwargs: dict[str, Any] = dict(
        club_id=club_id,
        status=status,
        from_dt=from_dt,
        to_dt=to_dt,
        include_test=include_test,
        q=q,
    )
    if rail == "crypto":
        kwargs["session"] = session
    base = apply_filters(session.query(payment_cls), **kwargs)
    if variant and variant.strip():
        base = base.filter(
            getattr(payment_cls, OWNER_VARIANT_COLUMNS[rail]) == variant.strip()
        )
    total, amount_cents = (
        base.order_by(None)
        .with_entities(
            func.count(payment_cls.id),
            func.coalesce(func.sum(payment_cls.amount_cents), 0),
        )
        .one()
    )
    rows = (
        base.order_by(payment_cls.created_at.desc(), payment_cls.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    names = club_name_map(session)
    build_read = _BUILD_READ_BY_RAIL[rail]
    items = []
    for row in rows:
        data = build_read(session, row)
        data["source"] = rail
        cid = data.get("club_id")
        data["club_name"] = names.get(int(cid)) if cid is not None else None
        items.append(data)
    return items, int(total or 0), int(amount_cents or 0)


@translate_errors
def search_payments(
    method: str = "all",
    status: str = "all",
    club: str | None = None,
    q: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    variant: str | None = None,
    include_test: bool = False,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Ingested payments across rails, newest first (the dashboard's All tab).
    `method`: all|venmo|zelle|cashapp|paypal|crypto|stripe|union (union = TR-checked
    pool-pay requests). `status` bound|unbound needs a single rail AND a club (per-rail
    listing; include_test only applies there). `variant` = receiving handle/address
    (not with method=all). from/to are UTC datetimes (YYYY-MM-DD = that ET day).
    Amounts in amount_cents + amount_usd; the `detail` dict holds the rail-specific
    row. Payer names/handles are masked unless include_pii. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    method_slug = _norm(method, "all")
    if method_slug not in SEARCH_METHODS:
        raise bad_input(f"method must be one of {', '.join(SEARCH_METHODS)}")
    status_slug = _norm(status, "all")
    if status_slug not in STATUSES:
        raise bad_input("status must be bound, unbound or all")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    from_dt, to_dt = parse_range(from_, to)
    notes: list[str] = []

    with read_session() as session:
        club_id = resolve_club_id(session, club)
        if status_slug != "all":
            if method_slug not in RAILS:
                raise bad_input(
                    "status filtering (bound/unbound) needs a single rail method "
                    f"({', '.join(RAILS)}); method=all/stripe/union has no bound state"
                )
            if club_id is None:
                raise bad_input("status filtering (bound/unbound) requires club")
            items, total, amount_cents = _rail_rows(
                session,
                rail=method_slug,
                club_id=club_id,
                status=status_slug,
                from_dt=from_dt,
                to_dt=to_dt,
                q=q,
                include_test=include_test,
                variant=variant,
                limit=limit,
                offset=offset,
            )
            notes.append(_summary_note(total, cents_to_usd(amount_cents)))
            notes.append(f"per-rail listing for {method_slug}; status={status_slug}")
            if include_test:
                notes.append("includes test payments")
            return page(
                items,
                total=total,
                limit=limit,
                offset=offset,
                include_pii=include_pii,
                notes=notes,
            )

        scope = "union" if method_slug == "union" else "all"
        unified_method = "all" if method_slug == "union" else method_slug
        filters = UnifiedPaymentFilters(
            variant=variant,
            from_dt=from_dt,
            to_dt=to_dt,
            q=q,
            club_id=club_id,
        )
        rows, total, summary = fetch_unified_page(
            session,
            scope=scope,
            owner=None,
            method=unified_method,
            filters=filters,
            limit=limit,
            offset=offset,
        )
        names = club_name_map(session)
        items = []
        for row in rows:
            data = row.model_dump()
            cid = data.get("club_id")
            data["club_name"] = names.get(int(cid)) if cid is not None else None
            items.append(data)
    notes.append(_summary_note(summary.total_count, summary.total_amount_usd))
    notes.append("test payments excluded")
    if include_test:
        notes.append("include_test only applies with status=bound|unbound")
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=notes,
    )


@translate_errors
def get_payment(
    method: str,
    payment_id: int,
    include_pii: bool = False,
) -> dict[str, Any]:
    """One ingested payment by rail (venmo|zelle|cashapp|paypal|crypto) and id, with
    its group binding (if bound), the first-time bind attempts that reference it and
    its auto-deposit outcome events. Amounts in amount_cents + amount_usd. Payer
    name/handle/address masked unless include_pii."""
    rail = _rail(method)
    payment_cls = OWNER_INGEST_METHODS[rail]
    with read_session() as session:
        row = session.get(payment_cls, int(payment_id))
        if row is None:
            raise not_found(f"{rail} payment {payment_id}")
        data = _BUILD_READ_BY_RAIL[rail](session, row)
        data["source"] = rail
        cid = data.get("club_id")
        data["club_name"] = (
            club_name_map(session).get(int(cid)) if cid is not None else None
        )
        binding = None
        if row.telegram_chat_id is not None:
            binding = (
                session.query(GroupPaymentMethodBinding)
                .filter(
                    GroupPaymentMethodBinding.telegram_chat_id
                    == int(row.telegram_chat_id),
                    GroupPaymentMethodBinding.payment_method_slug == rail,
                )
                .first()
            )
        data["binding"] = binding
        fk = _BIND_ATTEMPT_FK_BY_RAIL.get(rail)
        data["bind_attempts"] = (
            session.query(PaymentMethodBindAttempt)
            .filter(fk == int(row.id))
            .order_by(PaymentMethodBindAttempt.created_at.desc())
            .all()
            if fk is not None
            else []
        )
        data["auto_deposit_events"] = (
            session.query(PaymentAutoDepositEvent)
            .filter(
                PaymentAutoDepositEvent.payment_method_slug == rail,
                PaymentAutoDepositEvent.payment_id == int(row.id),
            )
            .order_by(PaymentAutoDepositEvent.id.desc())
            .all()
        )
        result = single(data, include_pii=include_pii)
    return result


@translate_errors
def list_payers(
    method: str,
    club: str,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Bound payers for one rail (venmo|zelle|cashapp|paypal; crypto has no payer
    aggregate) in a club, grouped by payer name + handle: total deposited
    (cents + usd), payment count, last payment and the latest group. `q` matches
    payer name or handle. Test payments excluded. Payer identity masked unless
    include_pii. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    rail = _rail(method)
    aggregates = _PAYER_AGGREGATES_BY_RAIL.get(rail)
    if aggregates is None:
        raise bad_input("list_payers supports venmo, zelle, cashapp or paypal")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        rows = aggregates(session, int(club_row.id), q).all()
        total = len(rows)
        items = []
        for row in rows[offset : offset + limit]:
            data = dict(row._mapping)
            chat_id = int(row.telegram_chat_id) if row.telegram_chat_id else None
            title, gg_id = (
                resolve_group_title(session, chat_id) if chat_id else (None, None)
            )
            total_cents = int(row.total_cents or 0)
            data.update(
                {
                    "telegram_chat_id": chat_id,
                    "group_title": title,
                    "gg_player_id": gg_id,
                    "gg_nickname": lookup_gg_nickname(session, int(club_row.id), gg_id),
                    "total_deposited_cents": total_cents,
                    "total_deposited_usd": cents_to_usd(total_cents),
                    "payment_count": int(row.payment_count or 0),
                }
            )
            data.pop("total_cents", None)
            items.append(data)
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=["test payments excluded", "paged in memory"],
    )


@translate_errors
def payment_bindings_summary(
    method: str = "venmo",
    club: str | None = None,
    bound_via: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    exclude_test_chats: bool = True,
) -> dict[str, Any]:
    """Group-binding analytics for one rail: total bound groups by bound_via, bind
    attempts by bind_kind, and the first-time setup funnel (initiated / succeeded /
    expired / cancelled / pending, success_rate). `bound_via` filters bindings
    (alias `manual` = manual_notification + manual_dashboard). from/to are UTC
    datetimes on bound_at / attempt created_at. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    slug = _norm(method, "venmo")
    from_dt, to_dt = parse_range(from_, to)
    bound_via_values = _resolve_bound_via_filter(bound_via)

    with read_session() as session:
        club_id = resolve_club_id(session, club)

        binding_q = session.query(
            GroupPaymentMethodBinding.bound_via,
            func.count(GroupPaymentMethodBinding.id),
        ).filter(GroupPaymentMethodBinding.payment_method_slug == slug)
        if club_id is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.club_id == club_id)
        if from_dt is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.bound_at >= from_dt)
        if to_dt is not None:
            binding_q = binding_q.filter(GroupPaymentMethodBinding.bound_at <= to_dt)
        binding_q = _apply_bound_via_filter(
            binding_q, GroupPaymentMethodBinding.bound_via, bound_via
        )
        if exclude_test_chats:
            binding_q = apply_analytics_chat_exclusion(
                session, binding_q, GroupPaymentMethodBinding.telegram_chat_id
            )
        binding_q = binding_q.group_by(GroupPaymentMethodBinding.bound_via)

        attempt_q = session.query(
            PaymentMethodBindAttempt.status,
            func.count(PaymentMethodBindAttempt.id),
        ).filter(PaymentMethodBindAttempt.payment_method_slug == slug)
        if club_id is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.club_id == club_id)
        if from_dt is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.created_at >= from_dt)
        if to_dt is not None:
            attempt_q = attempt_q.filter(PaymentMethodBindAttempt.created_at <= to_dt)
        if bound_via_values and len(bound_via_values) == 1:
            only = bound_via_values[0]
            if only in _FIRST_TIME_BOUND_VIA:
                attempt_q = attempt_q.filter(PaymentMethodBindAttempt.bind_kind == only)
        if exclude_test_chats:
            attempt_q = apply_analytics_chat_exclusion(
                session, attempt_q, PaymentMethodBindAttempt.telegram_chat_id
            )
        attempt_q = attempt_q.group_by(PaymentMethodBindAttempt.status)

        bind_kind_q = session.query(
            PaymentMethodBindAttempt.bind_kind,
            func.count(PaymentMethodBindAttempt.id),
        ).filter(PaymentMethodBindAttempt.payment_method_slug == slug)
        if club_id is not None:
            bind_kind_q = bind_kind_q.filter(
                PaymentMethodBindAttempt.club_id == club_id
            )
        if from_dt is not None:
            bind_kind_q = bind_kind_q.filter(
                PaymentMethodBindAttempt.created_at >= from_dt
            )
        if to_dt is not None:
            bind_kind_q = bind_kind_q.filter(
                PaymentMethodBindAttempt.created_at <= to_dt
            )
        if exclude_test_chats:
            bind_kind_q = apply_analytics_chat_exclusion(
                session, bind_kind_q, PaymentMethodBindAttempt.telegram_chat_id
            )
        bind_kind_q = bind_kind_q.group_by(PaymentMethodBindAttempt.bind_kind)

        bindings_by_via = [
            {"bound_via": str(row[0]), "count": int(row[1])} for row in binding_q.all()
        ]
        attempts_by_bind_kind = [
            {"bind_kind": str(row[0]), "count": int(row[1])}
            for row in bind_kind_q.all()
        ]
        by_status = {str(row[0]): int(row[1]) for row in attempt_q.all()}
        club_name = club_name_map(session).get(club_id) if club_id else None

    initiated = sum(by_status.values())
    succeeded = by_status.get("succeeded", 0)
    summary = {
        "payment_method_slug": slug,
        "club_id": club_id,
        "club_name": club_name,
        "total_bound": sum(row["count"] for row in bindings_by_via),
        "bindings_by_via": bindings_by_via,
        "attempts_by_bind_kind": attempts_by_bind_kind,
        "attempt_funnel": {
            "initiated": initiated,
            "succeeded": succeeded,
            "expired": by_status.get("expired", 0),
            "cancelled": by_status.get("cancelled", 0),
            "pending": by_status.get("pending", 0),
            "success_rate": (succeeded / initiated) if initiated else None,
        },
    }
    notes = ["excludes test chats"] if exclude_test_chats else []
    return single(summary, notes=notes)


@translate_errors
def list_bind_attempts(
    method: str | None = None,
    club: str | None = None,
    status: str | None = None,
    bound_via: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    exclude_test_chats: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """First-time payment-method setup attempts (special amount or memo emoji),
    newest first, with the payment that completed them. `method` = rail slug
    (omit for all). `status` pending|succeeded|expired|cancelled. `bound_via`
    special_amount|memo_emoji narrows by bind_kind. from/to are UTC datetimes on
    created_at. Amounts in amount_cents + amount_usd. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    slug = _norm(method, "all")
    status_slug = _norm(status, "all")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    from_dt, to_dt = parse_range(from_, to)
    bound_via_values = _resolve_bound_via_filter(bound_via)

    with read_session() as session:
        club_id = resolve_club_id(session, club)
        q = session.query(PaymentMethodBindAttempt)
        if slug != "all":
            q = q.filter(PaymentMethodBindAttempt.payment_method_slug == slug)
        if club_id is not None:
            q = q.filter(PaymentMethodBindAttempt.club_id == club_id)
        if status_slug != "all":
            q = q.filter(PaymentMethodBindAttempt.status == status_slug)
        if from_dt is not None:
            q = q.filter(PaymentMethodBindAttempt.created_at >= from_dt)
        if to_dt is not None:
            q = q.filter(PaymentMethodBindAttempt.created_at <= to_dt)
        if bound_via_values and len(bound_via_values) == 1:
            only = bound_via_values[0]
            if only in _FIRST_TIME_BOUND_VIA:
                q = q.filter(PaymentMethodBindAttempt.bind_kind == only)
        if exclude_test_chats:
            q = apply_analytics_chat_exclusion(
                session, q, PaymentMethodBindAttempt.telegram_chat_id
            )
        total = q.order_by(None).count()
        rows = (
            q.order_by(
                PaymentMethodBindAttempt.created_at.desc(),
                PaymentMethodBindAttempt.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = club_name_map(session)
        items = []
        for row in rows:
            title, _gg = resolve_group_title(session, int(row.telegram_chat_id))
            amount_cents = (
                int(row.amount_cents) if row.amount_cents is not None else None
            )
            items.append(
                {
                    "id": int(row.id),
                    "telegram_chat_id": int(row.telegram_chat_id),
                    "club_id": int(row.club_id),
                    "club_name": names.get(int(row.club_id)),
                    "payment_method_slug": str(row.payment_method_slug),
                    "method_id": int(row.method_id),
                    "tier_id": int(row.tier_id) if row.tier_id is not None else None,
                    "variant_id": int(row.variant_id),
                    "deposit_session_id": row.deposit_session_id,
                    "bind_kind": str(getattr(row, "bind_kind", None) or row.bound_via),
                    "amount_cents": amount_cents,
                    "amount_usd": cents_to_usd(amount_cents)
                    if amount_cents is not None
                    else None,
                    "setup_emoji": getattr(row, "setup_emoji", None),
                    "status": str(row.status),
                    "bound_via": str(row.bound_via),
                    "initiated_by_telegram_user_id": row.initiated_by_telegram_user_id,
                    "venmo_payment_id": row.venmo_payment_id,
                    "zelle_payment_id": row.zelle_payment_id,
                    "cashapp_payment_id": row.cashapp_payment_id,
                    "paypal_payment_id": row.paypal_payment_id,
                    "group_title": title,
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "completed_at": row.completed_at,
                }
            )
    notes = ["excludes test chats"] if exclude_test_chats else []
    return page(items, total=total, limit=limit, offset=offset, notes=notes)


def _auto_deposit_summary(
    session: Session,
    *,
    slug: str,
    club_id: int | None,
    from_dt,
    to_dt,
    exclude_test_chats: bool,
) -> dict[str, Any]:
    base_q = _auto_deposit_events_query(
        session,
        slug=slug,
        club_id=club_id,
        dt_from=from_dt,
        dt_to=to_dt,
        exclude_test_chats=exclude_test_chats,
    )
    succeeded = case((PaymentAutoDepositEvent.status == "succeeded", 1), else_=0)
    failed = case((PaymentAutoDepositEvent.status == "failed", 1), else_=0)
    skipped = case((PaymentAutoDepositEvent.status == "skipped", 1), else_=0)
    total, succ, fail, skip = base_q.with_entities(
        func.count(PaymentAutoDepositEvent.id),
        func.sum(succeeded),
        func.sum(failed),
        func.sum(skipped),
    ).one()
    funnel = _auto_deposit_funnel_from_counts(
        int(total or 0), int(succ or 0), int(fail or 0), int(skip or 0)
    )
    reason_rows = (
        base_q.with_entities(
            PaymentAutoDepositEvent.skip_reason,
            func.count(PaymentAutoDepositEvent.id),
        )
        .filter(PaymentAutoDepositEvent.status == "skipped")
        .filter(PaymentAutoDepositEvent.skip_reason.isnot(None))
        .group_by(PaymentAutoDepositEvent.skip_reason)
        .all()
    )
    by_club: list[dict[str, Any]] = []
    if club_id is None:
        names = club_name_map(session)
        club_rows = (
            base_q.with_entities(
                PaymentAutoDepositEvent.club_id,
                func.count(PaymentAutoDepositEvent.id),
                func.sum(succeeded),
                func.sum(failed),
                func.sum(skipped),
            )
            .filter(PaymentAutoDepositEvent.club_id.isnot(None))
            .group_by(PaymentAutoDepositEvent.club_id)
            .all()
        )
        for row in club_rows:
            cid = int(row[0])
            club_funnel = _auto_deposit_funnel_from_counts(
                int(row[1] or 0), int(row[2] or 0), int(row[3] or 0), int(row[4] or 0)
            )
            entry = club_funnel.model_dump()
            entry.update({"club_id": cid, "club_name": names.get(cid)})
            by_club.append(entry)
        by_club.sort(key=lambda row: (-row["total_payments"], row["club_id"]))
    return {
        "payment_method_slug": slug,
        "club_id": club_id,
        "funnel": funnel.model_dump(),
        "skipped_by_reason": [
            {"skip_reason": str(row[0]), "count": int(row[1])} for row in reason_rows
        ],
        "by_club": by_club,
    }


def _funnel_note(funnel: dict[str, Any]) -> str:
    rate = funnel.get("success_rate")
    rate_text = f"{rate:.1%}" if rate is not None else "n/a"
    return (
        f"funnel: total={funnel['total_payments']} eligible={funnel['eligible']} "
        f"succeeded={funnel['succeeded']} failed={funnel['failed']} "
        f"skipped={funnel['skipped']} success_rate={rate_text}"
    )


@translate_errors
def list_auto_deposits(
    method: str = "all",
    club: str | None = None,
    status: str | None = None,
    skip_reason: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    summary_only: bool = False,
    exclude_test_chats: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Auto-deposit outcomes for ingested payments (clubs with auto-deposit on),
    newest payment first. `method` = rail slug or all. `status`
    succeeded|failed|skipped|eligible (= succeeded+failed). `skip_reason` exact
    match. from/to are UTC datetimes on payment_at. summary_only returns just the
    funnel, skip reasons and per-club breakdown (also given in notes otherwise).
    Amounts in amount_cents + amount_usd. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    slug = _norm(method, "all")
    if slug != "all" and slug not in RAILS:
        raise bad_input(f"method must be all or one of {', '.join(RAILS)}")
    status_slug = _norm(status, "all")
    if status_slug not in AUTO_DEPOSIT_STATUSES:
        raise bad_input(f"status must be one of {', '.join(AUTO_DEPOSIT_STATUSES)}")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    from_dt, to_dt = parse_range(from_, to)
    base_notes = ["excludes test chats"] if exclude_test_chats else []

    with read_session() as session:
        club_id = resolve_club_id(session, club)
        summary = _auto_deposit_summary(
            session,
            slug=slug,
            club_id=club_id,
            from_dt=from_dt,
            to_dt=to_dt,
            exclude_test_chats=exclude_test_chats,
        )
        names = club_name_map(session)
        summary["club_name"] = names.get(club_id) if club_id else None
        if summary_only:
            return single(summary, notes=base_notes)

        q = _auto_deposit_events_query(
            session,
            slug=slug,
            club_id=club_id,
            dt_from=from_dt,
            dt_to=to_dt,
            exclude_test_chats=exclude_test_chats,
            status=status_slug if status_slug != "all" else None,
            skip_reason=skip_reason,
        )
        total = q.order_by(None).count()
        rows = (
            q.order_by(
                PaymentAutoDepositEvent.payment_at.desc(),
                PaymentAutoDepositEvent.id.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        items = [
            _auto_deposit_event_read(
                row, names.get(int(row.club_id)) if row.club_id is not None else None
            ).model_dump()
            for row in rows
        ]
    notes = [_funnel_note(summary["funnel"]), *base_notes]
    if status_slug != "all" or skip_reason:
        notes.append("funnel note ignores the status/skip_reason filters")
    return page(items, total=total, limit=limit, offset=offset, notes=notes)


@translate_errors
def list_stripe_sessions(
    club: str,
    status: str = "complete",
    q: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_pii: bool = False,
) -> dict[str, Any]:
    """Stripe Checkout sessions for a club, newest first. Only completed payments
    are stored, so `status` is complete or all. `q` matches group title, player or
    Stripe customer. from/to are UTC datetimes on created_at. Amounts in
    amount_cents + amount_usd plus an estimated stripe_fee_usd. Club: Club id (int), key (round_table|creator_club|clubgto) or slug (round-table|creator-club|clubgto|aces-table). Omit for all clubs."""
    status_slug = _norm(status, "complete")
    if status_slug not in ("complete", "all"):
        raise bad_input("status must be complete or all")
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    from_dt, to_dt = parse_range(from_, to)

    with read_session() as session:
        club_row = resolve_club(session, club)
        if club_row is None:
            raise bad_input("club is required")
        club_id = int(club_row.id)
        base = apply_session_filters(
            session.query(StripeCheckoutSession),
            club_id=club_id,
            status=status_slug,
            method_id=None,
            manual_only=False,
            from_dt=from_dt,
            to_dt=to_dt,
            q=q,
        )
        total = base.order_by(None).count()
        rows = (
            base.order_by(
                StripeCheckoutSession.created_at.desc(), StripeCheckoutSession.id.desc()
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        customer_by_stripe_id: dict[str, StripeCustomer] = {}
        if rows:
            customer_ids = {row.stripe_customer_id for row in rows}
            for cust in (
                session.query(StripeCustomer)
                .filter(
                    StripeCustomer.club_id == club_id,
                    StripeCustomer.stripe_customer_id.in_(customer_ids),
                )
                .all()
            ):
                customer_by_stripe_id[cust.stripe_customer_id] = cust
        items = []
        for row in rows:
            cust = customer_by_stripe_id.get(row.stripe_customer_id)
            title, gg_id = resolve_group_title(
                session,
                row.telegram_chat_id,
                fallback_gg_player_id=cust.gg_player_id if cust else None,
            )
            method_name, method_slug = resolve_method_display(
                session, club_id, row.payment_method_id
            )
            items.append(
                {
                    "id": row.id,
                    "stripe_checkout_session_id": row.stripe_checkout_session_id,
                    "stripe_customer_id": row.stripe_customer_id,
                    "telegram_chat_id": row.telegram_chat_id,
                    "club_id": row.club_id,
                    "club_name": str(club_row.name),
                    "amount_cents": row.amount_cents,
                    "amount_usd": cents_to_usd(row.amount_cents),
                    "stripe_fee_usd": stripe_fee_usd(row.amount_cents),
                    "currency": row.currency,
                    "status": row.status,
                    "payment_method_id": row.payment_method_id,
                    "method_name": method_name,
                    "method_slug": method_slug,
                    "stripe_payment_intent_id": row.stripe_payment_intent_id,
                    "group_title": title,
                    "gg_player_id": gg_id,
                    "gg_nickname": lookup_gg_nickname(session, club_id, gg_id),
                    "player_display_name": cust.player_display_name if cust else None,
                    "stripe_dashboard_url": stripe_dashboard_session_url(
                        row.stripe_checkout_session_id
                    ),
                    "stripe_payment_url": stripe_dashboard_payment_url(
                        row.stripe_payment_intent_id
                    ),
                    "created_at": row.created_at,
                    "completed_at": row.completed_at,
                    "updated_at": row.updated_at,
                }
            )
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        include_pii=include_pii,
        notes=["stripe_fee_usd is an estimate (2.9% + $0.30)"],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="search_payments", annotations=READ_ONLY)(search_payments)
    mcp.tool(name="get_payment", annotations=READ_ONLY)(get_payment)
    mcp.tool(name="list_payers", annotations=READ_ONLY)(list_payers)
    mcp.tool(name="payment_bindings_summary", annotations=READ_ONLY)(
        payment_bindings_summary
    )
    mcp.tool(name="list_bind_attempts", annotations=READ_ONLY)(list_bind_attempts)
    mcp.tool(name="list_auto_deposits", annotations=READ_ONLY)(list_auto_deposits)
    mcp.tool(name="list_stripe_sessions", annotations=READ_ONLY)(list_stripe_sessions)
    # write tools are added by the lead (bind_payment_to_group)


# ── Write tools ──────────────────────────────────────────────────────────────

_BIND_FUNCS = {
    "venmo": ("bot.services.venmo_payments", "bind_venmo_payment_by_id"),
    "zelle": ("bot.services.zelle_payments", "bind_zelle_payment_by_id"),
    "cashapp": ("bot.services.cashapp_payments", "bind_cashapp_payment_by_id"),
    "paypal": ("bot.services.paypal_payments", "bind_paypal_payment_by_id"),
    "crypto": ("bot.services.crypto_payments", "bind_crypto_payment_by_id"),
}


def _payment_snapshot(method: str, payment_id: int) -> dict[str, Any]:
    """Current binding state of one ingested payment (columns only)."""
    model = _RAIL_MODELS[method]
    with read_session() as session:
        row = session.get(model, int(payment_id))
        if row is None:
            raise not_found(f"{method} payment {payment_id}")
        return {
            "id": int(row.id),
            "method": method,
            "amount_cents": getattr(row, "amount_cents", None),
            "paid_at": getattr(row, "paid_at", None),
            "telegram_chat_id": getattr(row, "telegram_chat_id", None),
            "group_title": getattr(row, "bound_group_title_at_bind", None),
            "club_id": getattr(row, "club_id", None),
            "bound_via": getattr(row, "bound_via", None),
            "auto_bound": getattr(row, "auto_bound", None),
            "is_test": getattr(row, "is_test", None),
        }


@translate_errors
async def bind_payment_to_group(
    method: str,
    payment_id: int,
    group_title: str,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Bind (or rebind) an ingested payment to a player's support group, exactly
    like the dashboard Payments bind action. `method` is venmo|zelle|cashapp|paypal|
    crypto; `group_title` must be the full linked title ("GTO / 8190-5287 / name").
    Without confirm=true returns a dry run showing the payment and the resolved
    group. With confirm=true it binds, which may post to the group and trigger
    auto-deposit, and is logged under your token name."""
    from importlib import import_module

    from bot.services.venmo_payments import resolve_bound_group

    rail = (method or "").strip().lower()
    if rail not in _BIND_FUNCS:
        raise bad_input("method must be one of venmo, zelle, cashapp, paypal, crypto")
    title = (group_title or "").strip()
    if not title:
        raise bad_input("group_title is required")
    actor = actor_from_context(ctx)
    before = _payment_snapshot(rail, payment_id)
    resolved = resolve_bound_group(title)
    if not resolved.ok or resolved.bound_group is None:
        raise bad_input(resolved.error or "could not resolve group title")
    target = {
        "telegram_chat_id": resolved.bound_group.telegram_chat_id,
        "club_id": resolved.bound_group.club_id,
        "group_title": resolved.bound_group.group_title,
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=before,
            proposed={"bind_to": target},
            message="dry run: would bind this payment to the group above",
        )
    module_name, func_name = _BIND_FUNCS[rail]
    bind = getattr(import_module(module_name), func_name)
    result = await bind(payment_id=int(payment_id), group_title_input=title)
    if not result.ok:
        raise ToolError(result.error or "bind failed")
    after = _payment_snapshot(rail, payment_id)
    return write_result(
        actor=actor,
        before=before,
        after=after,
        message=f"bound {rail} payment {payment_id} to {target['group_title']}",
        notes=[
            "bound_via is recorded as manual_dashboard; the MCP actor is in mcp_tool_calls"
        ],
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="bind_payment_to_group", annotations=WRITE)(bind_payment_to_group)
