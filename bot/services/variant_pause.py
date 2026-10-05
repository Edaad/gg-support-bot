"""48-hour pause for a deposit variant.

A pause sets is_active false and paused_until. When the time passes, the
variant is active again. Saving is_active from the admin editor clears the
timer, so an inactive save stays off.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.gto_club import GTO_CLUB_NAME
from db.models import Club, ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant

PAUSE_DURATION = timedelta(hours=48)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def pause_deadline(variant, now: datetime | None = None) -> datetime | None:
    """Return paused_until when it is still in the future."""
    raw = (
        variant.get("paused_until")
        if isinstance(variant, dict)
        else getattr(variant, "paused_until", None)
    )
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = datetime.fromisoformat(raw)
    if not isinstance(raw, datetime):
        return None
    deadline = _as_utc(raw)
    if deadline <= _now(now):
        return None
    return deadline


def release_expired_variant_pauses(session: Session) -> None:
    """Turn variants back on once paused_until has passed."""
    now = datetime.now(timezone.utc)
    rows = (
        session.query(ClubPaymentTierVariant)
        .filter(
            ClubPaymentTierVariant.paused_until.isnot(None),
            ClubPaymentTierVariant.paused_until <= now,
        )
        .all()
    )
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        row.is_active = True
        row.paused_until = None
        changed = True
    if changed:
        session.flush()


def card_state(variant, now: datetime | None = None) -> str:
    if pause_deadline(variant, now) is not None:
        return "paused"
    flag = (
        variant.get("is_active", True)
        if isinstance(variant, dict)
        else getattr(variant, "is_active", True)
    )
    if flag is False:
        return "disabled"
    return "active"


def apply_pause(variant, now: datetime | None = None) -> None:
    """Pause an active variant for 48 hours. Rejects one that is already off."""
    from bot.services.club_payment_v2 import variant_is_active

    if not variant_is_active(variant):
        raise ValueError("Variant is not active")
    moment = _now(now)
    variant.is_active = False
    variant.paused_until = moment + PAUSE_DURATION


def apply_resume(variant, now: datetime | None = None) -> None:
    """End a running pause. Rejects a variant that is not in one."""
    if pause_deadline(variant, now) is None:
        raise ValueError("Variant is not paused")
    variant.is_active = True
    variant.paused_until = None


def clear_pause_if_active_flag_saved(data: dict) -> None:
    """An admin save that sets is_active drops the timer."""
    if "is_active" in data:
        data["paused_until"] = None


def gto_zelle_rows(
    session: Session,
) -> list[tuple[ClubPaymentTierVariant, ClubPaymentTier]]:
    release_expired_variant_pauses(session)
    club = session.query(Club).filter(Club.name == GTO_CLUB_NAME).first()
    if club is None:
        return []
    return (
        session.query(ClubPaymentTierVariant, ClubPaymentTier)
        .join(
            ClubPaymentMethod,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .join(ClubPaymentTier, ClubPaymentTierVariant.tier_id == ClubPaymentTier.id)
        .filter(
            ClubPaymentMethod.club_id == int(club.id),
            ClubPaymentMethod.direction == "deposit",
            func.lower(ClubPaymentMethod.slug) == "zelle",
            ClubPaymentMethod.tracks_manual_requests.is_(False),
        )
        .order_by(ClubPaymentTierVariant.sort_order, ClubPaymentTierVariant.id)
        .all()
    )


def build_gto_zelle_cards(
    rows: list[tuple[ClubPaymentTierVariant, ClubPaymentTier]],
    now: datetime | None = None,
) -> list[dict]:
    moment = _now(now)
    counts: dict[str, int] = {}
    for variant, _tier in rows:
        counts[variant.label] = counts.get(variant.label, 0) + 1
    cards: list[dict] = []
    for variant, tier in rows:
        state = card_state(variant, moment)
        deadline = pause_deadline(variant, moment)
        cards.append(
            {
                "id": int(variant.id),
                "label": variant.label,
                "tier_label": tier.label if counts[variant.label] > 1 else None,
                "state": state,
                "paused_until": deadline,
            }
        )
    return cards


def list_gto_zelle_cards(session: Session) -> list[dict]:
    return build_gto_zelle_cards(gto_zelle_rows(session))


def _gto_zelle_variant(
    session: Session, variant_id: int
) -> Optional[ClubPaymentTierVariant]:
    for variant, _tier in gto_zelle_rows(session):
        if int(variant.id) == int(variant_id):
            return variant
    return None


def pause_gto_zelle_variant(
    session: Session, variant_id: int, now: datetime | None = None
) -> dict:
    variant = _gto_zelle_variant(session, variant_id)
    if variant is None:
        raise LookupError("Variant not found")
    apply_pause(variant, now)
    session.flush()
    cards = build_gto_zelle_cards(gto_zelle_rows(session), now)
    return next(card for card in cards if card["id"] == int(variant_id))


def resume_gto_zelle_variant(
    session: Session, variant_id: int, now: datetime | None = None
) -> dict:
    variant = _gto_zelle_variant(session, variant_id)
    if variant is None:
        raise LookupError("Variant not found")
    apply_resume(variant, now)
    session.flush()
    cards = build_gto_zelle_cards(gto_zelle_rows(session), now)
    return next(card for card in cards if card["id"] == int(variant_id))
