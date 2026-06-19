"""Post ops alerts to Slack (custom app chat.postMessage or Incoming Webhook)."""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Custom Slack app (e.g. Engineer noti service) — preferred when set.
SLACK_OPS_BOT_TOKEN_ENV = "SLACK_OPS_BOT_TOKEN"
SLACK_OPS_CHANNEL_ID_ENV = "SLACK_OPS_CHANNEL_ID"

# Legacy Incoming Webhook fallback.
SLACK_OPS_WEBHOOK_URL_ENV = "SLACK_OPS_WEBHOOK_URL"
SLACK_OPS_MENTION_ENV = "SLACK_OPS_MENTION"

SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

_MAX_SLACK_TEXT_LEN = 3000
_HTTP_TIMEOUT_SEC = 5.0


def _slack_bot_token() -> str | None:
    raw = (os.getenv(SLACK_OPS_BOT_TOKEN_ENV) or "").strip()
    return raw or None


def _slack_channel_id() -> str | None:
    raw = (os.getenv(SLACK_OPS_CHANNEL_ID_ENV) or "").strip()
    return raw or None


def _slack_webhook_url() -> str | None:
    raw = (os.getenv(SLACK_OPS_WEBHOOK_URL_ENV) or "").strip()
    return raw or None


def _slack_mention() -> str | None:
    raw = (os.getenv(SLACK_OPS_MENTION_ENV) or "").strip()
    return raw or None


def format_slack_ops_message(text: str, *, source: str) -> str:
    from bot.services.slack_ops_format import beautify_slack_body, slack_header

    body = beautify_slack_body((text or "").strip(), source=source)
    header = slack_header(source, mention=_slack_mention())
    if body:
        message = f"{header}\n\n{body}"
    else:
        message = header
    if len(message) > _MAX_SLACK_TEXT_LEN:
        return message[: _MAX_SLACK_TEXT_LEN - 1] + "…"
    return message


async def _post_via_bot_api(text: str) -> bool:
    token = _slack_bot_token()
    channel = _slack_channel_id()
    if not token or not channel:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {"channel": channel, "text": text, "unfurl_links": False}

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = await client.post(
                SLACK_CHAT_POST_MESSAGE_URL,
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            logger.warning(
                "slack_ops: chat.postMessage failed error=%s",
                data.get("error"),
            )
            return False
        logger.info(
            "slack_ops: chat.postMessage ok channel=%s ts=%s",
            channel,
            data.get("ts"),
        )
        return True
    except Exception:
        logger.warning("slack_ops: chat.postMessage request failed", exc_info=True)
        return False


async def _post_via_webhook(text: str) -> bool:
    url = _slack_webhook_url()
    if not url:
        return False

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            resp = await client.post(url, json={"text": text})
            resp.raise_for_status()
        logger.info("slack_ops: webhook posted status=%s", resp.status_code)
        return True
    except Exception:
        logger.warning("slack_ops: webhook post failed", exc_info=True)
        return False


async def notify_slack_ops(text: str, *, source: str) -> bool:
    """Post to Slack. Prefers custom-app bot token; falls back to webhook. Never raises."""

    message = format_slack_ops_message(text, source=source)

    if _slack_bot_token() and _slack_channel_id():
        ok = await _post_via_bot_api(message)
        if ok:
            return True
        if _slack_webhook_url():
            logger.info("slack_ops: bot API failed; trying webhook fallback")
            return await _post_via_webhook(message)
        return False

    if _slack_webhook_url():
        return await _post_via_webhook(message)

    logger.warning(
        "slack_ops: skipped source=%s (set SLACK_OPS_BOT_TOKEN+SLACK_OPS_CHANNEL_ID or SLACK_OPS_WEBHOOK_URL)",
        source,
    )
    return False
