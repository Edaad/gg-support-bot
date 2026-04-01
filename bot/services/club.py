"""Shared database queries used by bot handlers."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, List, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import (
    Club,
    ClubLinkedAccount,
    PaymentMethod,
    PaymentMethodTier,
    PaymentSubOption,
    Group,
    CustomCommand,
    PlayerActivity,
    CooldownBypass,
)

EST = ZoneInfo("America/New_York")


def _club_id_for_telegram_user(session: Session, telegram_user_id: int) -> Optional[int]:
    """Resolve club_id for a primary or linked Telegram user. None if inactive or unknown."""
    club = session.query(Club).filter_by(telegram_user_id=telegram_user_id).first()
    if club:
        return club.id if club.is_active else None
    link = (
        session.query(ClubLinkedAccount)
        .filter_by(telegram_user_id=telegram_user_id)
        .first()
    )
    if not link:
        return None
    c = session.query(Club).get(link.club_id)
    return c.id if c and c.is_active else None


def get_club_by_telegram_id(telegram_user_id: int) -> Optional[Club]:
    with get_db() as session:
        club = session.query(Club).filter_by(
            telegram_user_id=telegram_user_id, is_active=True
        ).first()
        if club:
            session.expunge(club)
            return club
        link = (
            session.query(ClubLinkedAccount)
            .filter_by(telegram_user_id=telegram_user_id)
            .first()
        )
        if not link:
            return None
        club = session.query(Club).filter_by(id=link.club_id, is_active=True).first()
        if club:
            session.expunge(club)
        return club


def get_club_for_chat(chat_id: int) -> Optional[int]:
    """Return the club ID for a group chat, or None."""
    with get_db() as session:
        group = session.query(Group).filter_by(chat_id=chat_id).first()
        if group:
            return group.club_id
    return None


def get_club_by_id(club_id: int) -> Optional[Club]:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if club:
            session.expunge(club)
        return club


def set_group_club(chat_id: int, telegram_user_id: int) -> Optional[int]:
    """Link a group to the club owned by telegram_user_id (primary or linked). Returns club_id or None."""
    with get_db() as session:
        club_id = _club_id_for_telegram_user(session, telegram_user_id)
        if not club_id:
            return None
        existing = session.query(Group).filter_by(chat_id=chat_id).first()
        if existing:
            existing.club_id = club_id
        else:
            session.add(Group(chat_id=chat_id, club_id=club_id))
        return club_id


def get_methods_for_amount(
    club_id: int, direction: str, amount: Optional[Decimal] = None
) -> List[dict]:
    """Return active payment methods for a club, optionally filtered by amount.

    Each dict: {id, name, slug, min_amount, max_amount, has_sub_options,
                response_type, response_text, response_file_id, response_caption}
    """
    with get_db() as session:
        q = (
            session.query(PaymentMethod)
            .filter_by(club_id=club_id, direction=direction, is_active=True)
            .order_by(PaymentMethod.sort_order)
        )
        methods = q.all()
        result = []
        for m in methods:
            if amount is not None:
                if m.min_amount is not None and amount < m.min_amount:
                    continue
                if m.max_amount is not None and amount > m.max_amount:
                    continue
            result.append({
                "id": m.id,
                "name": m.name,
                "slug": m.slug,
                "min_amount": m.min_amount,
                "max_amount": m.max_amount,
                "has_sub_options": m.has_sub_options,
                "response_type": m.response_type,
                "response_text": m.response_text,
                "response_file_id": m.response_file_id,
                "response_caption": m.response_caption,
            })
        return result


def get_method_by_id(method_id: int) -> Optional[dict]:
    with get_db() as session:
        m = session.query(PaymentMethod).get(method_id)
        if not m:
            return None
        return {
            "id": m.id,
            "name": m.name,
            "slug": m.slug,
            "has_sub_options": m.has_sub_options,
            "response_type": m.response_type,
            "response_text": m.response_text,
            "response_file_id": m.response_file_id,
            "response_caption": m.response_caption,
        }


def get_sub_options(method_id: int) -> List[dict]:
    with get_db() as session:
        subs = (
            session.query(PaymentSubOption)
            .filter_by(method_id=method_id, is_active=True)
            .order_by(PaymentSubOption.sort_order)
            .all()
        )
        return [
            {
                "id": s.id,
                "name": s.name,
                "slug": s.slug,
                "response_type": s.response_type,
                "response_text": s.response_text,
                "response_file_id": s.response_file_id,
                "response_caption": s.response_caption,
            }
            for s in subs
        ]


def get_sub_option_by_id(sub_id: int) -> Optional[dict]:
    with get_db() as session:
        s = session.query(PaymentSubOption).get(sub_id)
        if not s:
            return None
        return {
            "id": s.id,
            "name": s.name,
            "slug": s.slug,
            "response_type": s.response_type,
            "response_text": s.response_text,
            "response_file_id": s.response_file_id,
            "response_caption": s.response_caption,
        }


def get_club_welcome(club_id: int) -> Optional[dict]:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club or not club.welcome_text and not club.welcome_file_id:
            return None
        return {
            "type": club.welcome_type or "text",
            "text": club.welcome_text,
            "file_id": club.welcome_file_id,
            "caption": club.welcome_caption,
        }


def get_club_list_content(club_id: int) -> Optional[dict]:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club or not club.list_text and not club.list_file_id:
            return None
        return {
            "type": club.list_type or "text",
            "text": club.list_text,
            "file_id": club.list_file_id,
            "caption": club.list_caption,
        }


def get_lowest_minimum(club_id: int, direction: str) -> Optional[Decimal]:
    """Return the smallest min_amount across all active methods, or None if none have a minimum."""
    with get_db() as session:
        methods = (
            session.query(PaymentMethod)
            .filter_by(club_id=club_id, direction=direction, is_active=True)
            .all()
        )
        mins = [m.min_amount for m in methods if m.min_amount is not None]
        return min(mins) if mins else None


def get_tier_for_amount(method_id: int, amount: Decimal) -> Optional[dict]:
    """Return the response tier matching the amount, or None to use the method default."""
    with get_db() as session:
        tiers = (
            session.query(PaymentMethodTier)
            .filter_by(method_id=method_id)
            .order_by(PaymentMethodTier.sort_order)
            .all()
        )
        for t in tiers:
            if t.min_amount is not None and amount < t.min_amount:
                continue
            if t.max_amount is not None and amount > t.max_amount:
                continue
            return {
                "response_type": t.response_type,
                "response_text": t.response_text,
                "response_file_id": t.response_file_id,
                "response_caption": t.response_caption,
            }
    return None


def get_custom_command(club_id: int, command_name: str) -> Optional[dict]:
    with get_db() as session:
        cmd = (
            session.query(CustomCommand)
            .filter_by(club_id=club_id, command_name=command_name.lower(), is_active=True)
            .first()
        )
        if not cmd:
            return None
        return {
            "response_type": cmd.response_type,
            "response_text": cmd.response_text,
            "response_file_id": cmd.response_file_id,
            "response_caption": cmd.response_caption,
            "customer_visible": bool(cmd.customer_visible),
        }


def get_club_id_for_telegram_user(telegram_user_id: int) -> Optional[int]:
    with get_db() as session:
        return _club_id_for_telegram_user(session, telegram_user_id)


def is_club_primary_owner(telegram_user_id: int) -> bool:
    """True if this user is the active primary owner (clubs.telegram_user_id) of some club."""
    with get_db() as session:
        club = session.query(Club).filter_by(telegram_user_id=telegram_user_id).first()
        return club is not None and bool(club.is_active)


def is_club_staff(telegram_user_id: int, club_id: int) -> bool:
    """True if primary owner or a linked backup account for this club."""
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club or not club.is_active:
            return False
        if club.telegram_user_id == telegram_user_id:
            return True
        return (
            session.query(ClubLinkedAccount)
            .filter_by(club_id=club_id, telegram_user_id=telegram_user_id)
            .first()
            is not None
        )


def get_club_allows_multi_cashout(club_id: int) -> bool:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club:
            return False
        return bool(club.allow_multi_cashout)


def get_club_simple_mode(club_id: int, direction: str) -> Optional[dict]:
    """If simple mode is on for the direction, return the response dict; otherwise None."""
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club:
            return None
        prefix = f"{direction}_simple"
        if not getattr(club, f"{prefix}_mode", False):
            return None
        return {
            "response_type": getattr(club, f"{prefix}_type", "text") or "text",
            "response_text": getattr(club, f"{prefix}_text", None),
            "response_file_id": getattr(club, f"{prefix}_file_id", None),
            "response_caption": getattr(club, f"{prefix}_caption", None),
        }


def get_club_allows_admin_commands(club_id: int) -> bool:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club:
            return True
        return bool(club.allow_admin_commands)


def is_group_linked(chat_id: int) -> bool:
    """Check if a group chat already has a club association in the DB."""
    with get_db() as session:
        return session.query(Group).filter_by(chat_id=chat_id).first() is not None


def try_link_group_by_admin(chat_id: int, admin_user_ids: list[int]) -> Optional[int]:
    """Try to link a group to a club by matching any of the provided admin user IDs.

    Checks each admin against known club owners (primary + linked accounts).
    Returns the club_id if linked successfully, else None.
    """
    with get_db() as session:
        for uid in admin_user_ids:
            club_id = _club_id_for_telegram_user(session, uid)
            if club_id:
                existing = session.query(Group).filter_by(chat_id=chat_id).first()
                if existing:
                    existing.club_id = club_id
                else:
                    session.add(Group(chat_id=chat_id, club_id=club_id))
                return club_id
    return None


# ── Cashout cooldown helpers ─────────────────────────────────────────────────


def get_cooldown_settings(club_id: int) -> Optional[dict]:
    with get_db() as session:
        club = session.query(Club).get(club_id)
        if not club:
            return None
        return {
            "cooldown_enabled": bool(club.cashout_cooldown_enabled),
            "cooldown_hours": club.cashout_cooldown_hours or 24,
            "hours_enabled": bool(club.cashout_hours_enabled),
            "hours_start": club.cashout_hours_start or "08:00",
            "hours_end": club.cashout_hours_end or "23:00",
        }


def record_activity(
    club_id: int, telegram_user_id: int, chat_id: int, activity_type: str
) -> None:
    with get_db() as session:
        session.add(
            PlayerActivity(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                activity_type=activity_type,
            )
        )


def cancel_last_cashout_activity(club_id: int, telegram_user_id: int) -> None:
    """Mark the most recent non-cancelled cashout as cancelled so the timer falls back."""
    with get_db() as session:
        activity = (
            session.query(PlayerActivity)
            .filter_by(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                activity_type="cashout",
                cancelled=False,
            )
            .order_by(PlayerActivity.created_at.desc())
            .first()
        )
        if activity:
            activity.cancelled = True


def get_last_activity(club_id: int, telegram_user_id: int) -> Optional[datetime]:
    """Return created_at (UTC) of the latest non-cancelled deposit or cashout, or None."""
    with get_db() as session:
        activity = (
            session.query(PlayerActivity)
            .filter_by(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                cancelled=False,
            )
            .order_by(PlayerActivity.created_at.desc())
            .first()
        )
        if activity and activity.created_at:
            ts = activity.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
    return None


def check_and_consume_bypass(club_id: int, telegram_user_id: int) -> Optional[str]:
    """Check for a bypass. Returns 'permanent', 'one_time' (and consumes it), or None."""
    with get_db() as session:
        perm = (
            session.query(CooldownBypass)
            .filter_by(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                bypass_type="permanent",
            )
            .first()
        )
        if perm:
            return "permanent"
        one = (
            session.query(CooldownBypass)
            .filter_by(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                bypass_type="one_time",
                used=False,
            )
            .order_by(CooldownBypass.created_at.desc())
            .first()
        )
        if one:
            one.used = True
            return "one_time"
    return None


def grant_bypass(club_id: int, telegram_user_id: int, bypass_type: str) -> None:
    with get_db() as session:
        session.add(
            CooldownBypass(
                club_id=club_id,
                telegram_user_id=telegram_user_id,
                bypass_type=bypass_type,
            )
        )


def _parse_time(t: str) -> tuple[int, int]:
    parts = t.split(":")
    return int(parts[0]), int(parts[1])


def _day_label(target_date, reference_date) -> str:
    """Return 'today', 'tomorrow', or a full date string."""
    if target_date == reference_date:
        return "today"
    if target_date == reference_date + timedelta(days=1):
        return "tomorrow"
    return f"on {target_date.strftime('%A, %B %-d')}"


def _is_within_hours(est_dt, hours_start: str, hours_end: str) -> bool:
    h_start_h, h_start_m = _parse_time(hours_start)
    h_end_h, h_end_m = _parse_time(hours_end)
    current = est_dt.hour * 60 + est_dt.minute
    return (h_start_h * 60 + h_start_m) <= current < (h_end_h * 60 + h_end_m)


def _next_open_time(est_dt, hours_start: str, hours_end: str):
    """Return the next business-hours open moment at or after est_dt."""
    h_start_h, h_start_m = _parse_time(hours_start)
    h_end_h, h_end_m = _parse_time(hours_end)
    current = est_dt.hour * 60 + est_dt.minute
    start = h_start_h * 60 + h_start_m
    end = h_end_h * 60 + h_end_m

    if current < start:
        return est_dt.replace(hour=h_start_h, minute=h_start_m, second=0, microsecond=0)
    if current >= end:
        return (est_dt + timedelta(days=1)).replace(
            hour=h_start_h, minute=h_start_m, second=0, microsecond=0
        )
    return est_dt


def check_cashout_eligibility(
    club_id: int, telegram_user_id: int
) -> tuple[bool, Optional[str]]:
    """Check cooldown. Returns (eligible, denial_message).

    If eligible is True, denial_message is None and the cashout may proceed.
    Business hours are only used to adjust the "reach back out" time in cooldown
    messages — they never block a cashout on their own.
    """
    settings = get_cooldown_settings(club_id)
    if not settings or not settings["cooldown_enabled"]:
        return True, None

    bypass = check_and_consume_bypass(club_id, telegram_user_id)
    if bypass:
        return True, None

    last = get_last_activity(club_id, telegram_user_id)
    if last is None:
        return True, None

    now_utc = datetime.now(timezone.utc)
    now_est = now_utc.astimezone(EST)

    cooldown_td = timedelta(hours=settings["cooldown_hours"])
    eligible_at_utc = last + cooldown_td

    if now_utc >= eligible_at_utc:
        return True, None

    # Still in cooldown
    eligible_at_est = eligible_at_utc.astimezone(EST)
    remaining = eligible_at_utc - now_utc
    hours_left = int(remaining.total_seconds() // 3600)
    mins_left = int((remaining.total_seconds() % 3600) // 60)

    if hours_left > 0 and mins_left > 0:
        wait_str = f"{hours_left} hour{'s' if hours_left != 1 else ''} and {mins_left} minute{'s' if mins_left != 1 else ''}"
    elif hours_left > 0:
        wait_str = f"{hours_left} hour{'s' if hours_left != 1 else ''}"
    else:
        wait_str = f"{mins_left} minute{'s' if mins_left != 1 else ''}"

    elig_day = _day_label(eligible_at_est.date(), now_est.date())
    elig_time = eligible_at_est.strftime("%-I:%M %p")

    # If business hours are configured and eligible time falls outside them,
    # tell them the actual eligible time AND when active hours start.
    if settings["hours_enabled"] and not _is_within_hours(
        eligible_at_est, settings["hours_start"], settings["hours_end"]
    ):
        open_at = _next_open_time(
            eligible_at_est, settings["hours_start"], settings["hours_end"]
        )
        open_day = _day_label(open_at.date(), now_est.date())
        open_time = open_at.strftime("%-I:%M %p")
        return False, (
            f"Sorry! You need to wait {wait_str} before requesting a cashout. "
            f"You can reach back out at {elig_time} EST {elig_day} to request your cashout, "
            f"but since our active cashout hours start at {open_time} EST, "
            f"you should request it then {open_day} to get instantly cashed out!"
        )

    return False, (
        f"Sorry! You need to wait {wait_str} before requesting a cashout. "
        f"You can reach back out at {elig_time} EST {elig_day} "
        f"and you will be cashed out instantly!"
    )
