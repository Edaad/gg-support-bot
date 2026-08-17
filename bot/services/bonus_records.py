"""CRUD helpers for bonus_records and the bonus Zapier webhook."""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy.orm import joinedload

from bot.services.bonus_player_resolve import resolve_bonus_player
from cashier.services.zapier import build_zapier_name
from db.connection import get_db
from db.models import BonusRecord, BonusType, Club

logger = logging.getLogger(__name__)

ZAPIER_WEBHOOK_ENV = "ZAPIER_BONUS_WEBHOOK_URL"


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def build_bonus_zapier_payload(data: dict[str, Any]) -> dict[str, Any]:
    admin_id = data.get("admin_user_id")
    if admin_id is None:
        admin_id = ""
    return {
        "player_username": data.get("player_username") or "",
        "gg_player_id": data.get("gg_player_id") or "",
        "group_title": data.get("group_title") or "",
        "amount": str(data["amount"]),
        "bonus_type": data.get("bonus_type_name") or "",
        "description": data.get("custom_description") or "",
        "club": data.get("club_name") or "",
        "admin_telegram_user_id": admin_id,
    }


def fire_bonus_zapier_webhook(data: dict[str, Any]) -> None:
    url = os.getenv(ZAPIER_WEBHOOK_ENV)
    if not url:
        return
    payload = build_bonus_zapier_payload(data)
    try:
        with httpx.Client(timeout=10) as client:
            client.post(url, json=payload)
    except Exception:
        logger.exception("bonus zapier webhook failed")


def record_to_dict(record: BonusRecord) -> dict[str, Any]:
    display_name = record.player_username
    if record.group_title:
        display_name = build_zapier_name(record.group_title) or record.group_title
    return {
        "id": record.id,
        "player_username": display_name,
        "amount": record.amount,
        "bonus_type_id": record.bonus_type_id,
        "bonus_type_name": record.bonus_type.name if record.bonus_type else None,
        "custom_description": record.custom_description,
        "club_id": record.club_id,
        "club_name": record.club.name if record.club else None,
        "gg_player_id": record.gg_player_id,
        "group_title": record.group_title,
        "chat_id": int(record.chat_id) if record.chat_id is not None else None,
        "player_details_id": record.player_details_id,
        "admin_telegram_user_id": record.admin_telegram_user_id,
        "created_at": record.created_at,
        "player_resolved": bool(record.gg_player_id),
    }


def _player_fields(group_title: str, club_id: int) -> dict[str, Any]:
    ctx = resolve_bonus_player(group_title=group_title, club_id=club_id)
    if ctx is None:
        return {
            "player_username": group_title,
            "gg_player_id": None,
            "player_details_id": None,
            "chat_id": None,
        }
    return {
        "player_username": ctx.zapier_name,
        "gg_player_id": ctx.gg_player_id,
        "player_details_id": ctx.player_details_id,
        "chat_id": ctx.chat_id,
    }


def _validate_type(
    session,
    bonus_type_id: Optional[int],
    custom_description: Optional[str],
) -> tuple[Optional[int], Optional[str], str]:
    desc = (custom_description or "").strip() or None
    if bonus_type_id is None:
        if not desc:
            raise ValueError("Description is required for Other")
        return None, desc, "Other"
    bt = session.get(BonusType, int(bonus_type_id))
    if not bt:
        raise ValueError("Bonus type not found")
    if not bt.is_active:
        raise ValueError("Bonus type is not active")
    return int(bt.id), desc, bt.name


def _zapier_data_from_record(record: BonusRecord, *, type_name: str, club_name: str) -> dict[str, Any]:
    return {
        "player_username": record.player_username,
        "gg_player_id": record.gg_player_id or "",
        "group_title": record.group_title or "",
        "amount": record.amount,
        "bonus_type_name": type_name,
        "custom_description": record.custom_description or "",
        "club_name": club_name,
        "admin_user_id": "",
    }


def list_bonus_records(*, limit: int = 200) -> list[dict[str, Any]]:
    with get_db() as session:
        rows = (
            session.query(BonusRecord)
            .options(joinedload(BonusRecord.bonus_type), joinedload(BonusRecord.club))
            .order_by(BonusRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [record_to_dict(r) for r in rows]


def create_bonus_record(
    *,
    club_id: int,
    group_title: str,
    amount: Decimal,
    bonus_type_id: Optional[int] = None,
    custom_description: Optional[str] = None,
) -> dict[str, Any]:
    title = (group_title or "").strip()
    if not title:
        raise ValueError("Name is required")
    amt = _as_decimal(amount)
    if amt <= 0:
        raise ValueError("Amount must be greater than zero")

    with get_db() as session:
        club = session.get(Club, int(club_id))
        if not club:
            raise ValueError("Club not found")
        type_id, desc, type_name = _validate_type(session, bonus_type_id, custom_description)
        player = _player_fields(title, int(club_id))
        rec = BonusRecord(
            player_username=player["player_username"],
            amount=amt,
            bonus_type_id=type_id,
            custom_description=desc,
            club_id=int(club_id),
            player_details_id=player["player_details_id"],
            gg_player_id=player["gg_player_id"],
            chat_id=player["chat_id"],
            group_title=title,
            admin_telegram_user_id=None,
        )
        session.add(rec)
        session.flush()
        session.refresh(rec)
        data = record_to_dict(rec)
        zapier_data = _zapier_data_from_record(rec, type_name=type_name, club_name=club.name)

    fire_bonus_zapier_webhook(zapier_data)
    return data


def update_bonus_record(record_id: int, **updates: Any) -> Optional[dict[str, Any]]:
    with get_db() as session:
        record = session.get(BonusRecord, int(record_id))
        if not record:
            return None
        if "club_id" in updates:
            if updates["club_id"] is None:
                raise ValueError("Club is required")
            club = session.get(Club, int(updates["club_id"]))
            if not club:
                raise ValueError("Club not found")
            record.club_id = int(updates["club_id"])
        if "group_title" in updates:
            title = (updates["group_title"] or "").strip()
            if not title:
                raise ValueError("Name is required")
            record.group_title = title
        if "amount" in updates:
            amt = _as_decimal(updates["amount"])
            if amt <= 0:
                raise ValueError("Amount must be greater than zero")
            record.amount = amt
        if "bonus_type_id" in updates or "custom_description" in updates:
            type_id = (
                updates["bonus_type_id"]
                if "bonus_type_id" in updates
                else record.bonus_type_id
            )
            desc = (
                updates["custom_description"]
                if "custom_description" in updates
                else record.custom_description
            )
            new_id, new_desc, _type_name = _validate_type(session, type_id, desc)
            record.bonus_type_id = new_id
            record.custom_description = new_desc
        if record.club_id is None:
            raise ValueError("Club is required")
        title = (record.group_title or "").strip() or record.player_username
        player = _player_fields(title, int(record.club_id))
        record.player_username = player["player_username"]
        record.gg_player_id = player["gg_player_id"]
        record.player_details_id = player["player_details_id"]
        record.chat_id = player["chat_id"]
        session.flush()
        session.refresh(record)
        return record_to_dict(record)


def delete_bonus_record(record_id: int) -> bool:
    with get_db() as session:
        record = session.get(BonusRecord, int(record_id))
        if not record:
            return False
        session.delete(record)
        return True
