"""Shared database queries used by bot handlers."""

from decimal import Decimal
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session

from db.connection import get_db
from db.models import Club, PaymentMethod, PaymentSubOption, Group, CustomCommand


def get_club_by_telegram_id(telegram_user_id: int) -> Optional[Club]:
    with get_db() as session:
        club = session.query(Club).filter_by(telegram_user_id=telegram_user_id, is_active=True).first()
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
    """Link a group to the club owned by telegram_user_id. Returns club_id or None."""
    with get_db() as session:
        club = session.query(Club).filter_by(telegram_user_id=telegram_user_id).first()
        if not club:
            return None
        existing = session.query(Group).filter_by(chat_id=chat_id).first()
        if existing:
            existing.club_id = club.id
        else:
            session.add(Group(chat_id=chat_id, club_id=club.id))
        return club.id


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


def get_custom_command(club_id: int, command_name: str) -> Optional[dict]:
    with get_db() as session:
        cmd = (
            session.query(CustomCommand)
            .filter_by(club_id=club_id, command_name=command_name, is_active=True)
            .first()
        )
        if not cmd:
            return None
        return {
            "response_type": cmd.response_type,
            "response_text": cmd.response_text,
            "response_file_id": cmd.response_file_id,
            "response_caption": cmd.response_caption,
        }


def get_club_id_for_telegram_user(telegram_user_id: int) -> Optional[int]:
    with get_db() as session:
        club = session.query(Club).filter_by(telegram_user_id=telegram_user_id).first()
        return club.id if club else None
