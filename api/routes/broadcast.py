"""Broadcast a message to all groups linked to a club via the Telegram Bot API.

Uses a background asyncio task so the HTTP request returns immediately.
The dashboard polls a status endpoint to track progress.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from telegram import Bot, InputMediaPhoto
from telegram.error import RetryAfter, TimedOut

from api.auth import get_current_admin
from db.connection import get_db_dependency, get_db
from db.models import Club, BroadcastJob

router = APIRouter(
    prefix="/api/clubs",
    tags=["broadcast"],
    dependencies=[Depends(get_current_admin)],
)

_SEND_INTERVAL = 0.05  # 50ms between groups — ~20 groups/sec
_MAX_RETRIES = 3
SEPARATOR = "\n---\n"


# ── Request / Response schemas ────────────────────────────────────────────────

class BroadcastRequest(BaseModel):
    response_type: str = "text"
    response_text: Optional[str] = None
    response_file_id: Optional[str] = None
    response_caption: Optional[str] = None


class BroadcastJobRead(BaseModel):
    id: int
    club_id: int
    status: str
    total_groups: int
    sent: int
    failed: int
    errors: List[str]
    created_at: Optional[str] = None
    finished_at: Optional[str] = None


def _job_to_read(job: BroadcastJob) -> BroadcastJobRead:
    return BroadcastJobRead(
        id=job.id,
        club_id=job.club_id,
        status=job.status,
        total_groups=job.total_groups,
        sent=job.sent,
        failed=job.failed,
        errors=json.loads(job.errors_json or "[]"),
        created_at=job.created_at.isoformat() if job.created_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
    )


# ── Telegram helpers ──────────────────────────────────────────────────────────

def _get_bot() -> Bot:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise HTTPException(500, "TELEGRAM_BOT_TOKEN not configured on server")
    return Bot(token=token)


async def _send_to_chat(bot: Bot, chat_id: int, data: dict) -> None:
    rtype = data.get("response_type", "text")
    if rtype == "photo" and data.get("response_file_id"):
        file_ids = [f.strip() for f in data["response_file_id"].split(",") if f.strip()]
        caption = data.get("response_caption") or None
        if len(file_ids) == 1:
            await bot.send_photo(chat_id=chat_id, photo=file_ids[0], caption=caption)
        else:
            media = [
                InputMediaPhoto(media=fid, caption=caption if i == 0 else None)
                for i, fid in enumerate(file_ids)
            ]
            await bot.send_media_group(chat_id=chat_id, media=media)

    text = data.get("response_text") or ""
    if text:
        parts = [p.strip() for p in text.split(SEPARATOR) if p.strip()]
        for part in parts:
            await bot.send_message(chat_id=chat_id, text=part)


async def _send_with_retry(bot: Bot, chat_id: int, data: dict) -> None:
    for attempt in range(_MAX_RETRIES):
        try:
            await _send_to_chat(bot, chat_id, data)
            return
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except TimedOut:
            await asyncio.sleep(2 ** attempt)
    await _send_to_chat(bot, chat_id, data)


# ── Background worker ─────────────────────────────────────────────────────────

async def _run_broadcast(job_id: int, chat_ids: List[int], message_data: dict) -> None:
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    errors: list[str] = []
    sent = 0
    cancelled = False

    for i, cid in enumerate(chat_ids):
        if i > 0:
            await asyncio.sleep(_SEND_INTERVAL)

        # Check for cancellation every 10 groups
        if i % 10 == 0:
            with get_db() as session:
                job = session.query(BroadcastJob).get(job_id)
                if job and job.status == "cancelled":
                    cancelled = True
                    break

        try:
            await _send_with_retry(bot, cid, message_data)
            sent += 1
        except Exception as exc:
            errors.append(f"chat {cid}: {exc}")

        # Flush progress to DB every 10 groups (or on last)
        if (i + 1) % 10 == 0 or i == len(chat_ids) - 1:
            with get_db() as session:
                job = session.query(BroadcastJob).get(job_id)
                if job:
                    job.sent = sent
                    job.failed = len(errors)
                    job.errors_json = json.dumps(errors[-50:])

    with get_db() as session:
        job = session.query(BroadcastJob).get(job_id)
        if job:
            job.sent = sent
            job.failed = len(errors)
            job.errors_json = json.dumps(errors[-50:])
            if not cancelled:
                job.status = "done"
            job.finished_at = datetime.now(timezone.utc)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/{club_id}/broadcast", response_model=BroadcastJobRead, status_code=202)
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

    # Block if there's already a running broadcast for this club
    running = (
        db.query(BroadcastJob)
        .filter_by(club_id=club_id, status="running")
        .first()
    )
    if running:
        raise HTTPException(409, "A broadcast is already running for this club")

    chat_ids = [g.chat_id for g in groups]

    job = BroadcastJob(
        club_id=club_id,
        status="running",
        total_groups=len(chat_ids),
        response_type=body.response_type,
        response_text=body.response_text,
        response_file_id=body.response_file_id,
        response_caption=body.response_caption,
    )
    db.add(job)
    db.flush()
    db.refresh(job)

    message_data = {
        "response_type": body.response_type,
        "response_text": body.response_text,
        "response_file_id": body.response_file_id,
        "response_caption": body.response_caption,
    }

    asyncio.create_task(_run_broadcast(job.id, chat_ids, message_data))

    return _job_to_read(job)


@router.get("/{club_id}/broadcast/{job_id}", response_model=BroadcastJobRead)
def get_broadcast_status(
    club_id: int, job_id: int, db: Session = Depends(get_db_dependency)
):
    job = db.query(BroadcastJob).filter_by(id=job_id, club_id=club_id).first()
    if not job:
        raise HTTPException(404, "Broadcast job not found")
    return _job_to_read(job)


@router.post("/{club_id}/broadcast/{job_id}/cancel", response_model=BroadcastJobRead)
def cancel_broadcast(
    club_id: int, job_id: int, db: Session = Depends(get_db_dependency)
):
    job = db.query(BroadcastJob).filter_by(id=job_id, club_id=club_id).first()
    if not job:
        raise HTTPException(404, "Broadcast job not found")
    if job.status != "running":
        raise HTTPException(400, "Broadcast is not running")
    job.status = "cancelled"
    db.flush()
    db.refresh(job)
    return _job_to_read(job)
