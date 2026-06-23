"""Heroku release-phase admin DMs before deploy restarts dynos."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session
from telegram import Bot

from config import ADMIN_USER_IDS

logger = logging.getLogger(__name__)

DEPLOY_NOTIFY_ENABLED_ENV = "DEPLOY_NOTIFY_ENABLED"
DEPLOY_NOTIFY_COOLDOWN_SECONDS_ENV = "DEPLOY_NOTIFY_COOLDOWN_SECONDS"
TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"

DEPLOY_NOTIFY_MESSAGE = (
    "Engineers are deploying an update to the support bot. "
    "Please expect brief disruptions."
)

_DEFAULT_COOLDOWN_SECONDS = 900


def is_deploy_notify_enabled() -> bool:
    raw = (os.getenv(DEPLOY_NOTIFY_ENABLED_ENV) or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def cooldown_seconds() -> int:
    raw = (os.getenv(DEPLOY_NOTIFY_COOLDOWN_SECONDS_ENV) or "").strip()
    if not raw:
        return _DEFAULT_COOLDOWN_SECONDS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "deploy_notify: invalid %s=%r; using default %s",
            DEPLOY_NOTIFY_COOLDOWN_SECONDS_ENV,
            raw,
            _DEFAULT_COOLDOWN_SECONDS,
        )
        return _DEFAULT_COOLDOWN_SECONDS
    return max(0, value)


def _last_notified_at(session: Session) -> datetime | None:
    row = session.execute(
        text("SELECT last_notified_at FROM deploy_notify_state WHERE id = 1")
    ).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def should_notify_deploy(session: Session) -> bool:
    """Return True when cooldown has expired or no prior notification exists."""

    if not is_deploy_notify_enabled():
        return False

    last = _last_notified_at(session)
    if last is None:
        return True

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=cooldown_seconds())
    return last <= cutoff


def record_deploy_notify(session: Session) -> None:
    session.execute(
        text(
            """
            INSERT INTO deploy_notify_state (id, last_notified_at)
            VALUES (1, NOW())
            ON CONFLICT (id) DO UPDATE
            SET last_notified_at = EXCLUDED.last_notified_at
            """
        )
    )


async def notify_all_admin_user_ids(text: str) -> int:
    """DM every ``ADMIN_USER_IDS`` account. Returns count of successful sends."""

    body = (text or "").strip()
    if not body:
        return 0

    token = (os.getenv(TELEGRAM_BOT_TOKEN_ENV) or "").strip()
    if not token:
        logger.warning("deploy_notify: %s is not set; skipping admin DMs", TELEGRAM_BOT_TOKEN_ENV)
        return 0

    bot = Bot(token=token)
    await bot.initialize()
    sent = 0
    try:
        for user_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(chat_id=int(user_id), text=body[:4096])
                sent += 1
            except Exception:
                logger.warning(
                    "deploy_notify: failed to DM admin user_id=%s",
                    user_id,
                    exc_info=True,
                )
    finally:
        await bot.shutdown()
    return sent
