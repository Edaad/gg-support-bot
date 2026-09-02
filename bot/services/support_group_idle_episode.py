"""Durable idle episodes for support groups.

Open on player free text (or deposit Slack feed): immediate Slack (unless already
sent), no-op menu hook, then 1m quiet burst for follow-ups. Episode ends on
5 minutes of any-human silence, 30m hard cap from open, or flow-end close.

Observe-only relative to /deposit and /cashout wizards.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from telegram.ext import ContextTypes

from bot.services.escalation_notification import (
    REASON_PLAYER_IDLE,
    REASON_PLAYER_IDLE_FOLLOWUP,
    awaiting_agent_debounce_seconds,
    notify_escalation_slack,
    offer_idle_help_prompt,
)
from bot.services.escalation_observability import (
    CLOSE_REASON_FLOW_END,
    CLOSE_REASON_HARD_CAP,
    CLOSE_REASON_SILENCE,
    close_history_episode,
)
from bot.services.group_activity import ESCALATION_SILENCE_SECONDS
from db.connection import get_db
from db.models import EscalationEpisode, SupportGroupIdleEpisodeState

logger = logging.getLogger(__name__)

EXPECTED_FLOW_INPUT_KEY = "expected_flow_input"

IDLE_EPISODE_HARD_CAP_SECONDS = 1800  # 30 minutes
IDLE_EPISODE_HARD_CAP_SECONDS_TEST = 120

_idle_app: Any | None = None


@dataclass(frozen=True)
class ReachOutResult:
    outcome: Literal["opened", "fed", "ignored"]
    episode_id: UUID | None = None
    escalation_event_id: int | None = None


def register_support_group_idle_episode_runtime(app: Any) -> None:
    global _idle_app
    _idle_app = app
    try:
        restore_support_group_idle_episode_jobs(getattr(app, "job_queue", None))
    except Exception:
        logger.warning(
            "support_group_idle_episode: restore jobs failed",
            exc_info=True,
        )


def _resolve_job_queue(job_queue: Any | None = None) -> Any | None:
    if job_queue is not None:
        return job_queue
    if _idle_app is not None:
        return getattr(_idle_app, "job_queue", None)
    return None


def idle_episode_hard_cap_seconds() -> int:
    from bot.runtime_config import is_test_bot_worker

    if is_test_bot_worker():
        return IDLE_EPISODE_HARD_CAP_SECONDS_TEST
    return IDLE_EPISODE_HARD_CAP_SECONDS


def idle_episode_silence_seconds() -> int:
    return int(ESCALATION_SILENCE_SECONDS)


def mark_expected_flow_input(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard accepted this update; group_activity should skip escalate/episode."""
    chat_data = getattr(context, "chat_data", None)
    if chat_data is None:
        return
    chat_data[EXPECTED_FLOW_INPUT_KEY] = True


def consume_expected_flow_input(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True and clear when the current update was marked expected."""
    chat_data = getattr(context, "chat_data", None)
    if chat_data is None:
        return False
    if not chat_data.pop(EXPECTED_FLOW_INPUT_KEY, False):
        return False
    return True


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _debounce_job_name(chat_id: int | str) -> str:
    return f"sg_idle_debounce_{int(chat_id)}"


def _silence_job_name(chat_id: int | str) -> str:
    return f"sg_idle_silence_{int(chat_id)}"


def _hardcap_job_name(chat_id: int | str) -> str:
    return f"sg_idle_hardcap_{int(chat_id)}"


def _cancel_jobs(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    include_hardcap: bool = True,
    include_silence: bool = True,
    include_debounce: bool = True,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    names: list[str] = []
    if include_debounce:
        names.append(_debounce_job_name(chat_id))
    if include_silence:
        names.append(_silence_job_name(chat_id))
    if include_hardcap:
        names.append(_hardcap_job_name(chat_id))
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
                    "support_group_idle_episode: cancel job failed name=%s",
                    name,
                    exc_info=True,
                )


def _get_or_create_row(session, chat_id: int) -> SupportGroupIdleEpisodeState:
    row = session.get(SupportGroupIdleEpisodeState, int(chat_id))
    if row is None:
        row = SupportGroupIdleEpisodeState(telegram_chat_id=int(chat_id))
        session.add(row)
    return row


def _row_to_dict(row: SupportGroupIdleEpisodeState) -> dict[str, Any]:
    burst = row.burst_json if isinstance(row.burst_json, list) else []
    return {
        "telegram_chat_id": int(row.telegram_chat_id),
        "title": row.title,
        "episode_started_at": _as_utc(row.episode_started_at),
        "last_human_at": _as_utc(row.last_human_at),
        "burst": list(burst),
        "history_episode_id": getattr(row, "history_episode_id", None),
    }


def load_episode_state(chat_id: int) -> dict[str, Any] | None:
    with get_db() as session:
        row = session.get(SupportGroupIdleEpisodeState, int(chat_id))
        if row is None or row.episode_started_at is None:
            return None
        return _row_to_dict(row)


def episode_is_open(chat_id: int) -> bool:
    return load_episode_state(int(chat_id)) is not None


def list_open_episodes() -> list[dict[str, Any]]:
    with get_db() as session:
        rows = (
            session.query(SupportGroupIdleEpisodeState)
            .filter(SupportGroupIdleEpisodeState.episode_started_at.isnot(None))
            .all()
        )
        return [_row_to_dict(r) for r in rows]


def close_episode(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    close_reason: str = CLOSE_REASON_FLOW_END,
) -> None:
    """Clear durable episode fields and cancel timers (flow-end / silence / cap)."""
    cid = int(chat_id)
    _cancel_jobs(cid, job_queue=job_queue, include_hardcap=True)
    history_id = None
    with get_db() as session:
        row = session.get(SupportGroupIdleEpisodeState, cid)
        if row is None:
            return
        history_id = getattr(row, "history_episode_id", None)
        row.episode_started_at = None
        row.last_human_at = None
        row.burst_json = []
        row.history_episode_id = None
        row.updated_at = _now()
    close_history_episode(history_id, close_reason=close_reason)
    logger.info(
        "support_group_idle_episode: closed chat_id=%s reason=%s",
        cid,
        close_reason,
    )


def format_burst_message_text(burst: list[dict[str, str]]) -> str | None:
    if not burst:
        return None
    lines: list[str] = []
    for i, item in enumerate(burst):
        if i > 0:
            lines.append("---")
        body = (item.get("text") or "").strip()
        if body:
            lines.append(body)
    text = "\n".join(lines).strip()
    return text or None


def _schedule_debounce(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    when: float | None = None,
    club_id: int | None = None,
    title: str | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        logger.warning(
            "support_group_idle_episode: no job_queue for debounce chat_id=%s",
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
        delay = 0.1
    jq.run_once(
        _idle_debounce_callback,
        when=delay,
        data={
            "chat_id": int(chat_id),
            "club_id": club_id,
            "title": title,
        },
        name=name,
        job_kwargs={"misfire_grace_time": 30},
    )


def _schedule_silence(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    when: float | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    name = _silence_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass
    delay = float(idle_episode_silence_seconds() if when is None else when)
    if delay <= 0:
        delay = 0.1
    jq.run_once(
        _idle_silence_callback,
        when=delay,
        data={"chat_id": int(chat_id)},
        name=name,
        job_kwargs={"misfire_grace_time": 60},
    )


def _schedule_hardcap(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    when: float | None = None,
) -> None:
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    name = _hardcap_job_name(chat_id)
    try:
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()
    except Exception:
        pass
    delay = float(idle_episode_hard_cap_seconds() if when is None else when)
    if delay <= 0:
        delay = 0.1
    jq.run_once(
        _idle_hardcap_callback,
        when=delay,
        data={"chat_id": int(chat_id)},
        name=name,
        job_kwargs={"misfire_grace_time": 60},
    )


def _burst_entry(
    message_text: str | None,
    trigger_message: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if trigger_message:
        return dict(trigger_message)
    body = (message_text or "").strip()
    if not body:
        return None
    return {"text": body}


def _persist_open_or_feed(
    chat_id: int,
    *,
    title: str | None,
    message_text: str | None,
    now: datetime | None,
    feed_burst: bool,
    club_id: int | None = None,
    trigger_message: dict[str, Any] | None = None,
) -> tuple[bool, list[dict[str, Any]], Any]:
    """Write DB. Returns (opened_new, burst_after, history_episode_id)."""
    cid = int(chat_id)
    ts = _as_utc(now) or _now()
    entry = _burst_entry(message_text, trigger_message)
    history_id = None

    with get_db() as session:
        row = _get_or_create_row(session, cid)
        if title:
            row.title = title
        row.updated_at = ts
        row.last_human_at = ts

        if row.episode_started_at is None:
            history_id = uuid.uuid4()
            try:
                session.add(
                    EscalationEpisode(
                        id=history_id,
                        telegram_chat_id=cid,
                        club_id=int(club_id) if club_id is not None else None,
                        group_title=title,
                        opened_at=ts,
                        trigger_messages=[entry] if entry else [],
                    )
                )
            except Exception:
                logger.warning(
                    "support_group_idle_episode: history insert failed chat_id=%s",
                    cid,
                    exc_info=True,
                )
                history_id = None
            row.history_episode_id = history_id
            row.episode_started_at = ts
            # Opening Slack already covers this message; burst starts empty so the
            # 1m debounce only fires for *follow-up* player messages.
            row.burst_json = [entry] if (feed_burst and entry) else []
            burst = list(row.burst_json)
            return True, burst, history_id

        history_id = row.history_episode_id
        burst = list(row.burst_json) if isinstance(row.burst_json, list) else []
        if entry and (not burst or burst[-1] != entry):
            burst.append(entry)
        row.burst_json = burst
        if entry and history_id is not None:
            hist = session.get(EscalationEpisode, history_id)
            if hist is not None:
                current = list(hist.trigger_messages or [])
                if not current or current[-1] != entry:
                    current.append(entry)
                    hist.trigger_messages = current
        return False, burst, history_id


async def on_player_reach_out(
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    message_text: str | None = None,
    reason: str = REASON_PLAYER_IDLE,
    slack_already_sent: bool = False,
    job_queue: Any | None = None,
    bot: Any | None = None,
    now: datetime | None = None,
    trigger_message: dict[str, Any] | None = None,
) -> ReachOutResult:
    """Open episode (Slack + no-op menu) or feed burst."""
    cid = int(chat_id)
    body = (message_text or "").strip()
    if not body and not slack_already_sent:
        # Media-only still escalates via placeholder from caller; empty = ignore.
        return ReachOutResult(outcome="ignored")

    opened, burst, history_id = _persist_open_or_feed(
        cid,
        title=title,
        message_text=message_text,
        now=now,
        # When deposit already Slacked, still seed this text into burst so a quiet
        # 1m can re-surface it as follow-up; when we Slack player_idle ourselves,
        # leave burst empty until a later feed.
        feed_burst=bool(slack_already_sent),
        club_id=club_id,
        trigger_message=trigger_message,
    )

    jq = _resolve_job_queue(job_queue)
    event_id: int | None = None

    if opened:
        if not slack_already_sent:
            _ok, event_id = await notify_escalation_slack(
                reason,
                club_id=club_id,
                chat_id=cid,
                title=title,
                message_text=message_text,
                episode_id=history_id,
                trigger_messages=[trigger_message]
                if trigger_message
                else ([{"text": body}] if body else None),
            )
            del _ok
        try:
            await offer_idle_help_prompt(
                bot,
                cid,
                club_id=club_id,
                title=title,
            )
        except Exception:
            logger.warning(
                "support_group_idle_episode: offer_idle_help_prompt failed chat_id=%s",
                cid,
                exc_info=True,
            )
        _cancel_jobs(cid, job_queue=jq, include_hardcap=True)
        _schedule_hardcap(cid, job_queue=jq)
        _schedule_silence(cid, job_queue=jq)
        if slack_already_sent and burst:
            _schedule_debounce(
                cid, job_queue=jq, club_id=club_id, title=title
            )
        logger.info(
            "support_group_idle_episode: opened chat_id=%s slack_already_sent=%s",
            cid,
            slack_already_sent,
        )
        return ReachOutResult(
            outcome="opened",
            episode_id=history_id if isinstance(history_id, UUID) else None,
            escalation_event_id=event_id,
        )

    # Already open: feed burst + reschedule debounce/silence (hardcap stays).
    _schedule_silence(cid, job_queue=jq)
    if burst:
        _schedule_debounce(cid, job_queue=jq, club_id=club_id, title=title)
    return ReachOutResult(
        outcome="fed",
        episode_id=history_id if isinstance(history_id, UUID) else None,
    )


async def feed_or_open_episode(
    chat_id: int,
    *,
    club_id: int | None,
    title: str | None = None,
    message_text: str | None = None,
    slack_already_sent: bool = True,
    job_queue: Any | None = None,
    bot: Any | None = None,
    now: datetime | None = None,
    trigger_message: dict[str, Any] | None = None,
) -> ReachOutResult:
    """Deposit path: reason Slack already sent; open/feed without double Slack."""
    return await on_player_reach_out(
        chat_id,
        club_id=club_id,
        title=title,
        message_text=message_text,
        reason=REASON_PLAYER_IDLE,
        slack_already_sent=slack_already_sent,
        job_queue=job_queue,
        bot=bot,
        now=now,
        trigger_message=trigger_message,
    )



def on_staff_human(
    chat_id: int,
    *,
    job_queue: Any | None = None,
    now: datetime | None = None,
    title: str | None = None,
) -> None:
    """Staff message: clear burst, cancel 1m debounce, bump last_human, keep open."""
    cid = int(chat_id)
    state = load_episode_state(cid)
    if state is None:
        return
    ts = _as_utc(now) or _now()
    with get_db() as session:
        row = session.get(SupportGroupIdleEpisodeState, cid)
        if row is None or row.episode_started_at is None:
            return
        if title:
            row.title = title
        row.last_human_at = ts
        row.burst_json = []
        row.updated_at = ts
    _cancel_jobs(
        cid,
        job_queue=job_queue,
        include_hardcap=False,
        include_silence=False,
        include_debounce=True,
    )
    _schedule_silence(cid, job_queue=job_queue)
    logger.info("support_group_idle_episode: staff cleared burst chat_id=%s", cid)


async def _idle_debounce_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or context.job.chat_id)
    club_id = data.get("club_id")
    title = data.get("title")
    state = load_episode_state(chat_id)
    if state is None:
        return

    last_at = state.get("last_human_at")
    if last_at is not None:
        quiet = (_now() - last_at).total_seconds()
        if quiet + 0.5 < float(awaiting_agent_debounce_seconds()):
            return

    burst = state.get("burst") or []
    if not burst:
        return

    body = format_burst_message_text(burst)
    try:
        await notify_escalation_slack(
            REASON_PLAYER_IDLE_FOLLOWUP,
            club_id=int(club_id) if club_id is not None else None,
            chat_id=chat_id,
            title=title or state.get("title"),
            message_text=body,
            episode_id=state.get("history_episode_id"),
            trigger_messages=list(burst) if burst else None,
        )
    except Exception:
        logger.warning(
            "support_group_idle_episode: followup slack failed chat_id=%s",
            chat_id,
            exc_info=True,
        )

    with get_db() as session:
        row = session.get(SupportGroupIdleEpisodeState, chat_id)
        if row is None or row.episode_started_at is None:
            return
        row.burst_json = []
        row.updated_at = _now()
    logger.info("support_group_idle_episode: followup slacked chat_id=%s", chat_id)


async def _idle_silence_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or context.job.chat_id)
    state = load_episode_state(chat_id)
    if state is None:
        return
    last_at = state.get("last_human_at")
    if last_at is None:
        close_episode(
            chat_id,
            job_queue=getattr(context, "job_queue", None),
            close_reason=CLOSE_REASON_SILENCE,
        )
        return
    elapsed = (_now() - last_at).total_seconds()
    if elapsed + 0.5 < float(idle_episode_silence_seconds()):
        # Newer human activity should have rescheduled; bail.
        return
    close_episode(
        chat_id,
        job_queue=getattr(context, "job_queue", None),
        close_reason=CLOSE_REASON_SILENCE,
    )
    logger.info("support_group_idle_episode: silence closed chat_id=%s", chat_id)


async def _idle_hardcap_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    chat_id = int(data.get("chat_id") or context.job.chat_id)
    close_episode(
        chat_id,
        job_queue=getattr(context, "job_queue", None),
        close_reason=CLOSE_REASON_HARD_CAP,
    )
    logger.info("support_group_idle_episode: hardcap closed chat_id=%s", chat_id)


def restore_support_group_idle_episode_jobs(job_queue: Any | None = None) -> None:
    """Re-arm debounce / silence / hardcap after worker restart."""
    jq = _resolve_job_queue(job_queue)
    if jq is None:
        return
    now = _now()
    debounce_s = float(awaiting_agent_debounce_seconds())
    silence_s = float(idle_episode_silence_seconds())
    hardcap_s = float(idle_episode_hard_cap_seconds())

    for state in list_open_episodes():
        chat_id = int(state["telegram_chat_id"])
        started = state.get("episode_started_at")
        if started is None:
            continue
        hardcap_remaining = hardcap_s - (now - started).total_seconds()
        if hardcap_remaining <= 0:
            close_episode(
                chat_id, job_queue=jq, close_reason=CLOSE_REASON_HARD_CAP
            )
            continue

        last_at = state.get("last_human_at") or started
        silence_remaining = silence_s - (now - last_at).total_seconds()
        if silence_remaining <= 0:
            close_episode(
                chat_id, job_queue=jq, close_reason=CLOSE_REASON_SILENCE
            )
            continue

        _schedule_hardcap(chat_id, job_queue=jq, when=hardcap_remaining)
        _schedule_silence(chat_id, job_queue=jq, when=silence_remaining)

        burst = state.get("burst") or []
        if burst:
            debounce_remaining = debounce_s - (now - last_at).total_seconds()
            _schedule_debounce(
                chat_id,
                job_queue=jq,
                when=debounce_remaining,
                title=state.get("title"),
            )
        logger.info(
            "support_group_idle_episode: restored chat_id=%s hardcap_remaining=%.1f "
            "silence_remaining=%.1f burst=%s",
            chat_id,
            hardcap_remaining,
            silence_remaining,
            bool(burst),
        )
