from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import TierCreate, TierUpdate, TierRead
from db.connection import get_db_dependency
from db.models import PaymentMethod, PaymentMethodTier

router = APIRouter(prefix="/api", tags=["tiers"], dependencies=[Depends(get_current_admin)])


@router.get("/methods/{method_id}/tiers", response_model=List[TierRead])
def list_tiers(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    return [
        TierRead.model_validate(t)
        for t in sorted(method.tiers, key=lambda t: t.sort_order)
    ]


@router.post("/methods/{method_id}/tiers", response_model=TierRead, status_code=201)
def create_tier(method_id: int, body: TierCreate, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    tier = PaymentMethodTier(method_id=method_id, **body.model_dump())
    db.add(tier)
    db.flush()
    db.refresh(tier)
    return TierRead.model_validate(tier)


@router.put("/tiers/{tier_id}", response_model=TierRead)
def update_tier(tier_id: int, body: TierUpdate, db: Session = Depends(get_db_dependency)):
    tier = db.query(PaymentMethodTier).get(tier_id)
    if not tier:
        raise HTTPException(404, "Tier not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tier, field, value)
    db.flush()
    db.refresh(tier)
    return TierRead.model_validate(tier)


@router.delete("/tiers/{tier_id}", status_code=204)
def delete_tier(tier_id: int, db: Session = Depends(get_db_dependency)):
    tier = db.query(PaymentMethodTier).get(tier_id)
    if not tier:
        raise HTTPException(404, "Tier not found")
    db.delete(tier)
