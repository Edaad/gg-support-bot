from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import MethodCreate, MethodUpdate, MethodRead
from db.connection import get_db_dependency
from db.models import Club, PaymentMethod

router = APIRouter(prefix="/api", tags=["methods"], dependencies=[Depends(get_current_admin)])


@router.get("/clubs/{club_id}/methods", response_model=List[MethodRead])
def list_methods(club_id: int, direction: str = None, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    q = db.query(PaymentMethod).filter_by(club_id=club_id)
    if direction:
        q = q.filter_by(direction=direction)
    methods = q.order_by(PaymentMethod.sort_order).all()
    return [MethodRead.model_validate(m) for m in methods]


@router.post("/clubs/{club_id}/methods", response_model=MethodRead, status_code=201)
def create_method(club_id: int, body: MethodCreate, db: Session = Depends(get_db_dependency)):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    if body.direction not in ("deposit", "cashout"):
        raise HTTPException(400, "direction must be 'deposit' or 'cashout'")
    method = PaymentMethod(club_id=club_id, **body.model_dump())
    db.add(method)
    db.flush()
    db.refresh(method)
    return MethodRead.model_validate(method)


@router.get("/methods/{method_id}", response_model=MethodRead)
def get_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    return MethodRead.model_validate(method)


@router.put("/methods/{method_id}", response_model=MethodRead)
def update_method(method_id: int, body: MethodUpdate, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(method, field, value)
    db.flush()
    db.refresh(method)
    return MethodRead.model_validate(method)


@router.delete("/methods/{method_id}", status_code=204)
def delete_method(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    db.delete(method)


@router.post("/methods/{method_id}/reset-accumulated", response_model=MethodRead)
def reset_accumulated(method_id: int, db: Session = Depends(get_db_dependency)):
    method = db.query(PaymentMethod).get(method_id)
    if not method:
        raise HTTPException(404, "Method not found")
    method.accumulated_amount = 0
    db.flush()
    db.refresh(method)
    return MethodRead.model_validate(method)


@router.put("/clubs/{club_id}/methods/reorder")
def reorder_methods(club_id: int, body: dict, db: Session = Depends(get_db_dependency)):
    """Body: {"order": [method_id, method_id, ...]}"""
    order = body.get("order", [])
    for idx, method_id in enumerate(order):
        m = db.query(PaymentMethod).filter_by(id=method_id, club_id=club_id).first()
        if m:
            m.sort_order = idx
