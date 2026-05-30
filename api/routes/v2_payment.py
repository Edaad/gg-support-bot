"""Greenfield payment config API — /api/v2 only; does not touch legacy tables."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payment_v2_helpers import (
    DEFAULT_TIER_LABEL,
    RESPONSE_UPDATE_FIELDS,
    create_empty_default_variant,
    ensure_legacy_tier_before_new_variant,
    method_needs_variants,
    strip_response_from_tier_payload,
    tier_variant_count,
    validate_all_method_tiers,
    validate_tier_amount_band,
)
from api.schemas_v2 import (
    ClubPaymentMethodCreate,
    ClubPaymentMethodRead,
    ClubPaymentMethodUpdate,
    ClubPaymentSubOptionCreate,
    ClubPaymentSubOptionRead,
    ClubPaymentSubOptionUpdate,
    ClubPaymentTierCreate,
    ClubPaymentTierRead,
    ClubPaymentTierUpdate,
    ClubPaymentTierVariantCreate,
    ClubPaymentTierVariantRead,
    ClubPaymentTierVariantUpdate,
)
from db.connection import get_db_dependency
from db.models import (
    Club,
    ClubPaymentMethod,
    ClubPaymentSubOption,
    ClubPaymentTier,
    ClubPaymentTierVariant,
)

router = APIRouter(prefix="/api/v2", tags=["payment-v2"], dependencies=[Depends(get_current_admin)])


def _method_query(db: Session):
    return db.query(ClubPaymentMethod).options(
        joinedload(ClubPaymentMethod.tiers).joinedload(ClubPaymentTier.variants),
        joinedload(ClubPaymentMethod.sub_options),
    )


def _read_method(method: ClubPaymentMethod) -> ClubPaymentMethodRead:
    return ClubPaymentMethodRead.model_validate(method)


def _get_method(db: Session, method_id: int) -> ClubPaymentMethod:
    method = _method_query(db).filter(ClubPaymentMethod.id == method_id).first()
    if not method:
        raise HTTPException(404, "Method not found")
    return method


def _get_tier(db: Session, tier_id: int) -> ClubPaymentTier:
    tier = db.query(ClubPaymentTier).get(tier_id)
    if not tier:
        raise HTTPException(404, "Tier not found")
    return tier


def _get_variant(db: Session, variant_id: int) -> ClubPaymentTierVariant:
    variant = db.query(ClubPaymentTierVariant).get(variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")
    return variant


# ── Methods ─────────────────────────────────────────────────────────────────


@router.get("/clubs/{club_id}/methods", response_model=List[ClubPaymentMethodRead])
def list_methods(club_id: int, direction: str | None = None, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    q = _method_query(db).filter_by(club_id=club_id)
    if direction:
        q = q.filter_by(direction=direction)
    methods = q.order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id).all()
    return [_read_method(m) for m in methods]


@router.post("/clubs/{club_id}/methods", response_model=ClubPaymentMethodRead, status_code=201)
def create_method(club_id: int, body: ClubPaymentMethodCreate, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    if body.direction not in ("deposit", "cashout"):
        raise HTTPException(400, "direction must be 'deposit' or 'cashout'")
    method = ClubPaymentMethod(club_id=club_id, **body.model_dump())
    db.add(method)
    db.flush()
    tier = ClubPaymentTier(
        method_id=method.id,
        label=DEFAULT_TIER_LABEL,
        min_amount=method.min_amount,
        max_amount=method.max_amount,
        sort_order=0,
    )
    db.add(tier)
    db.flush()
    if not method.has_sub_options:
        create_empty_default_variant(db, tier)
        db.flush()
    method = _get_method(db, method.id)
    return _read_method(method)


@router.get("/methods/{method_id}", response_model=ClubPaymentMethodRead)
def get_method(method_id: int, db: Session = Depends(get_db_dependency)):
    return _read_method(_get_method(db, method_id))


@router.put("/methods/{method_id}", response_model=ClubPaymentMethodRead)
def update_method(method_id: int, body: ClubPaymentMethodUpdate, db: Session = Depends(get_db_dependency)):
    method = _get_method(db, method_id)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(method, field, value)
    if method.min_amount is not None and method.max_amount is not None and method.min_amount > method.max_amount:
        raise HTTPException(400, "Method min amount cannot be greater than max amount.")
    db.flush()
    method = _get_method(db, method_id)
    if {"min_amount", "max_amount"}.intersection(data.keys()):
        validate_all_method_tiers(method)
    method = _get_method(db, method_id)
    return _read_method(method)


@router.delete("/methods/{method_id}", status_code=204)
def delete_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(ClubPaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    db.delete(method)


@router.post("/methods/{method_id}/reset-accumulated", response_model=ClubPaymentMethodRead)
def reset_accumulated(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_method(db, method_id)
    method.accumulated_amount = 0
    db.flush()
    method = _get_method(db, method_id)
    return _read_method(method)


@router.put("/clubs/{club_id}/methods/reorder")
def reorder_methods(club_id: int, body: dict, db: Session = Depends(get_db_dependency)):
    order = body.get("order", [])
    for idx, method_id in enumerate(order):
        m = db.query(ClubPaymentMethod).filter_by(id=method_id, club_id=club_id).first()
        if m:
            m.sort_order = idx
    return {"ok": True}


# ── Tiers ───────────────────────────────────────────────────────────────────


@router.get("/methods/{method_id}/tiers", response_model=List[ClubPaymentTierRead])
def list_tiers(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_method(db, method_id)
    tiers = sorted(method.tiers, key=lambda t: (t.sort_order, t.id))
    return [ClubPaymentTierRead.model_validate(t) for t in tiers]


@router.post("/methods/{method_id}/tiers", response_model=ClubPaymentTierRead, status_code=201)
def create_tier(method_id: int, body: ClubPaymentTierCreate, db: Session = Depends(get_db_dependency)):
    method = _get_method(db, method_id)
    tier_data = body.model_dump()
    if method_needs_variants(method):
        tier_data = strip_response_from_tier_payload(tier_data)
    validate_tier_amount_band(
        method,
        tier_data.get("min_amount"),
        tier_data.get("max_amount"),
        method.tiers,
        tier_label=tier_data.get("label"),
    )
    tier = ClubPaymentTier(method_id=method_id, **tier_data)
    db.add(tier)
    db.flush()
    if method_needs_variants(method):
        create_empty_default_variant(db, tier)
        db.flush()
    db.refresh(tier)
    return ClubPaymentTierRead.model_validate(tier)


@router.put("/tiers/{tier_id}", response_model=ClubPaymentTierRead)
def update_tier(tier_id: int, body: ClubPaymentTierUpdate, db: Session = Depends(get_db_dependency)):
    tier = _get_tier(db, tier_id)
    method = _get_method(db, tier.method_id)
    data = body.model_dump(exclude_unset=True)
    if method_needs_variants(method):
        blocked = RESPONSE_UPDATE_FIELDS.intersection(data.keys())
        if blocked:
            raise HTTPException(
                400,
                "Player response is configured on variants, not tiers. Edit variants instead.",
            )
    merged_min = data.get("min_amount", tier.min_amount)
    merged_max = data.get("max_amount", tier.max_amount)
    merged_label = data.get("label", tier.label)
    validate_tier_amount_band(
        method,
        merged_min,
        merged_max,
        method.tiers,
        exclude_tier_id=tier.id,
        tier_label=merged_label,
    )
    for field, value in data.items():
        setattr(tier, field, value)
    db.flush()
    db.refresh(tier)
    return ClubPaymentTierRead.model_validate(tier)


@router.delete("/tiers/{tier_id}", status_code=204)
def delete_tier(tier_id: int, db: Session = Depends(get_db_dependency)):
    tier = _get_tier(db, tier_id)
    remaining = (
        db.query(ClubPaymentTier)
        .filter_by(method_id=tier.method_id)
        .filter(ClubPaymentTier.id != tier_id)
        .count()
    )
    if remaining == 0:
        raise HTTPException(400, "Cannot delete the last tier on a method")
    db.delete(tier)


# ── Tier variants ───────────────────────────────────────────────────────────


@router.get("/tiers/{tier_id}/variants", response_model=List[ClubPaymentTierVariantRead])
def list_tier_variants(tier_id: int, db: Session = Depends(get_db_dependency)):
    tier = _get_tier(db, tier_id)
    variants = sorted(tier.variants, key=lambda v: (v.sort_order, v.id))
    return [ClubPaymentTierVariantRead.model_validate(v) for v in variants]


@router.post("/tiers/{tier_id}/variants", response_model=ClubPaymentTierVariantRead, status_code=201)
def create_tier_variant(tier_id: int, body: ClubPaymentTierVariantCreate, db: Session = Depends(get_db_dependency)):
    tier = _get_tier(db, tier_id)
    if body.weight < 1:
        raise HTTPException(400, "Weight must be at least 1")
    ensure_legacy_tier_before_new_variant(db, tier)
    variant = ClubPaymentTierVariant(
        method_id=tier.method_id,
        tier_id=tier_id,
        **body.model_dump(),
    )
    db.add(variant)
    db.flush()
    db.refresh(variant)
    return ClubPaymentTierVariantRead.model_validate(variant)


@router.put("/variants/{variant_id}", response_model=ClubPaymentTierVariantRead)
def update_variant(variant_id: int, body: ClubPaymentTierVariantUpdate, db: Session = Depends(get_db_dependency)):
    variant = _get_variant(db, variant_id)
    data = body.model_dump(exclude_unset=True)
    if "weight" in data and data["weight"] < 1:
        raise HTTPException(400, "Weight must be at least 1")
    if "tier_id" in data:
        new_tier_id = data["tier_id"]
        if new_tier_id is not None:
            tier = _get_tier(db, new_tier_id)
            if tier.method_id != variant.method_id:
                raise HTTPException(400, "Invalid tier for this variant")
            data["method_id"] = tier.method_id
    for field, value in data.items():
        setattr(variant, field, value)
    db.flush()
    db.refresh(variant)
    return ClubPaymentTierVariantRead.model_validate(variant)


@router.delete("/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: int, db: Session = Depends(get_db_dependency)):
    variant = _get_variant(db, variant_id)
    method = db.query(ClubPaymentMethod).get(variant.method_id)
    if method and method_needs_variants(method):
        remaining = tier_variant_count(db, variant.tier_id)
        if remaining <= 1:
            raise HTTPException(400, "Cannot delete the last variant on this tier")
    db.delete(variant)


# ── Sub-options ─────────────────────────────────────────────────────────────


@router.get("/methods/{method_id}/sub-options", response_model=List[ClubPaymentSubOptionRead])
def list_sub_options(method_id: int, db: Session = Depends(get_db_dependency)):
    method = _get_method(db, method_id)
    subs = sorted(method.sub_options, key=lambda s: (s.sort_order, s.id))
    return [ClubPaymentSubOptionRead.model_validate(s) for s in subs]


@router.post("/methods/{method_id}/sub-options", response_model=ClubPaymentSubOptionRead, status_code=201)
def create_sub_option(method_id: int, body: ClubPaymentSubOptionCreate, db: Session = Depends(get_db_dependency)):
    _get_method(db, method_id)
    sub = ClubPaymentSubOption(method_id=method_id, **body.model_dump())
    db.add(sub)
    db.flush()
    db.refresh(sub)
    return ClubPaymentSubOptionRead.model_validate(sub)


@router.put("/sub-options/{sub_option_id}", response_model=ClubPaymentSubOptionRead)
def update_sub_option(sub_option_id: int, body: ClubPaymentSubOptionUpdate, db: Session = Depends(get_db_dependency)):
    sub = db.query(ClubPaymentSubOption).get(sub_option_id)
    if not sub:
        raise HTTPException(404, "Sub-option not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(sub, field, value)
    db.flush()
    db.refresh(sub)
    return ClubPaymentSubOptionRead.model_validate(sub)


@router.delete("/sub-options/{sub_option_id}", status_code=204)
def delete_sub_option(sub_option_id: int, db: Session = Depends(get_db_dependency)):
    sub = db.query(ClubPaymentSubOption).get(sub_option_id)
    if not sub:
        raise HTTPException(404, "Sub-option not found")
    db.delete(sub)
