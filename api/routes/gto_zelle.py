"""Account-manager list of ClubGTO Zelle deposit variants."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import ROLE_ACCOUNT_MANAGER, get_current_admin
from bot.services.variant_pause import (
    list_gto_zelle_cards,
    pause_gto_zelle_variant,
    resume_gto_zelle_variant,
)
from db.connection import get_db_dependency

router = APIRouter(prefix="/api/v2", tags=["gto-zelle"])


class GtoZelleCard(BaseModel):
    id: int
    label: str
    tag: Optional[str] = None
    tier_id: int
    tier_label: str
    tier_min: Optional[Decimal] = None
    tier_max: Optional[Decimal] = None
    state: Literal["active", "paused", "disabled"]
    paused_until: Optional[datetime] = None


def require_account_manager(role: str = Depends(get_current_admin)) -> str:
    if role != ROLE_ACCOUNT_MANAGER:
        raise HTTPException(403, "Account manager only")
    return role


def _card_or_error(fn, db: Session, variant_id: int) -> dict:
    try:
        return fn(db, variant_id)
    except LookupError as exc:
        raise HTTPException(404, "Variant not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/gto-zelle", response_model=List[GtoZelleCard])
def list_gto_zelle(
    db: Session = Depends(get_db_dependency),
    _role: str = Depends(require_account_manager),
):
    return list_gto_zelle_cards(db)


@router.post("/gto-zelle/{variant_id}/pause", response_model=GtoZelleCard)
def pause_gto_zelle(
    variant_id: int,
    db: Session = Depends(get_db_dependency),
    _role: str = Depends(require_account_manager),
):
    return _card_or_error(pause_gto_zelle_variant, db, variant_id)


@router.post("/gto-zelle/{variant_id}/resume", response_model=GtoZelleCard)
def resume_gto_zelle(
    variant_id: int,
    db: Session = Depends(get_db_dependency),
    _role: str = Depends(require_account_manager),
):
    return _card_or_error(resume_gto_zelle_variant, db, variant_id)
