"""Pushover push notifications for ops alerts (cashout reminders, etc.)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

PUSHOVER_APP_TOKEN_ENV = "PUSHOVER_APP_TOKEN"
PUSHOVER_MESSAGES_URL = "https://api.pushover.net/1/messages.json"

_HTTP_TIMEOUT_SEC = 5.0
_MAX_MESSAGE_LEN = 1024
_MAX_TITLE_LEN = 250
_DEFAULT_TITLE = "URGENT cashout"
_DEFAULT_PRIORITY = 1
_DEFAULT_URL_TITLE = "Open cashout"


def _app_token() -> str | None:
    raw = (os.getenv(PUSHOVER_APP_TOKEN_ENV) or "").strip()
    return raw or None


def _build_payload(
    message: str,
    *,
    user: str,
    title: str | None,
    url: str | None,
    url_title: str | None,
    priority: int,
) -> dict[str, str | int] | None:
    body = (message or "").strip()
    if not body:
        return None
    if len(body) > _MAX_MESSAGE_LEN:
        body = body[: _MAX_MESSAGE_LEN - 1] + "…"

    user_key = (user or "").strip()
    if not user_key:
        return None

    token = _app_token()
    if not token:
        return None

    title_text = (title or _DEFAULT_TITLE).strip() or _DEFAULT_TITLE
    if len(title_text) > _MAX_TITLE_LEN:
        title_text = title_text[:_MAX_TITLE_LEN]

    payload: dict[str, str | int] = {
        "token": token,
        "user": user_key,
        "message": body,
        "title": title_text,
        "priority": int(priority),
    }
    url_clean = (url or "").strip()
    if url_clean:
        payload["url"] = url_clean[:512]
        payload["url_title"] = (
            (url_title or _DEFAULT_URL_TITLE).strip() or _DEFAULT_URL_TITLE
        )[:100]
    return payload


def _interpret_response(resp: httpx.Response, *, source: str) -> bool:
    if resp.status_code >= 500:
        logger.warning(
            "pushover: server error source=%s status=%s",
            source,
            resp.status_code,
        )
        return False
    if resp.status_code >= 400:
        logger.warning(
            "pushover: client error source=%s status=%s body=%s",
            source,
            resp.status_code,
            (resp.text or "")[:300],
        )
        return False
    data = resp.json()
    if data.get("status") != 1:
        logger.warning(
            "pushover: status!=1 source=%s errors=%s request=%s",
            source,
            data.get("errors"),
            data.get("request"),
        )
        return False
    logger.info(
        "pushover: ok source=%s request=%s",
        source,
        data.get("request"),
    )
    return True


async def notify_pushover(
    message: str,
    *,
    user: str,
    title: str | None = None,
    url: str | None = None,
    url_title: str | None = None,
    priority: int = _DEFAULT_PRIORITY,
    source: str = "pushover",
) -> bool:
    """POST one Pushover message to ``user``. Never raises. Returns True on status=1."""
    payload = _build_payload(
        message,
        user=user,
        title=title,
        url=url,
        url_title=url_title,
        priority=priority,
    )
    if payload is None:
        if not (message or "").strip():
            return False
        if not (user or "").strip():
            logger.warning("pushover: skipped source=%s (empty user key)", source)
        elif not _app_token():
            logger.warning(
                "pushover: skipped source=%s (set %s)",
                source,
                PUSHOVER_APP_TOKEN_ENV,
            )
        return False

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = await client.post(PUSHOVER_MESSAGES_URL, data=payload)
            return _interpret_response(resp, source=source)
    except Exception:
        logger.warning("pushover: request failed source=%s", source, exc_info=True)
        return False


def notify_pushover_sync(
    message: str,
    *,
    user: str,
    title: str | None = None,
    url: str | None = None,
    url_title: str | None = None,
    priority: int = _DEFAULT_PRIORITY,
    source: str = "pushover",
) -> bool:
    """Sync POST for create-path fan-out. Never raises."""
    payload = _build_payload(
        message,
        user=user,
        title=title,
        url=url,
        url_title=url_title,
        priority=priority,
    )
    if payload is None:
        if not (message or "").strip():
            return False
        if not (user or "").strip():
            logger.warning("pushover: skipped source=%s (empty user key)", source)
        elif not _app_token():
            logger.warning(
                "pushover: skipped source=%s (set %s)",
                source,
                PUSHOVER_APP_TOKEN_ENV,
            )
        return False

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = client.post(PUSHOVER_MESSAGES_URL, data=payload)
            return _interpret_response(resp, source=source)
    except Exception:
        logger.warning("pushover: request failed source=%s", source, exc_info=True)
        return False
