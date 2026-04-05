from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import VariantCreate, VariantUpdate, VariantRead
from db.connection import get_db_dependency
from db.models import PaymentMethod, MethodVariant

router = APIRouter(prefix="/api", tags=["variants"], dependencies=[Depends(get_current_admin)])


@router.get("/methods/{method_id}/variants", response_model=List[VariantRead])
def list_variants(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    return [
        VariantRead.model_validate(v)
        for v in sorted(method.variants, key=lambda v: v.sort_order)
    ]


@router.post("/methods/{method_id}/variants", response_model=VariantRead, status_code=201)
def create_variant(method_id: int, body: VariantCreate, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    if body.weight < 1:
        raise HTTPException(400, "Weight must be at least 1")
    variant = MethodVariant(method_id=method_id, **body.model_dump())
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
