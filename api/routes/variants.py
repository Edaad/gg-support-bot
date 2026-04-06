from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import VariantCreate, VariantUpdate, VariantRead
from db.connection import get_db_dependency
from db.models import PaymentMethod, PaymentMethodTier, MethodVariant

router = APIRouter(prefix="/api", tags=["variants"], dependencies=[Depends(get_current_admin)])


# ── Method-level variants (tier_id IS NULL) ───────────────────────────────────

@router.get("/methods/{method_id}/variants", response_model=List[VariantRead])
def list_method_variants(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    variants = (
        db.query(MethodVariant)
        .filter_by(method_id=method_id, tier_id=None)
        .order_by(MethodVariant.sort_order)
        .all()
    )
    return [VariantRead.model_validate(v) for v in variants]


@router.post("/methods/{method_id}/variants", response_model=VariantRead, status_code=201)
def create_method_variant(method_id: int, body: VariantCreate, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    if body.weight < 1:
        raise HTTPException(400, "Weight must be at least 1")
    variant = MethodVariant(method_id=method_id, tier_id=None, **body.model_dump())
    db.add(variant)
    db.flush()
    db.refresh(variant)
    return VariantRead.model_validate(variant)


# ── Tier-level variants ───────────────────────────────────────────────────────

@router.get("/tiers/{tier_id}/variants", response_model=List[VariantRead])
def list_tier_variants(tier_id: int, db: Session = Depends(get_db_dependency)):
    tier = db.query(PaymentMethodTier).get(tier_id)
    if not tier:
        raise HTTPException(404, "Tier not found")
    variants = (
        db.query(MethodVariant)
        .filter_by(tier_id=tier_id)
        .order_by(MethodVariant.sort_order)
        .all()
    )
    return [VariantRead.model_validate(v) for v in variants]


@router.post("/tiers/{tier_id}/variants", response_model=VariantRead, status_code=201)
def create_tier_variant(tier_id: int, body: VariantCreate, db: Session = Depends(get_db_dependency)):
    tier = db.query(PaymentMethodTier).get(tier_id)
    if not tier:
        raise HTTPException(404, "Tier not found")
    if body.weight < 1:
        raise HTTPException(400, "Weight must be at least 1")
    variant = MethodVariant(method_id=tier.method_id, tier_id=tier_id, **body.model_dump())
    db.add(variant)
    db.flush()
    db.refresh(variant)
    return VariantRead.model_validate(variant)


@router.put("/variants/{variant_id}", response_model=VariantRead)
def update_variant(variant_id: int, body: VariantUpdate, db: Session = Depends(get_db_dependency)):
    variant = db.query(MethodVariant).get(variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")
    data = body.model_dump(exclude_unset=True)
    if "weight" in data and data["weight"] < 1:
        raise HTTPException(400, "Weight must be at least 1")
    for field, value in data.items():
        setattr(variant, field, value)
    db.flush()
    db.refresh(variant)
    return VariantRead.model_validate(variant)


@router.delete("/variants/{variant_id}", status_code=204)
def delete_variant(variant_id: int, db: Session = Depends(get_db_dependency)):
    variant = db.query(MethodVariant).get(variant_id)
    if not variant:
        raise HTTPException(404, "Variant not found")
    db.delete(variant)
