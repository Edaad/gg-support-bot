"""Unified payments list: merge owner-ingested + union TR-checked rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.payments_helpers import (
    OWNER_INGEST_METHODS,
    OWNER_METHODS_BY_OWNER,
    OWNER_VARIANT_COLUMNS,
    aggregate_owner_payment_query,
    apply_owner_ingest_filters,
    apply_owner_stripe_filters,
    cents_to_usd,
    lookup_gg_nickname,
    resolve_group_title,
)
from api.routes.manual_deposit_requests import (
    _list_query as union_list_query,
    _list_summary as union_list_summary,
    _to_read as manual_deposit_to_read,
)
from api.schemas_payments import (
    OwnerPaymentSummary,
    UnifiedPaymentRowRead,
)
from bot.services.union_method_types import validate_union_method_type
from db.models import ManualDepositRequest, StripeCheckoutSession

ScopeSlug = Literal["all", "owner", "union"]
OwnerSlug = Literal["round-table", "vaughn", "mateos"]

ALL_OWNERS: tuple[str, ...] = ("round-table", "vaughn", "mateos")
UNION_METHOD_TYPES: frozenset[str] = frozenset({"zelle", "cashapp", "applepay", "venmo"})

OWNER_LABELS: dict[str, str] = {
    "round-table": "RT",
    "vaughn": "Vaughn",
    "mateos": "Mateos",
}

METHOD_LABELS: dict[str, str] = {
    "stripe": "Stripe",
    "venmo": "Venmo",
    "zelle": "Zelle",
    "cashapp": "Cash App",
    "paypal": "PayPal",
    "crypto": "Crypto",
    "applepay": "Apple Pay",
}


@dataclass(frozen=True)
class PaymentSourceSpec:
    kind: str
    owner_slug: str | None = None
    method_slug: str | None = None
    union_method_type: str | None = None


@dataclass
class UnifiedPaymentFilters:
    variant: str | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    q: str | None = None
    club_id: int | None = None
    deposit_union: str | None = None


def _method_label(slug: str) -> str:
    return METHOD_LABELS.get(slug, slug.replace("-", " ").title())


def _owner_label(owner_slug: str | None) -> str:
    if owner_slug is None:
        return "Union"
    return OWNER_LABELS.get(owner_slug, owner_slug)


def _amount_to_cents(amount: Decimal | int | float) -> int:
    return int(Decimal(str(amount)) * 100)


def resolve_sources(
    scope: ScopeSlug,
    owner: str | None,
    method: str,
) -> list[PaymentSourceSpec]:
    method_slug = (method or "all").strip().lower()
    sources: list[PaymentSourceSpec] = []

    if scope == "union":
        if method_slug == "all":
            union_types = sorted(UNION_METHOD_TYPES)
        elif method_slug in UNION_METHOD_TYPES:
            union_types = [method_slug]
        else:
            raise HTTPException(
                400,
                f"Method '{method_slug}' is not a union method. "
                f"Allowed: {', '.join(sorted(UNION_METHOD_TYPES))}, all",
            )
        for union_type in union_types:
            sources.append(
                PaymentSourceSpec(kind="union_manual", union_method_type=union_type)
            )
        return sources

    if scope == "owner":
        if not owner or owner not in OWNER_METHODS_BY_OWNER:
            raise HTTPException(400, f"Unknown owner '{owner}'")
        owners = [owner]
    else:
        owners = list(ALL_OWNERS)

    if method_slug == "all":
        ingest_methods: set[str] = set()
        for owner_slug in owners:
            ingest_methods.update(OWNER_METHODS_BY_OWNER[owner_slug])
    elif method_slug in UNION_METHOD_TYPES and scope == "all":
        ingest_methods = {method_slug} & set().union(
            *(OWNER_METHODS_BY_OWNER[o] for o in owners)
        )
    elif method_slug == "stripe" or method_slug in OWNER_INGEST_METHODS:
        ingest_methods = {method_slug}
    else:
        raise HTTPException(400, f"Unknown method '{method_slug}'")

    for owner_slug in owners:
        allowed = OWNER_METHODS_BY_OWNER[owner_slug]
        for ingest_method in sorted(ingest_methods):
            if ingest_method not in allowed:
                continue
            if ingest_method == "stripe" and owner_slug != "round-table":
                continue
            sources.append(
                PaymentSourceSpec(
                    kind=ingest_method,
                    owner_slug=owner_slug,
                    method_slug=ingest_method,
                )
            )

    if scope == "all":
        if method_slug == "all":
            union_types = sorted(UNION_METHOD_TYPES)
        elif method_slug in UNION_METHOD_TYPES:
            union_types = [method_slug]
        else:
            union_types = []
        for union_type in union_types:
            sources.append(
                PaymentSourceSpec(kind="union_manual", union_method_type=union_type)
            )

    return sources


def _sort_key(occurred_at: datetime, source_kind: str, row_id: int) -> tuple:
    ts = occurred_at.timestamp() if occurred_at.tzinfo else occurred_at.replace(tzinfo=None).timestamp()
    return (-ts, source_kind, -row_id)


def _stripe_occurred_at(row: StripeCheckoutSession) -> datetime:
    return row.completed_at or row.created_at


def _ingest_occurred_at(method_slug: str, read_payload: dict[str, Any]) -> Any:
    """Time for unified list: crypto prefers paid_at; Stripe completed_at; else created_at."""
    created_at = read_payload["created_at"]
    if method_slug == "stripe":
        return read_payload.get("completed_at") or created_at
    if method_slug == "crypto":
        from bot.services.payment_chip_match import parse_payment_reference_at

        paid_raw = read_payload.get("paid_at")
        if paid_raw:
            parsed = parse_payment_reference_at(paid_at=str(paid_raw), created_at=None)
            if parsed is not None:
                return parsed
    return created_at


def _ingest_to_unified(
    db: Session,
    *,
    method_slug: str,
    owner_slug: str,
    read_payload: dict[str, Any],
) -> UnifiedPaymentRowRead:
    occurred_at = _ingest_occurred_at(method_slug, read_payload)
    status = read_payload.get("status")
    can_bind = (
        method_slug != "stripe"
        and status == "unbound"
    )
    variant = None
    if method_slug == "stripe":
        variant = read_payload.get("method_name")
    elif method_slug in OWNER_VARIANT_COLUMNS:
        variant = read_payload.get(OWNER_VARIANT_COLUMNS[method_slug])
    return UnifiedPaymentRowRead(
        source=method_slug,  # type: ignore[arg-type]
        id=int(read_payload["id"]),
        occurred_at=occurred_at,
        amount_cents=int(read_payload["amount_cents"]),
        amount_usd=Decimal(str(read_payload["amount_usd"])),
        method_slug=method_slug,
        method_label=_method_label(method_slug),
        owner_label=_owner_label(owner_slug),
        group_title=read_payload.get("group_title"),
        gg_nickname=read_payload.get("gg_nickname"),
        club_id=read_payload.get("club_id"),
        status=status,
        variant=variant,
        can_bind=can_bind,
        detail=read_payload,
    )


def _union_to_unified(db: Session, row: ManualDepositRequest) -> UnifiedPaymentRowRead:
    method_slug = (row.method_slug or "").strip().lower()
    title, gg_id = resolve_group_title(db, int(row.telegram_chat_id))
    group_title = row.group_title or title
    gg_nickname = (
        lookup_gg_nickname(db, int(row.club_id), gg_id) if gg_id else None
    )
    amount = Decimal(str(row.amount))
    read_model = manual_deposit_to_read(row)
    return UnifiedPaymentRowRead(
        source="union_manual",
        id=int(row.id),
        occurred_at=row.created_at,
        amount_cents=_amount_to_cents(amount),
        amount_usd=amount,
        method_slug=method_slug,
        method_label=row.method_name or _method_label(method_slug),
        owner_label="Union",
        group_title=group_title,
        gg_nickname=gg_nickname,
        club_id=int(row.club_id),
        status=None,
        variant=row.variant_name,
        can_bind=False,
        detail=read_model.model_dump(mode="json"),
    )


def _owner_read_helpers():
    from api.routes.owner_payments import (
        _BUILD_READ_BY_METHOD,
        _READ_MODEL_BY_METHOD,
        _build_stripe_session_read,
    )

    return _build_stripe_session_read, _BUILD_READ_BY_METHOD, _READ_MODEL_BY_METHOD


def _fetch_stripe_rows(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[UnifiedPaymentRowRead]]:
    build_stripe_read, _, _ = _owner_read_helpers()
    base = db.query(StripeCheckoutSession)
    base = apply_owner_stripe_filters(
        base,
        variant=filters.variant,
        from_dt=filters.from_dt,
        to_dt=filters.to_dt,
        q=filters.q,
        club_id=filters.club_id,
    )
    total_count, total_amount_cents = aggregate_owner_payment_query(
        base, StripeCheckoutSession.amount_cents
    )
    rows = (
        base.order_by(
            StripeCheckoutSession.created_at.desc(),
            StripeCheckoutSession.id.desc(),
        )
        .limit(fetch_limit)
        .all()
    )
    owner_slug = spec.owner_slug or "round-table"
    items = []
    for row in rows:
        read = build_stripe_read(db, row)
        payload = read.model_dump(mode="json")
        items.append(
            _ingest_to_unified(
                db, method_slug="stripe", owner_slug=owner_slug, read_payload=payload
            )
        )
    return total_count, total_amount_cents, items


def _fetch_ingest_rows(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[UnifiedPaymentRowRead]]:
    _, build_read_by_method, read_model_by_method = _owner_read_helpers()
    method_slug = spec.method_slug or spec.kind
    owner_slug = spec.owner_slug or ""
    payment_cls = OWNER_INGEST_METHODS[method_slug]
    base = db.query(payment_cls)
    base = apply_owner_ingest_filters(
        base,
        payment_cls,
        method_owner=owner_slug,
        variant=filters.variant,
        from_dt=filters.from_dt,
        to_dt=filters.to_dt,
        q=filters.q,
        club_id=filters.club_id,
    )
    total_count, total_amount_cents = aggregate_owner_payment_query(
        base, payment_cls.amount_cents
    )
    rows = (
        base.order_by(payment_cls.created_at.desc(), payment_cls.id.desc())
        .limit(fetch_limit)
        .all()
    )
    build_read = build_read_by_method[method_slug]
    read_model = read_model_by_method[method_slug]
    items = []
    for row in rows:
        payload = read_model.model_validate(build_read(db, row)).model_dump(mode="json")
        items.append(
            _ingest_to_unified(
                db,
                method_slug=method_slug,
                owner_slug=owner_slug,
                read_payload=payload,
            )
        )
    return total_count, total_amount_cents, items


def _fetch_union_rows(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[UnifiedPaymentRowRead]]:
    union_type = spec.union_method_type or ""
    query = union_list_query(
        db,
        method_type=union_type,
        deposit_union=filters.deposit_union,
        pool_pay_type="union_method",
        trade_record_checked=True,
        variant=filters.variant,
        from_dt=filters.from_dt,
        to_dt=filters.to_dt,
        q=filters.q,
        club_id=filters.club_id,
    )
    summary = union_list_summary(query)
    total_count = int(summary.total_count)
    total_amount_cents = _amount_to_cents(summary.total_amount)
    rows = (
        query.order_by(
            ManualDepositRequest.created_at.desc(),
            ManualDepositRequest.id.desc(),
        )
        .limit(fetch_limit)
        .all()
    )
    items = [_union_to_unified(db, row) for row in rows]
    return total_count, total_amount_cents, items


def _fetch_source_page(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[UnifiedPaymentRowRead]]:
    if spec.kind == "stripe":
        return _fetch_stripe_rows(db, spec, filters, fetch_limit)
    if spec.kind == "union_manual":
        return _fetch_union_rows(db, spec, filters, fetch_limit)
    return _fetch_ingest_rows(db, spec, filters, fetch_limit)


def _merge_rows(
    per_source: list[tuple[PaymentSourceSpec, list[UnifiedPaymentRowRead]]],
    offset: int,
    limit: int,
) -> list[UnifiedPaymentRowRead]:
    tagged: list[tuple[tuple, UnifiedPaymentRowRead]] = []
    for spec, rows in per_source:
        source_key = spec.union_method_type or spec.method_slug or spec.kind
        for row in rows:
            tagged.append((_sort_key(row.occurred_at, source_key, row.id), row))
    tagged.sort(key=lambda item: item[0])
    merged = [row for _, row in tagged]
    return merged[offset : offset + limit]


def aggregate_unified_summary(
    db: Session,
    sources: list[PaymentSourceSpec],
    filters: UnifiedPaymentFilters,
) -> OwnerPaymentSummary:
    total_count = 0
    total_amount_cents = 0
    for spec in sources:
        count, amount_cents, _ = _fetch_source_page(db, spec, filters, fetch_limit=0)
        total_count += count
        total_amount_cents += amount_cents
    return OwnerPaymentSummary(
        total_count=total_count,
        total_amount_cents=total_amount_cents,
        total_amount_usd=cents_to_usd(total_amount_cents),
    )


def fetch_unified_page(
    db: Session,
    *,
    scope: ScopeSlug,
    owner: str | None,
    method: str,
    filters: UnifiedPaymentFilters,
    limit: int,
    offset: int,
) -> tuple[list[UnifiedPaymentRowRead], int, OwnerPaymentSummary]:
    if method.strip().lower() == "all" and filters.variant:
        raise HTTPException(400, "Variant filter is not supported when method=all.")

    sources = resolve_sources(scope, owner, method)
    if not sources:
        empty = OwnerPaymentSummary(
            total_count=0, total_amount_cents=0, total_amount_usd=Decimal("0")
        )
        return [], 0, empty

    fetch_limit = offset + limit
    per_source: list[tuple[PaymentSourceSpec, list[UnifiedPaymentRowRead]]] = []
    total_count = 0
    total_amount_cents = 0
    for spec in sources:
        count, amount_cents, rows = _fetch_source_page(db, spec, filters, fetch_limit)
        total_count += count
        total_amount_cents += amount_cents
        per_source.append((spec, rows))

    items = _merge_rows(per_source, offset, limit)
    summary = OwnerPaymentSummary(
        total_count=total_count,
        total_amount_cents=total_amount_cents,
        total_amount_usd=cents_to_usd(total_amount_cents),
    )
    return items, total_count, summary


def fetch_all_unified_rows(
    db: Session,
    *,
    scope: ScopeSlug,
    owner: str | None,
    method: str,
    filters: UnifiedPaymentFilters,
    page_size: int = 200,
) -> tuple[list[UnifiedPaymentRowRead], OwnerPaymentSummary]:
    """Fetch all matching rows for export (paginate internally)."""
    sources = resolve_sources(scope, owner, method)
    if not sources:
        empty = OwnerPaymentSummary(
            total_count=0, total_amount_cents=0, total_amount_usd=Decimal("0")
        )
        return [], empty

    all_rows: list[UnifiedPaymentRowRead] = []
    total_count = 0
    total_amount_cents = 0
    offset = 0
    while True:
        page, count, summary = fetch_unified_page(
            db,
            scope=scope,
            owner=owner,
            method=method,
            filters=filters,
            limit=page_size,
            offset=offset,
        )
        if offset == 0:
            total_count = count
            total_amount_cents = summary.total_amount_cents
        all_rows.extend(page)
        offset += len(page)
        if offset >= total_count or not page:
            break

    summary = OwnerPaymentSummary(
        total_count=total_count,
        total_amount_cents=total_amount_cents,
        total_amount_usd=cents_to_usd(total_amount_cents),
    )
    return all_rows, summary


def validate_unified_method_for_scope(scope: ScopeSlug, method: str) -> str:
    method_slug = (method or "all").strip().lower()
    if method_slug == "all":
        return method_slug
    if scope == "union":
        validate_union_method_type(method_slug)
        return method_slug
    if method_slug in UNION_METHOD_TYPES and scope == "owner":
        raise HTTPException(
            400, f"Method '{method_slug}' is not available for owner scope."
        )
    allowed_all = set(OWNER_INGEST_METHODS) | {"stripe"} | UNION_METHOD_TYPES
    if method_slug not in allowed_all:
        raise HTTPException(400, f"Unknown method '{method_slug}'")
    return method_slug
