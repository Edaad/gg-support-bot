"""Restart Heroku dynos via Platform API (admin /refresh)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HEROKU_API_KEY_ENV = "HEROKU_API_KEY"
HEROKU_APP_NAME_ENV = "HEROKU_APP_NAME"
HEROKU_API_BASE = "https://api.heroku.com"
HEROKU_ACCEPT = "application/vnd.heroku+json; version=3"


def _heroku_api_key() -> str | None:
    return (os.getenv(HEROKU_API_KEY_ENV) or "").strip() or None


def get_heroku_app_name() -> str | None:
    return (os.getenv(HEROKU_APP_NAME_ENV) or "").strip() or None


def heroku_restart_configured() -> bool:
    return bool(_heroku_api_key() and get_heroku_app_name())


async def restart_all_dynos(*, triggered_by_user_id: int | None = None) -> str:
    """Restart all dynos for the configured Heroku app. Returns app name."""
    api_key = _heroku_api_key()
    app_name = get_heroku_app_name()
    if not api_key:
        raise RuntimeError(f"{HEROKU_API_KEY_ENV} is not set")
    if not app_name:
        raise RuntimeError(f"{HEROKU_APP_NAME_ENV} is not set")

    url = f"{HEROKU_API_BASE}/apps/{app_name}/dynos"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": HEROKU_ACCEPT,
    }

    logger.info(
        "heroku restart: requesting all dynos restart app=%s triggered_by=%s",
        app_name,
        triggered_by_user_id,
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(url, headers=headers)

    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:500]
        raise RuntimeError(
            f"Heroku API error {resp.status_code}: {detail or resp.reason_phrase}"
        )

    logger.info(
        "heroku restart: accepted app=%s status=%s triggered_by=%s",
        app_name,
        resp.status_code,
        triggered_by_user_id,
    )
    return app_name
