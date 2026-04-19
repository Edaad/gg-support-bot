"""Send weekly-stat messages to a player's Telegram group using player_details."""

from __future__ import annotations

import asyncio
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from telegram import Bot

from api.auth import get_current_admin
from db.connection import get_db_dependency
from db.models import Club, PlayerDetails

# Mirror dashboard/src/config/clubMap.ts — slug -> canonical clubs.name
CLUB_SLUG_TO_NAME = {
    "clubgto": "ClubGTO",
    "round-table": "Round Table",
    "aces-table": "Round Table",
    "creator-club": "Creator Club",
}

router = APIRouter(
    prefix="/api/weekly-stats",
    tags=["weekly-stats"],
    dependencies=[Depends(get_current_admin)],
)


def _resolve_club_id(db: Session, club_slug: str) -> int:
    key = club_slug.strip().lower()
    full_name = CLUB_SLUG_TO_NAME.get(key)
    if not full_name:
        raise HTTPException(400, f"Unknown club slug: {club_slug!r}")
    club = (
        db.query(Club)
        .filter(text("lower(name) = lower(:n)"))
        .params(n=full_name)
        .first()
    )
    if not club:
        raise HTTPException(
            404,
            f"No club named {full_name!r} in database — check clubs.name matches mapping.",
        )
    return int(club.id)


class PlayerChatsResponse(BaseModel):
    chat_ids: List[int]


@router.get("/player-chats", response_model=PlayerChatsResponse)
def get_player_chats(
    club_slug: str = Query(..., alias="club_slug"),
    gg_player_id: str = Query(..., min_length=1),
    db: Session = Depends(get_db_dependency),
):
    """Telegram group chat ids bound to this player for the club (from player_details)."""
    club_id = _resolve_club_id(db, club_slug)
    row = (
        db.query(PlayerDetails)
        .filter_by(club_id=club_id, gg_player_id=gg_player_id.strip())
        .first()
    )
    if not row:
        raise HTTPException(404, "No player_details row for this club and GG player id")
    ids = [int(x) for x in (row.chat_ids or [])]
    return PlayerChatsResponse(chat_ids=ids)


class WeeklyPlayerMessageBody(BaseModel):
    club_slug: str = Field(..., min_length=1)
    gg_player_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=4096)
    chat_id: int = Field(..., description="Telegram group chat id (must be in player_details.chat_ids)")


class WeeklyPlayerMessageResponse(BaseModel):
    ok: bool = True


async def _send_telegram_text(chat_id: int, message: str) -> None:
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN not configured on server")
    bot = Bot(token=tok)
    await bot.send_message(chat_id=chat_id, text=message)


@router.post("/message", response_model=WeeklyPlayerMessageResponse)
def send_weekly_player_message(
    body: WeeklyPlayerMessageBody,
    db: Session = Depends(get_db_dependency),
):
    club_id = _resolve_club_id(db, body.club_slug)
    row = (
        db.query(PlayerDetails)
        .filter_by(club_id=club_id, gg_player_id=body.gg_player_id.strip())
        .first()
    )
    if not row:
        raise HTTPException(404, "No player_details row for this club and GG player id")
    allowed = {int(x) for x in (row.chat_ids or [])}
    cid = int(body.chat_id)
    if cid not in allowed:
        raise HTTPException(
            400,
            f"chat_id {cid} is not in player_details.chat_ids for this player",
        )
    try:
        asyncio.run(_send_telegram_text(cid, body.message))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Telegram send failed: {exc}") from exc
    return WeeklyPlayerMessageResponse(ok=True)
