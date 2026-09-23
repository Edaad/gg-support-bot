"""Unified payments list: merge owner-ingested + union TR-checked rows."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from api.payments_helpers import (
    OWNER_INGEST_METHODS,
    OWNER_METHODS_BY_OWNER,
    OWNER_VARIANT_COLUMNS,
    aggregate_owner_payment_query,
    apply_owner_ingest_filters,
    apply_owner_stripe_filters,
    cents_to_usd,
    crypto_occurred_at,
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
UNION_METHOD_TYPES: frozenset[str] = frozenset(
    {"zelle", "cashapp", "applepay", "venmo"}
)

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


@dataclass(frozen=True)
class PaymentCandidate:
    """Sort key + identity for one row, before it is loaded and enriched."""

    sort_key: tuple
    spec: PaymentSourceSpec
    row_id: int


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
    ts = (
        occurred_at.timestamp()
        if occurred_at.tzinfo
        else occurred_at.replace(tzinfo=None).timestamp()
    )
    return (-ts, source_kind, -row_id)


def _stripe_occurred_at(row: StripeCheckoutSession) -> datetime:
    return row.completed_at or row.created_at


def _ingest_occurred_at(method_slug: str, read_payload: dict[str, Any]) -> Any:
    """Time for unified list: crypto prefers paid_at; Stripe completed_at; else created_at."""
    created_at = read_payload["created_at"]
    if method_slug == "stripe":
        return read_payload.get("completed_at") or created_at
    if method_slug == "crypto":
        return crypto_occurred_at(read_payload) or created_at
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
    can_bind = method_slug != "stripe" and status == "unbound"
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
    gg_nickname = lookup_gg_nickname(db, int(row.club_id), gg_id) if gg_id else None
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


def _candidate(
    spec: PaymentSourceSpec, row_id: int, occurred_at: datetime
) -> PaymentCandidate:
    source_key = spec.union_method_type or spec.method_slug or spec.kind
    return PaymentCandidate(
        sort_key=_sort_key(occurred_at, source_key, row_id),
        spec=spec,
        row_id=row_id,
    )


def _fetch_stripe_keys(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[PaymentCandidate]]:
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
        base.with_entities(
            StripeCheckoutSession.id,
            StripeCheckoutSession.created_at,
            StripeCheckoutSession.completed_at,
        )
        .order_by(
            StripeCheckoutSession.created_at.desc(),
            StripeCheckoutSession.id.desc(),
        )
        .limit(fetch_limit)
        .all()
    )
    candidates = [
        _candidate(spec, int(row.id), row.completed_at or row.created_at)
        for row in rows
    ]
    return total_count, total_amount_cents, candidates


def _fetch_ingest_keys(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[PaymentCandidate]]:
    method_slug = spec.method_slug or spec.kind
    payment_cls = OWNER_INGEST_METHODS[method_slug]
    base = db.query(payment_cls)
    base = apply_owner_ingest_filters(
        base,
        payment_cls,
        method_owner=spec.owner_slug or "",
        variant=filters.variant,
        from_dt=filters.from_dt,
        to_dt=filters.to_dt,
        q=filters.q,
        club_id=filters.club_id,
    )
    total_count, total_amount_cents = aggregate_owner_payment_query(
        base, payment_cls.amount_cents
    )
    columns = [payment_cls.id, payment_cls.created_at]
    if method_slug == "crypto":
        columns.append(payment_cls.paid_at)
    rows = (
        base.with_entities(*columns)
        .order_by(payment_cls.created_at.desc(), payment_cls.id.desc())
        .limit(fetch_limit)
        .all()
    )
    candidates = []
    for row in rows:
        if method_slug == "crypto":
            occurred_at = (
                crypto_occurred_at(
                    {"paid_at": row.paid_at, "created_at": row.created_at}
                )
                or row.created_at
            )
        else:
            occurred_at = row.created_at
        candidates.append(_candidate(spec, int(row.id), occurred_at))
    return total_count, total_amount_cents, candidates


def _union_query(db: Session, spec: PaymentSourceSpec, filters: UnifiedPaymentFilters):
    return union_list_query(
        db,
        method_type=spec.union_method_type or "",
        deposit_union=filters.deposit_union,
        pool_pay_type="union_method",
        trade_record_checked=True,
        variant=filters.variant,
        from_dt=filters.from_dt,
        to_dt=filters.to_dt,
        q=filters.q,
        club_id=filters.club_id,
    )


def _fetch_union_keys(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[PaymentCandidate]]:
    query = _union_query(db, spec, filters)
    summary = union_list_summary(query)
    total_count = int(summary.total_count)
    total_amount_cents = _amount_to_cents(summary.total_amount)
    rows = (
        query.enable_eagerloads(False)
        .with_entities(ManualDepositRequest.id, ManualDepositRequest.created_at)
        .order_by(
            ManualDepositRequest.created_at.desc(),
            ManualDepositRequest.id.desc(),
        )
        .limit(fetch_limit)
        .all()
    )
    candidates = [_candidate(spec, int(row.id), row.created_at) for row in rows]
    return total_count, total_amount_cents, candidates


def _fetch_source_keys(
    db: Session,
    spec: PaymentSourceSpec,
    filters: UnifiedPaymentFilters,
    fetch_limit: int,
) -> tuple[int, int, list[PaymentCandidate]]:
    """Per-source totals plus sort keys only -- no row bodies, no enrichment."""
    if spec.kind == "stripe":
        return _fetch_stripe_keys(db, spec, filters, fetch_limit)
    if spec.kind == "union_manual":
        return _fetch_union_keys(db, spec, filters, fetch_limit)
    return _fetch_ingest_keys(db, spec, filters, fetch_limit)


def _hydrate_stripe(
    db: Session, spec: PaymentSourceSpec, ids: list[int]
) -> dict[int, UnifiedPaymentRowRead]:
    build_stripe_read, _, _ = _owner_read_helpers()
    rows = (
        db.query(StripeCheckoutSession).filter(StripeCheckoutSession.id.in_(ids)).all()
    )
    owner_slug = spec.owner_slug or "round-table"
    return {
        int(row.id): _ingest_to_unified(
            db,
            method_slug="stripe",
            owner_slug=owner_slug,
            read_payload=build_stripe_read(db, row).model_dump(mode="json"),
        )
        for row in rows
    }


def _hydrate_ingest(
    db: Session, spec: PaymentSourceSpec, ids: list[int]
) -> dict[int, UnifiedPaymentRowRead]:
    _, build_read_by_method, read_model_by_method = _owner_read_helpers()
    method_slug = spec.method_slug or spec.kind
    payment_cls = OWNER_INGEST_METHODS[method_slug]
    rows = db.query(payment_cls).filter(payment_cls.id.in_(ids)).all()
    build_read = build_read_by_method[method_slug]
    read_model = read_model_by_method[method_slug]
    hydrated = {}
    for row in rows:
        payload = read_model.model_validate(build_read(db, row)).model_dump(mode="json")
        hydrated[int(row.id)] = _ingest_to_unified(
            db,
            method_slug=method_slug,
            owner_slug=spec.owner_slug or "",
            read_payload=payload,
        )
    return hydrated


def _hydrate_union(
    db: Session, spec: PaymentSourceSpec, ids: list[int]
) -> dict[int, UnifiedPaymentRowRead]:
    rows = (
        db.query(ManualDepositRequest)
        .options(joinedload(ManualDepositRequest.club))
        .filter(ManualDepositRequest.id.in_(ids))
        .all()
    )
    return {int(row.id): _union_to_unified(db, row) for row in rows}


def _hydrate_source(
    db: Session, spec: PaymentSourceSpec, ids: list[int]
) -> dict[int, UnifiedPaymentRowRead]:
    if spec.kind == "stripe":
        return _hydrate_stripe(db, spec, ids)
    if spec.kind == "union_manual":
        return _hydrate_union(db, spec, ids)
    return _hydrate_ingest(db, spec, ids)


def _merge_candidates(
    candidates: list[PaymentCandidate], offset: int, limit: int
) -> list[PaymentCandidate]:
    candidates.sort(key=lambda candidate: candidate.sort_key)
    return candidates[offset : offset + limit]


def aggregate_unified_summary(
    db: Session,
    sources: list[PaymentSourceSpec],
    filters: UnifiedPaymentFilters,
) -> OwnerPaymentSummary:
    total_count = 0
    total_amount_cents = 0
    for spec in sources:
        count, amount_cents, _ = _fetch_source_keys(db, spec, filters, fetch_limit=0)
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
    candidates: list[PaymentCandidate] = []
    total_count = 0
    total_amount_cents = 0
    for spec in sources:
        count, amount_cents, keys = _fetch_source_keys(db, spec, filters, fetch_limit)
        total_count += count
        total_amount_cents += amount_cents
        candidates.extend(keys)

    page = _merge_candidates(candidates, offset, limit)
    ids_by_spec: dict[PaymentSourceSpec, list[int]] = defaultdict(list)
    for candidate in page:
        ids_by_spec[candidate.spec].append(candidate.row_id)
    hydrated: dict[tuple[PaymentSourceSpec, int], UnifiedPaymentRowRead] = {}
    for spec, ids in ids_by_spec.items():
        for row_id, row in _hydrate_source(db, spec, ids).items():
            hydrated[(spec, row_id)] = row

    items = [
        hydrated[(candidate.spec, candidate.row_id)]
        for candidate in page
        if (candidate.spec, candidate.row_id) in hydrated
    ]
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


def list_all_scope_variant_options(
    db: Session,
    method: str,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    club_id: int | None = None,
) -> list[dict[str, str]]:
    """Distinct variant options for All-scope payments (cross-owner + union)."""
    from api.payments_helpers import (
        distinct_owner_ingest_variants,
        distinct_owner_stripe_variants,
    )

    method_slug = validate_unified_method_for_scope("all", method)
    if method_slug == "all":
        raise HTTPException(
            400, "Use method=all on the payments endpoint, not variants."
        )

    seen: dict[str, str] = {}

    if method_slug == "stripe":
        for row in distinct_owner_stripe_variants(
            db, from_dt=from_dt, to_dt=to_dt, club_id=club_id
        ):
            seen[row["id"]] = row["label"]
    elif method_slug in OWNER_INGEST_METHODS:
        payment_cls = OWNER_INGEST_METHODS[method_slug]
        for owner_slug in ALL_OWNERS:
            if method_slug not in OWNER_METHODS_BY_OWNER[owner_slug]:
                continue
            for value in distinct_owner_ingest_variants(
                db,
                payment_cls,
                method_owner=owner_slug,
                from_dt=from_dt,
                to_dt=to_dt,
                club_id=club_id,
            ):
                seen[value] = value

    if method_slug in UNION_METHOD_TYPES:
        query = union_list_query(
            db,
            method_type=method_slug,
            deposit_union=None,
            pool_pay_type="union_method",
            trade_record_checked=True,
            from_dt=from_dt,
            to_dt=to_dt,
            club_id=club_id,
        )
        rows = (
            query.with_entities(ManualDepositRequest.variant_name)
            .distinct()
            .order_by(ManualDepositRequest.variant_name.asc())
            .all()
        )
        for row in rows:
            if row[0]:
                value = str(row[0])
                seen[value] = value

    return [
        {"value": key, "label": label}
        for key, label in sorted(seen.items(), key=lambda x: x[1].lower())
    ]


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
