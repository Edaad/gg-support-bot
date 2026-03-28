"""Broadcast a message to all groups linked to a club via the Telegram Bot API."""

import asyncio
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut

from api.auth import get_current_admin
from db.connection import get_db_dependency
from db.models import Club

# Stay safely under Telegram's 30 msg/sec global cap.
# At 20 msgs/sec we have headroom even if a send triggers multiple API calls
# (e.g. media group = several calls internally).
_SEND_INTERVAL = 0.05  # seconds between groups
_MAX_RETRIES = 3

router = APIRouter(
    prefix="/api/clubs",
    tags=["broadcast"],
    dependencies=[Depends(get_current_admin)],
)


class BroadcastRequest(BaseModel):
    response_type: str = "text"
    response_text: str | None = None
    response_file_id: str | None = None
    response_caption: str | None = None


class BroadcastResult(BaseModel):
    total_groups: int
    sent: int
    failed: int
    errors: List[str]


SEPARATOR = "\n---\n"


def _get_bot() -> Bot:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN not configured on server")
    return Bot(token=token)


async def _send_to_chat(bot: Bot, chat_id: int, data: BroadcastRequest) -> None:
    """Send a broadcast message (photo and/or multi-part text) to one chat."""
    if data.response_type == "photo" and data.response_file_id:
        file_ids = [f.strip() for f in data.response_file_id.split(",") if f.strip()]
        caption = data.response_caption or None
        if len(file_ids) == 1:
            await bot.send_photo(chat_id=chat_id, photo=file_ids[0], caption=caption)
        else:
            media = [
                InputMediaPhoto(media=fid, caption=caption if i == 0 else None)
                for i, fid in enumerate(file_ids)
            ]
            await bot.send_media_group(chat_id=chat_id, media=media)

    text = data.response_text or ""
    if text:
        parts = [p.strip() for p in text.split(SEPARATOR) if p.strip()]
        for part in parts:
            await bot.send_message(chat_id=chat_id, text=part)


async def _send_with_retry(bot: Bot, chat_id: int, data: BroadcastRequest) -> None:
    """Attempt _send_to_chat with automatic back-off on RetryAfter / TimedOut."""
    for attempt in range(_MAX_RETRIES):
        try:
            await _send_to_chat(bot, chat_id, data)
            return
        except RetryAfter as exc:
            # Telegram told us exactly how long to wait
            wait = exc.retry_after + 1
            await asyncio.sleep(wait)
        except TimedOut:
            # Brief network hiccup — back off exponentially
            await asyncio.sleep(2 ** attempt)
    # Final attempt — let the exception propagate so the caller records the error
    await _send_to_chat(bot, chat_id, data)


@router.post("/{club_id}/broadcast", response_model=BroadcastResult)
async def broadcast(
    club_id: int, body: BroadcastRequest, db: Session = Depends(get_db_dependency)
):
    club = db.query(Club).get(club_id)
    if not club:
        raise HTTPException(404, "Club not found")

    groups = club.groups
    if not groups:
        raise HTTPException(400, "This club has no linked groups to broadcast to")

    has_content = (
        (body.response_type == "photo" and body.response_file_id)
        or body.response_text
    )
    if not has_content:
        raise HTTPException(400, "Provide at least response_text or a photo file ID")

    bot = _get_bot()
    sent = 0
    errors: list[str] = []

    for i, g in enumerate(groups):
        # Throttle: pause between every send to stay under Telegram's rate limit
        if i > 0:
            await asyncio.sleep(_SEND_INTERVAL)
        try:
            await _send_with_retry(bot, g.chat_id, body)
            sent += 1
        except Exception as exc:
            errors.append(f"chat {g.chat_id}: {exc}")

    return BroadcastResult(
        total_groups=len(groups),
        sent=sent,
        failed=len(errors),
        errors=errors,
    )
