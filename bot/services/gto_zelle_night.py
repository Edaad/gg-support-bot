"""Disable one ClubGTO Zelle tag from midnight until 2:00am Eastern.

The tag is made inactive, the same as a variant that was turned off. Account
managers have no re-enable on that state. A head admin can turn it back on
from the club variant editor; that override lasts through 2:00am. A variant
that was already off, or sitting in a 48-hour pause, is left alone at 2:00am.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth import ROLE_ADMIN
from api.gto_club import GTO_CLUB_NAME
from bot.services.destination_fields import stored_zelle_recipient
from db.models import Club, ClubPaymentMethod, ClubPaymentTierVariant

logger = logging.getLogger(__name__)

NIGHT_TAG = "starship5vllc@gmail.com"
_NY = ZoneInfo("America/New_York")


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local(now: datetime | None) -> datetime:
    return _now(now).astimezone(_NY)


def in_gto_zelle_night_window(now: datetime | None = None) -> bool:
    """Midnight inclusive through 2:00am exclusive, America/New_York."""
    return _local(now).hour < 2


def _window_end(now: datetime | None) -> datetime:
    local = _local(now)
    end = local.replace(hour=2, minute=0, second=0, microsecond=0)
    return end.astimezone(timezone.utc)


def _same_moment(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return abs((_as_utc(left) - _as_utc(right)).total_seconds()) < 1


def is_night_tag(variant) -> bool:
    recipient = stored_zelle_recipient(variant)
    return recipient == NIGHT_TAG


def night_window_suppressed(variant, now: datetime | None = None) -> bool:
    """True when this tag must stay out of rotation for the current night."""
    if not is_night_tag(variant):
        return False
    if not in_gto_zelle_night_window(now):
        return False
    released = getattr(variant, "night_release_on", None)
    return released != _local(now).date()


def _night_variants(session: Session) -> list[ClubPaymentTierVariant]:
    club = session.query(Club).filter(Club.name == GTO_CLUB_NAME).first()
    club_id = getattr(club, "id", None)
    if not isinstance(club_id, int):
        return []
    rows = (
        session.query(ClubPaymentTierVariant)
        .join(
            ClubPaymentMethod,
            ClubPaymentTierVariant.method_id == ClubPaymentMethod.id,
        )
        .filter(
            ClubPaymentMethod.club_id == int(club_id),
            ClubPaymentMethod.direction == "deposit",
            func.lower(ClubPaymentMethod.slug) == "zelle",
            ClubPaymentMethod.tracks_manual_requests.is_(False),
        )
        .all()
    )
    if not isinstance(rows, list):
        return []
    return [row for row in rows if is_night_tag(row)]


def sync_gto_zelle_night_window(session: Session, now: datetime | None = None) -> None:
    """Turn the tag off until 2:00am, and forget a head-admin override after that."""
    from bot.services.variant_pause import pause_deadline

    moment = _now(now)
    variants = _night_variants(session)
    if not variants:
        return
    local_date = _local(moment).date()
    in_window = in_gto_zelle_night_window(moment)
    window_end = _window_end(moment)
    changed = False
    for variant in variants:
        released = getattr(variant, "night_release_on", None)
        if not in_window:
            if released is not None:
                variant.night_release_on = None
                changed = True
            continue
        if released == local_date:
            continue
        deadline = pause_deadline(variant, moment)
        if deadline is not None and not _same_moment(deadline, window_end):
            continue
        if variant.is_active is False and deadline is None:
            continue
        if variant.is_active or not _same_moment(
            getattr(variant, "paused_until", None), window_end
        ):
            variant.is_active = False
            variant.paused_until = window_end
            changed = True
            logger.info(
                "gto zelle night window off variant_id=%s until=%s",
                variant.id,
                window_end.isoformat(),
            )
    if changed:
        session.flush()


def guard_night_window_activation(
    variant,
    data: dict,
    role: str,
    now: datetime | None = None,
) -> None:
    """Head admin may turn the tag on during the window. Anyone else may not."""
    if not is_night_tag(variant):
        return
    if data.get("is_active") is False:
        # A save while the window already has the tag off must not wipe the
        # 2:00am return. Unchecking it after a head admin turned it on does.
        if in_gto_zelle_night_window(now) and _same_moment(
            getattr(variant, "paused_until", None), _window_end(now)
        ):
            data.pop("paused_until", None)
            return
        variant.night_release_on = None
        return
    if data.get("is_active") is not True or not in_gto_zelle_night_window(now):
        return
    if role != ROLE_ADMIN:
        raise ValueError("Only a head admin can turn this Zelle on right now.")
    variant.night_release_on = _local(now).date()


async def gto_zelle_night_window_job(_context) -> None:
    from bot.services.variant_pause import release_expired_variant_pauses
    from db.connection import get_db

    try:
        with get_db() as session:
            release_expired_variant_pauses(session)
    except Exception:
        logger.exception("gto zelle night window sync failed")


def schedule_gto_zelle_night_window_job(app) -> None:
    if app.job_queue is None:
        logger.warning("gto zelle night window: job_queue unavailable")
        return
    app.job_queue.run_repeating(
        gto_zelle_night_window_job,
        interval=timedelta(minutes=1),
        first=timedelta(seconds=15),
        name="gto_zelle_night_window",
    )
    logger.info("gto zelle night window job scheduled")
