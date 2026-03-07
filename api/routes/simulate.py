from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_admin
from api.schemas import SimulateResponse, SimulateMethodOut, SubOptionRead
from db.connection import get_db_dependency
from db.models import Club, PaymentMethod

router = APIRouter(prefix="/api", tags=["simulate"], dependencies=[Depends(get_current_admin)])


@router.get("/clubs/{club_id}/simulate/{direction}", response_model=SimulateResponse)
def simulate_flow(club_id: int, direction: str, db: Session = Depends(get_db_dependency)):
    if direction not in ("deposit", "cashout"):
        raise HTTPException(400, "direction must be 'deposit' or 'cashout'")
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    methods = (
        db.query(PaymentMethod)
        .filter_by(club_id=club_id, direction=direction, is_active=True)
        .order_by(PaymentMethod.sort_order)
        .all()
    )
    out = []
    for m in methods:
        subs = [SubOptionRead.model_validate(s) for s in sorted(m.sub_options, key=lambda s: s.sort_order) if s.is_active]
        out.append(
            SimulateMethodOut(
                id=m.id,
                name=m.name,
                slug=m.slug,
                min_amount=m.min_amount,
                max_amount=m.max_amount,
                has_sub_options=m.has_sub_options,
                response_type=m.response_type,
                response_text=m.response_text,
                response_caption=m.response_caption,
                sub_options=subs,
            )
        )
    return SimulateResponse(club_name=club.name, direction=direction, methods=out)
