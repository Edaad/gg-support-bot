from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import SubOptionCreate, SubOptionUpdate, SubOptionRead
from db.connection import get_db_dependency
from db.models import PaymentMethod, PaymentSubOption

router = APIRouter(prefix="/api", tags=["sub_options"], dependencies=[Depends(get_current_admin)])


@router.get("/methods/{method_id}/sub-options", response_model=List[SubOptionRead])
def list_sub_options(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    return [
        SubOptionRead.model_validate(s)
        for s in sorted(method.sub_options, key=lambda s: s.sort_order)
    ]


@router.post("/methods/{method_id}/sub-options", response_model=SubOptionRead, status_code=201)
def create_sub_option(method_id: int, body: SubOptionCreate, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    sub = PaymentSubOption(method_id=method_id, **body.model_dump())
    db.add(sub)
    db.flush()
    db.refresh(sub)
    return SubOptionRead.model_validate(sub)


@router.put("/sub-options/{sub_id}", response_model=SubOptionRead)
def update_sub_option(sub_id: int, body: SubOptionUpdate, db: Session = Depends(get_db_dependency)):
    sub = db.query(PaymentSubOption).get(sub_id)
    if not sub:
        raise HTTPException(404, "Sub-option not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(sub, field, value)
    db.flush()
    db.refresh(sub)
    return SubOptionRead.model_validate(sub)


@router.delete("/sub-options/{sub_id}", status_code=204)
def delete_sub_option(sub_id: int, db: Session = Depends(get_db_dependency)):
    sub = db.query(PaymentSubOption).get(sub_id)
    if not sub:
        raise HTTPException(404, "Sub-option not found")
    db.delete(sub)
