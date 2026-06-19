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
SLACK_FILES_UPLOAD_URL = "https://slack.com/api/files.upload"

_MAX_SLACK_TEXT_LEN = 3000
_HTTP_TIMEOUT_SEC = 5.0
_ISSUE_REPORT_TAG_MENTIONS_ENV = "ISSUE_REPORT_TAG_MENTIONS"


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


def _issue_report_tag_mentions() -> dict[str, str]:
    import json

    raw = (os.getenv(_ISSUE_REPORT_TAG_MENTIONS_ENV) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("slack_ops: invalid %s JSON", _ISSUE_REPORT_TAG_MENTIONS_ENV)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def _collect_tag_mentions(tags: list[str]) -> str:
    mentions_map = _issue_report_tag_mentions()
    if not mentions_map or not tags:
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for tag in tags:
        mention = mentions_map.get(tag)
        if mention and mention not in seen:
            seen.add(mention)
            parts.append(mention)
    return " ".join(parts)


async def _upload_slack_file(
    client: httpx.AsyncClient,
    *,
    token: str,
    channel: str,
    filename: str,
    content: bytes,
    content_type: str,
    thread_ts: str | None = None,
) -> str | None:
    headers = {"Authorization": f"Bearer {token}"}
    data: dict[str, str] = {"channels": channel, "filename": filename}
    if thread_ts:
        data["thread_ts"] = thread_ts
    files = {"file": (filename, content, content_type)}
    try:
        resp = await client.post(
            SLACK_FILES_UPLOAD_URL,
            headers=headers,
            data=data,
            files=files,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        logger.warning(
            "slack_ops: files.upload failed filename=%s",
            filename,
            exc_info=True,
        )
        return None
    if not payload.get("ok"):
        logger.warning(
            "slack_ops: files.upload not ok filename=%s error=%s",
            filename,
            payload.get("error"),
        )
        return None
    file_obj = payload.get("file") or {}
    file_id = file_obj.get("id")
    return str(file_id) if file_id else None


async def notify_slack_issue_report(
    text: str,
    *,
    tags: list[str] | None = None,
    file_bytes: list[tuple[str, bytes, str]] | None = None,
) -> tuple[bool, str | None, list[str | None]]:
    """Post issue report to Slack with optional screenshot uploads.

    Returns (ok, message_ts, slack_file_ids aligned with file_bytes).
    Never raises.
    """

    tag_mentions = _collect_tag_mentions(tags or [])
    mention = _slack_mention()
    prefix_parts = [p for p in (mention, tag_mentions) if p]
    prefix = " ".join(prefix_parts)

    from bot.services.slack_ops_format import beautify_slack_body, slack_header

    body = beautify_slack_body((text or "").strip(), source="issue_report")
    header = slack_header("issue_report")
    if prefix:
        header = f"{prefix} {header}"
    if body:
        message = f"{header}\n\n{body}"
    else:
        message = header
    if len(message) > _MAX_SLACK_TEXT_LEN:
        message = message[: _MAX_SLACK_TEXT_LEN - 1] + "…"

    files = file_bytes or []
    slack_file_ids: list[str | None] = [None] * len(files)

    token = _slack_bot_token()
    channel = _slack_channel_id()
    if token and channel:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                }
                payload = {
                    "channel": channel,
                    "text": message,
                    "unfurl_links": False,
                }
                resp = await client.post(
                    SLACK_CHAT_POST_MESSAGE_URL,
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(
                        "slack_ops: issue_report chat.postMessage failed error=%s",
                        data.get("error"),
                    )
                    if _slack_webhook_url():
                        note = message
                        if files:
                            note += (
                                f"\n\n_({len(files)} screenshot(s) stored in API only — "
                                "webhook fallback cannot attach files)_"
                            )
                        ok = await _post_via_webhook(note)
                        return ok, None, slack_file_ids
                    return False, None, slack_file_ids

                message_ts = data.get("ts")
                ts_str = str(message_ts) if message_ts else None
                logger.info(
                    "slack_ops: issue_report chat.postMessage ok channel=%s ts=%s",
                    channel,
                    ts_str,
                )

                for idx, (filename, content, content_type) in enumerate(files):
                    file_id = await _upload_slack_file(
                        client,
                        token=token,
                        channel=channel,
                        filename=filename,
                        content=content,
                        content_type=content_type,
                        thread_ts=ts_str,
                    )
                    slack_file_ids[idx] = file_id

                return True, ts_str, slack_file_ids
        except Exception:
            logger.warning(
                "slack_ops: issue_report bot API request failed",
                exc_info=True,
            )
            if _slack_webhook_url():
                note = message
                if files:
                    note += (
                        f"\n\n_({len(files)} screenshot(s) stored in API only — "
                        "webhook fallback cannot attach files)_"
                    )
                ok = await _post_via_webhook(note)
                return ok, None, slack_file_ids
            return False, None, slack_file_ids

    if _slack_webhook_url():
        note = message
        if files:
            note += (
                f"\n\n_({len(files)} screenshot(s) stored in API only — "
                "webhook fallback cannot attach files)_"
            )
        ok = await _post_via_webhook(note)
        return ok, None, slack_file_ids

    logger.warning(
        "slack_ops: skipped issue_report (set SLACK_OPS_BOT_TOKEN+SLACK_OPS_CHANNEL_ID or SLACK_OPS_WEBHOOK_URL)",
    )
    return False, None, slack_file_ids
