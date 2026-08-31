"""Dashboard QR login for Telethon sessions (bypasses SMS/app login codes)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from telethon.errors import SessionPasswordNeededError

from bot.services.mtproto_group_create import get_mtproto_lock, make_client
from bot.services.mtproto_session_db import clear_disk_login_session, snapshot_disk_session_to_database
from club_gc_settings import ClubGcConfig

logger = logging.getLogger(__name__)

QrStatus = Literal["pending", "success", "needs_password", "error", "expired", "idle"]


@dataclass
class QrLoginJob:
    url: str
    expires_at: datetime
    status: QrStatus = "pending"
    detail: str | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)


_jobs: dict[str, QrLoginJob] = {}


async def cancel_qr_login(club_key: str) -> None:
    job = _jobs.pop(club_key, None)
    if job is None:
        return
    if job._task and not job._task.done():
        job._task.cancel()
        try:
            await job._task
        except asyncio.CancelledError:
            pass


def get_qr_login_status(club_key: str) -> QrLoginJob | None:
    return _jobs.get(club_key)


async def start_qr_login(cfg: ClubGcConfig) -> QrLoginJob:
    await cancel_qr_login(cfg.club_key)

    async with get_mtproto_lock(cfg.club_key):
        clear_disk_login_session(cfg)
        client = make_client(cfg, prefer_database=False)
        await client.connect()
        try:
            qr = await client.qr_login()
        except Exception:
            await client.disconnect()
            raise

        job = QrLoginJob(url=qr.url, expires_at=qr.expires)
        _jobs[cfg.club_key] = job

        async def _run() -> None:
            try:
                try:
                    await qr.wait()
                    if await snapshot_disk_session_to_database(cfg):
                        job.status = "success"
                        logger.info("MTProto QR login succeeded club=%s", cfg.club_key)
                    else:
                        job.status = "error"
                        job.detail = (
                            "Telegram accepted the QR login, but syncing the session to Postgres failed."
                        )
                except SessionPasswordNeededError:
                    job.status = "needs_password"
                    logger.info("MTProto QR login needs 2FA club=%s", cfg.club_key)
                except asyncio.TimeoutError:
                    job.status = "expired"
                    job.detail = "QR code expired before it was scanned."
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        "MTProto QR login failed club=%s: %s",
                        cfg.club_key,
                        type(e).__name__,
                    )
                    job.status = "error"
                    job.detail = str(e) or type(e).__name__
            except asyncio.CancelledError:
                job.status = "idle"
                raise
            finally:
                await client.disconnect()

        job._task = asyncio.create_task(_run())
        return job
