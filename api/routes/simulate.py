from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from api.auth import get_current_admin
from api.payment_v2_helpers import DEFAULT_TIER_LABEL
from api.schemas import SimulateResponse, SimulateMethodOut, SubOptionRead
from db.connection import get_db_dependency
from db.models import Club, ClubPaymentMethod, ClubPaymentTier

router = APIRouter(prefix="/api", tags=["simulate"], dependencies=[Depends(get_current_admin)])


def _default_tier(method: ClubPaymentMethod) -> ClubPaymentTier | None:
    tiers = sorted(method.tiers or [], key=lambda t: (t.sort_order, t.id))
    for tier in tiers:
        if tier.label == DEFAULT_TIER_LABEL:
            return tier
    return tiers[0] if tiers else None


def _variant_preview(tier: ClubPaymentTier | None) -> tuple[str | None, str | None, str | None]:
    if tier is None:
        return None, None, None
    variants = sorted(tier.variants or [], key=lambda v: (v.sort_order, v.id))
    if not variants:
        return None, None, None
    variant = variants[0]
    return variant.response_type, variant.response_text, variant.response_caption


@router.get("/clubs/{club_id}/simulate/{direction}", response_model=SimulateResponse)
def simulate_flow(club_id: int, direction: str, db: Session = Depends(get_db_dependency)):
    if direction not in ("deposit", "cashout"):
        raise HTTPException(400, "direction must be 'deposit' or 'cashout'")
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")
    methods = (
        db.query(ClubPaymentMethod)
        .options(
            joinedload(ClubPaymentMethod.sub_options),
            joinedload(ClubPaymentMethod.tiers).joinedload(ClubPaymentTier.variants),
        )
        .filter_by(club_id=club_id, direction=direction, is_active=True)
        .order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id)
        .all()
    )
    out = []
    for m in methods:
        subs = [
            SubOptionRead.model_validate(s)
            for s in sorted(m.sub_options, key=lambda s: s.sort_order)
            if s.is_active
        ]
        response_type: str | None = None
        response_text: str | None = None
        response_caption: str | None = None
        if not m.has_sub_options:
            response_type, response_text, response_caption = _variant_preview(_default_tier(m))
        out.append(
            SimulateMethodOut(
                id=m.id,
                name=m.name,
                slug=m.slug,
                min_amount=m.min_amount,
                max_amount=m.max_amount,
                has_sub_options=m.has_sub_options,
                response_type=response_type,
                response_text=response_text,
                response_caption=response_caption,
                sub_options=subs,
            )
        )
    return SimulateResponse(club_name=club.name, direction=direction, methods=out)
