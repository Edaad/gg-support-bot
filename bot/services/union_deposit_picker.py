"""Deduplicated union-type deposit picker (Zelle / Cash App / Apple Pay)."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from sqlalchemy.orm import joinedload

from bot.services.club_payment_v2 import get_methods_for_amount, _method_dict
from bot.services.manual_deposit_requests import capacity_allows
from bot.services.union_method_types import (
    CLUB_SLUG_TO_UNION_TYPE,
    UNION_METHOD_TYPES,
    UNION_TYPE_SLUGS,
    union_type_display_name,
    validate_union_method_type,
)
from db.connection import get_db
from db.models import ClubPaymentMethod, ClubPaymentMethodClub


def _union_type_slug_for_method(method: ClubPaymentMethod) -> Optional[str]:
    raw = getattr(method, "union_type", None)
    if raw:
        try:
            return validate_union_method_type(str(raw))
        except ValueError:
            pass
    from bot.services.union_method_types import union_type_from_display_name

    return union_type_from_display_name(method.name or "")


def _union_type_slug_for_method_dict(method: dict) -> Optional[str]:
    raw = method.get("union_type")
    if raw:
        try:
            return validate_union_method_type(str(raw))
        except ValueError:
            pass
    from bot.services.union_method_types import union_type_from_display_name

    return union_type_from_display_name(method.get("name") or "")


def list_union_methods_for_club(
    club_id: int,
    *,
    method_type: Optional[str] = None,
    active_only: bool = True,
) -> List[ClubPaymentMethod]:
    with get_db() as session:
        q = (
            session.query(ClubPaymentMethod)
            .join(
                ClubPaymentMethodClub,
                ClubPaymentMethodClub.method_id == ClubPaymentMethod.id,
            )
            .filter(
                ClubPaymentMethodClub.club_id == int(club_id),
                ClubPaymentMethod.direction == "deposit",
                ClubPaymentMethod.tracks_manual_requests.is_(True),
            )
            .options(joinedload(ClubPaymentMethod.method_clubs))
            .order_by(ClubPaymentMethod.sort_order, ClubPaymentMethod.id)
        )
        if active_only:
            q = q.filter(ClubPaymentMethod.is_active.is_(True))
        rows = q.all()
        for m in rows:
            # Detach with loaded columns so callers can read name/id after commit.
            session.expunge(m)
        if method_type is not None:
            type_slug = validate_union_method_type(method_type)
            rows = [m for m in rows if _union_type_slug_for_method(m) == type_slug]
        return rows


def pick_union_method(
    club_id: int,
    method_type: str,
    amount: Optional[Decimal],
) -> Optional[ClubPaymentMethod]:
    """First union pool in dashboard order with enough remaining capacity."""
    methods = list_union_methods_for_club(club_id, method_type=method_type, active_only=True)
    if not methods:
        return None
    with get_db() as session:
        for method in methods:
            if method.deposit_limit is None:
                continue
            if method.min_amount is not None and amount is not None:
                if amount < Decimal(str(method.min_amount)):
                    continue
            if method.max_amount is not None and amount is not None:
                if amount > Decimal(str(method.max_amount)):
                    continue
            if capacity_allows(
                session,
                method_id=int(method.id),
                amount=amount if amount is not None else Decimal("0"),
                deposit_limit=Decimal(str(method.deposit_limit)),
            ):
                return method
    return None


def get_club_deposit_method_by_slug(club_id: int, club_slug: str) -> Optional[dict]:
    """Active club deposit method (non-union) by slug."""
    with get_db() as session:
        m = (
            session.query(ClubPaymentMethod)
            .filter_by(
                club_id=int(club_id),
                direction="deposit",
                slug=club_slug.strip().lower(),
                is_active=True,
            )
            .filter(ClubPaymentMethod.tracks_manual_requests.is_(False))
            .one_or_none()
        )
        if not m:
            return None
        return _method_dict(m)


def build_deposit_picker_methods(
    club_id: int,
    amount: Optional[Decimal],
) -> List[dict]:
    """Return picker entries: synthetic union-type rows + other club methods."""
    raw = get_methods_for_amount(club_id, "deposit", amount)

    union_types_eligible: set[str] = set()
    club_by_type: dict[str, dict] = {}
    other: list[dict] = []
    for m in raw:
        slug = (m.get("slug") or "").strip().lower()
        if bool(m.get("tracks_manual_requests")):
            type_slug = _union_type_slug_for_method_dict(m)
            if type_slug:
                union_types_eligible.add(type_slug)
            continue
        type_slug = CLUB_SLUG_TO_UNION_TYPE.get(slug)
        if type_slug:
            club_by_type[type_slug] = m
        else:
            other.append(m)

    merged: list[dict] = []
    for type_slug in sorted(UNION_TYPE_SLUGS):
        if type_slug in union_types_eligible or type_slug in club_by_type:
            meta = UNION_METHOD_TYPES[type_slug]
            merged.append(
                {
                    "picker_kind": "union_type",
                    "type_slug": type_slug,
                    "name": meta["name"],
                    "slug": meta["club_slug"],
                    "id": None,
                    "tracks_manual_requests": False,
                }
            )

    return merged + other
