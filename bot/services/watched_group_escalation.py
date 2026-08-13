"""Listen-only escalation for env-allowlisted non-support Telegram groups.

Episodes reuse awaiting-agent timings (1m debounce / 10m episode). State is
durable in ``watched_group_escalation_state``; one head-admin Slack post per
episode.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.services.escalation_notification import (
    awaiting_agent_debounce_seconds,
    awaiting_agent_episode_seconds,
)
from bot.services.support_group_chats import (
    fetch_support_group_chat_by_telegram_chat_id,
)
from db.connection import get_db
from db.models import WatchedGroupEscalationState

logger = logging.getLogger(__name__)

WATCH_GROUP_ESCALATION_CHAT_IDS_ENV = "WATCH_GROUP_ESCALATION_CHAT_IDS"
SLACK_SOURCE = "watched_group"
HEADLINE = "Watched group activity."

# Union-chat automation accounts (Telegram users, not bots). Skip head-admin Slack.
WATCHED_GROUP_IGNORE_USERNAMES = frozenset({"rtaccountant", "widget_stick"})

_watched_app: Any | None = None


def register_watched_group_escalation_runtime(app: Any) -> None:
    global _watched_app
    _watched_app = app
    try:
        restore_watched_group_escalation_jobs(getattr(app, "job_queue", None))
    except Exception:
        logger.warning(
            "watched_group_escalation: restore jobs failed",
            exc_info=True,
        )


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    if _watched_app is not None:
        return getattr(_watched_app, "job_queue", None)
    return None


def watched_escalation_chat_ids() -> frozenset[int]:
    """Parse ``WATCH_GROUP_ESCALATION_CHAT_IDS`` (comma-separated ints)."""
    raw = (os.getenv(WATCH_GROUP_ESCALATION_CHAT_IDS_ENV) or "").strip()
    if not raw:
        return frozenset()
    out: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            logger.warning(
                "watched_group_escalation: invalid chat id in %s: %r",
                WATCH_GROUP_ESCALATION_CHAT_IDS_ENV,
                token,
            )
    return frozenset(out)


def is_env_allowlisted_chat(chat_id: int) -> bool:
    return int(chat_id) in watched_escalation_chat_ids()


def is_support_group_chat(chat_id: int) -> bool:
    try:
        return fetch_support_group_chat_by_telegram_chat_id(int(chat_id)) is not None
    except Exception:
        logger.warning(
            "watched_group_escalation: support GC lookup failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return False


def is_watched_escalation_chat(chat_id: int) -> bool:
    """True when allowlisted and not a support megagroup."""
    if not is_env_allowlisted_chat(chat_id):
        return False
    return not is_support_group_chat(chat_id)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sender_username(user: Any) -> str:
    return (getattr(user, "username", None) or "").strip().lstrip("@").lower()


def is_ignored_watched_sender(user: Any) -> bool:
    """True for union automation accounts that should not open/feed episodes."""
    return sender_username(user) in WATCHED_GROUP_IGNORE_USERNAMES


def format_sender_label(user: Any) -> str:
    name = (
        (getattr(user, "full_name", None) or "").strip()
        or (getattr(user, "first_name", None) or "").strip()
        or "Unknown"
    )
    username = (getattr(user, "username", None) or "").strip().lstrip("@")
    if username:
        return f"{name} (@{username})"
    return name


def _media_placeholder(message: Any) -> str | None:
    if message is None:
        return None
    if getattr(message, "photo", None):
        return "[photo]"
    if getattr(message, "video", None):
        return "[video]"
    if getattr(message, "video_note", None):
        return "[video_note]"
    if getattr(message, "voice", None):
        return "[voice]"
    if getattr(message, "audio", None):
        return "[audio]"
    if getattr(message, "document", None):
        return "[document]"
    if getattr(message, "animation", None):
        return "[animation]"
    if getattr(message, "sticker", None):
        return "[sticker]"
    return None


def extract_watched_message_text(message: Any) -> str | None:
    """Text, caption, and/or media placeholder. None if nothing to escalate."""
    if message is None:
        return None
    text = (getattr(message, "text", None) or "").strip()
    if text:
        return text
    caption = (getattr(message, "caption", None) or "").strip()
    tag = _media_placeholder(message)
    if tag and caption:
        return f"{tag} {caption}"
    if tag:
        return tag
    if caption:
        return caption
    return None


def format_watched_group_slack_text(
    *,
    title: str | None,
    burst: list[dict[str, str]],
) -> str:
    group = (title or "").strip() or "(untitled group)"
    lines = [HEADLINE, f"Group: {group}"]
    for i, item in enumerate(burst):
        if i > 0:
            lines.append("---")
        sender = (item.get("from") or "").strip() or "Unknown"
        body = (item.get("text") or "").strip()
        lines.append(f"From: {sender}")
        if body:
            lines.append(body)
    return "\n".join(lines)


def _debounce_job_name(chat_id: int | str) -> str:
    return f"watched_group_debounce_{int(chat_id)}"


def _episode_job_name(chat_id: int | str) -> str:
    return f"watched_group_episode_{int(chat_id)}"


def _cancel_jobs(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    include_episode: bool = True,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    names = [_debounce_job_name(chat_id)]
    if include_episode:
        names.append(_episode_job_name(chat_id))
    for name in names:
        try:
            jobs = jq.get_jobs_by_name(name)
        except Exception:
            continue
        try:
            job_list = list(jobs)
        except TypeError:
            continue
        for job in job_list:
            try:
                job.schedule_removal()
            except Exception:
                logger.debug(
                    "watched_group_escalation: cancel job failed name=%s",
                    name,
                    exc_info=True,
                )


def _get_or_create_row(session, chat_id: int) -> WatchedGroupEscalationState:
    row = session.get(WatchedGroupEscalationState, int(chat_id))
    if row is None:
        row = WatchedGroupEscalationState(telegram_chat_id=int(chat_id))
        session.add(row)
    return row


def _row_to_dict(row: WatchedGroupEscalationState) -> dict[str, Any]:
    burst = row.burst_json if isinstance(row.burst_json, list) else []
    return {
        "telegram_chat_id": int(row.telegram_chat_id),
        "title": row.title,
        "episode_started_at": _as_utc(row.episode_started_at),
        "last_message_at": _as_utc(row.last_message_at),
        "escalated_at": _as_utc(row.escalated_at),
        "burst": list(burst),
    }


def load_episode_state(chat_id: int) -> dict[str, Any] | None:
    with get_db() as session:
        row = session.get(WatchedGroupEscalationState, int(chat_id))
        if row is None or row.episode_started_at is None:
            return None
        return _row_to_dict(row)


def clear_episode(chat_id: int, *, job_queue: Any | None = None) -> None:
    cid = int(chat_id)
    _cancel_jobs(cid, job_queue=job_queue, include_episode=True)
    with get_db() as session:
        row = session.get(WatchedGroupEscalationState, cid)
        if row is None:
            return
        row.episode_started_at = None
        row.last_message_at = None
        row.escalated_at = None
        row.burst_json = []
        row.updated_at = _now()


def list_open_episodes() -> list[dict[str, Any]]:
    with get_db() as session:
        rows = (
            session.query(WatchedGroupEscalationState)
            .filter(WatchedGroupEscalationState.episode_started_at.isnot(None))
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def _schedule_debounce(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    when: float | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        logger.warning(
            "watched_group_escalation: no job_queue for debounce chat_id=%s",
            chat_id,
        )
        return
    name = _debounce_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass
    delay = float(awaiting_agent_debounce_seconds() if when is None else when)
    if delay <= 0:
        # Fire ASAP via zero-delay job (still goes through callback path).
        delay = 0.1
    jq.run_once(
        _watched_group_debounce_callback,
        when=delay,
        data={"chat_id": int(chat_id)},
        name=name,
        job_kwargs={"misfire_grace_time": 30},
    )


def _schedule_episode_end(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    when: float | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        logger.warning(
            "watched_group_escalation: no job_queue for episode chat_id=%s",
            chat_id,
        )
        return
    name = _episode_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass
    delay = float(awaiting_agent_episode_seconds() if when is None else when)
    if delay <= 0:
        delay = 0.1
    jq.run_once(
        _watched_group_episode_end_callback,
        when=delay,
        data={"chat_id": int(chat_id)},
        name=name,
        job_kwargs={"misfire_grace_time": 60},
    )


def on_watched_group_message(
    chat_id: int,
    *,
    title: str | None,
    sender_label: str,
    message_text: str,
    job_queue: Any | None = None,
    now: datetime | None = None,
) -> bool:
    """Open or feed episode. Returns False if ignored (already escalated)."""
    cid = int(chat_id)
    body = (message_text or "").strip()
    if not body:
        return False
    ts = _as_utc(now) or _now()
    entry = {
        "from": (sender_label or "").strip() or "Unknown",
        "text": body,
    }

    with get_db() as session:
        row = _get_or_create_row(session, cid)
        if title:
            row.title = title
        row.updated_at = ts

        if row.episode_started_at is None:
            row.episode_started_at = ts
            row.last_message_at = ts
            row.escalated_at = None
            row.burst_json = [entry]
            open_new = True
            already_escalated = False
        else:
            open_new = False
            already_escalated = row.escalated_at is not None
            if already_escalated:
                return False
            burst = list(row.burst_json) if isinstance(row.burst_json, list) else []
            if not burst or burst[-1] != entry:
                burst.append(entry)
            row.burst_json = burst
            row.last_message_at = ts

    jq = _resolve_job_queue(job_queue)
    if open_new:
        _cancel_jobs(cid, job_queue=jq, include_episode=True)
        _schedule_episode_end(cid, job_queue=jq)
        _schedule_debounce(cid, job_queue=jq)
        logger.info(
            "watched_group_escalation: episode started chat_id=%s debounce_s=%s episode_s=%s",
            cid,
            awaiting_agent_debounce_seconds(),
            awaiting_agent_episode_seconds(),
        )
    else:
        _schedule_debounce(cid, job_queue=jq)
    return True


async def _watched_group_debounce_callback(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or context.job.chat_id)
    state = load_episode_state(chat_id)
    if state is None:
        return
    if state.get("escalated_at") is not None:
        return

    last_at = state.get("last_message_at")
    if last_at is not None:
        quiet = (_now() - last_at).total_seconds()
        if quiet + 0.5 < float(awaiting_agent_debounce_seconds()):
            # Message arrived after this job was scheduled; a newer job should exist.
            return

    burst = state.get("burst") or []
    if not burst:
        return

    text = format_watched_group_slack_text(
        title=state.get("title"),
        burst=burst,
    )
    try:
        from bot.services.slack_ops_notify import notify_slack_head_admin_escalation

        await notify_slack_head_admin_escalation(text, source=SLACK_SOURCE)
    except Exception:
        logger.warning(
            "watched_group_escalation: slack failed chat_id=%s",
            chat_id,
            exc_info=True,
        )

    with get_db() as session:
        row = session.get(WatchedGroupEscalationState, chat_id)
        if row is None or row.episode_started_at is None:
            return
        if row.escalated_at is not None:
            return
        row.escalated_at = _now()
        row.updated_at = row.escalated_at
    logger.info("watched_group_escalation: escalated chat_id=%s", chat_id)


async def _watched_group_episode_end_callback(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or context.job.chat_id)
    clear_episode(chat_id, job_queue=getattr(context, "job_queue", None))
    logger.info("watched_group_escalation: episode ended chat_id=%s", chat_id)


def restore_watched_group_escalation_jobs(job_queue: Any | None = None) -> None:
    """Re-arm debounce / episode-end after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    now = _now()
    debounce_s = float(awaiting_agent_debounce_seconds())
    episode_s = float(awaiting_agent_episode_seconds())

    for state in list_open_episodes():
        chat_id = int(state["telegram_chat_id"])
        started = state.get("episode_started_at")
        if started is None:
            continue
        episode_remaining = episode_s - (now - started).total_seconds()
        if episode_remaining <= 0:
            clear_episode(chat_id, job_queue=jq)
            continue

        _schedule_episode_end(chat_id, job_queue=jq, when=episode_remaining)

        if state.get("escalated_at") is not None:
            continue

        last_at = state.get("last_message_at") or started
        debounce_remaining = debounce_s - (now - last_at).total_seconds()
        _schedule_debounce(chat_id, job_queue=jq, when=debounce_remaining)
        logger.info(
            "watched_group_escalation: restored chat_id=%s episode_remaining=%.1f "
            "debounce_remaining=%.1f",
            chat_id,
            episode_remaining,
            debounce_remaining,
        )


async def notify_admins_non_support_group_join(
    *,
    chat_id: int,
    title: str | None,
    bot: Any | None = None,
) -> int:
    """DM ADMIN_USER_IDS with chat_id so ops can fill the env allowlist."""
    from config import ADMIN_USER_IDS

    group = (title or "").strip() or "(untitled)"
    body = (
        f'Bot joined non-support group "{group}" chat_id={int(chat_id)}. '
        f"Add to {WATCH_GROUP_ESCALATION_CHAT_IDS_ENV} to enable listen-escalate."
    )
    logger.info(
        "watched_group_escalation: non-support join chat_id=%s title=%r",
        chat_id,
        title,
    )

    if bot is not None:
        sent = 0
        for user_id in ADMIN_USER_IDS:
            try:
                await bot.send_message(chat_id=int(user_id), text=body[:4096])
                sent += 1
            except Exception:
                logger.warning(
                    "watched_group_escalation: failed DM admin user_id=%s",
                    user_id,
                    exc_info=True,
                )
        return sent

    from bot.services.deploy_notify import notify_all_admin_user_ids

    return await notify_all_admin_user_ids(body)


async def watched_group_message_gate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handler group -1: escalate-only path for allowlisted non-support chats."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type not in ("group", "supergroup"):
        return
    if not is_watched_escalation_chat(chat.id):
        return

    # Stop all later handlers for this chat (commands, auto-link, activity, …).
    if user.is_bot or is_ignored_watched_sender(user):
        raise ApplicationHandlerStop

    text = extract_watched_message_text(message)
    if text:
        on_watched_group_message(
            chat.id,
            title=chat.title,
            sender_label=format_sender_label(user),
            message_text=text,
            job_queue=getattr(context, "job_queue", None),
        )
    raise ApplicationHandlerStop
