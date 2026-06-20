"""Player dispute support notes for AM shift handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from bot.services.player_details import _GG_RE
from db.connection import get_db
from db.models import Club, PlayerDetails, PlayerSupportIssue, PlayerSupportNote


class SupportNoteValidationError(ValueError):
    pass


@dataclass(frozen=True)
class OpenIssueSummary:
    issue_id: int
    club_id: int
    club_label: str
    gg_player_id: str
    status: str
    latest_next_steps: str
    latest_note_at: datetime
    note_count: int


@dataclass(frozen=True)
class NoteHistoryEntry:
    note_id: int
    issue_id: int
    club_label: str
    gg_player_id: str
    issue_status: str
    situation: str
    actions_taken: str
    next_steps: str
    created_at: datetime


@dataclass(frozen=True)
class ResolveResult:
    gg_player_id: str
    resolved_count: int
    club_labels: tuple[str, ...]


def validate_gg_player_id(raw: str) -> str:
    value = (raw or "").strip()
    if not _GG_RE.match(value):
        raise SupportNoteValidationError(
            "Invalid player id format (example: 8190-5287)."
        )
    return value


def club_label_for_id(session: Session, club_id: int) -> str:
    club = session.get(Club, club_id)
    if not club:
        return f"club#{club_id}"
    name = (club.name or "").strip()
    return name or f"club#{club_id}"


def lookup_club_ids_for_player(session: Session, gg_player_id: str) -> list[int]:
    rows = (
        session.query(PlayerDetails.club_id)
        .filter(PlayerDetails.gg_player_id == gg_player_id)
        .order_by(PlayerDetails.club_id)
        .all()
    )
    return [int(r[0]) for r in rows]


def get_or_create_open_issue(
    session: Session,
    *,
    club_id: int,
    gg_player_id: str,
    telegram_chat_id: int | None = None,
) -> PlayerSupportIssue:
    gg_player_id = validate_gg_player_id(gg_player_id)
    issue = (
        session.query(PlayerSupportIssue)
        .filter(
            PlayerSupportIssue.club_id == club_id,
            PlayerSupportIssue.gg_player_id == gg_player_id,
            PlayerSupportIssue.status == "open",
        )
        .first()
    )
    if issue:
        if telegram_chat_id is not None and issue.telegram_chat_id is None:
            issue.telegram_chat_id = telegram_chat_id
        return issue

    issue = PlayerSupportIssue(
        club_id=club_id,
        gg_player_id=gg_player_id,
        status="open",
        telegram_chat_id=telegram_chat_id,
    )
    session.add(issue)
    session.flush()
    return issue


def add_note(
    *,
    club_id: int,
    gg_player_id: str,
    situation: str,
    actions_taken: str,
    next_steps: str,
    created_by_telegram_user_id: int,
    source_telegram_chat_id: int | None = None,
    telegram_chat_id: int | None = None,
) -> tuple[PlayerSupportNote, PlayerSupportIssue]:
    situation = (situation or "").strip()
    actions_taken = (actions_taken or "").strip()
    next_steps = (next_steps or "").strip()
    if not situation:
        raise SupportNoteValidationError("Situation is required.")
    if not actions_taken:
        raise SupportNoteValidationError("Actions taken is required.")
    if not next_steps:
        raise SupportNoteValidationError("Next steps is required.")

    with get_db() as session:
        issue = get_or_create_open_issue(
            session,
            club_id=club_id,
            gg_player_id=gg_player_id,
            telegram_chat_id=telegram_chat_id or source_telegram_chat_id,
        )
        note = PlayerSupportNote(
            issue_id=issue.id,
            situation=situation,
            actions_taken=actions_taken,
            next_steps=next_steps,
            created_by_telegram_user_id=created_by_telegram_user_id,
            source_telegram_chat_id=source_telegram_chat_id,
        )
        session.add(note)
        session.flush()
        session.refresh(note)
        session.refresh(issue)
        return note, issue


def list_open_issues() -> list[OpenIssueSummary]:
    with get_db() as session:
        latest_note_sq = (
            session.query(
                PlayerSupportNote.issue_id.label("issue_id"),
                func.max(PlayerSupportNote.created_at).label("latest_at"),
            )
            .group_by(PlayerSupportNote.issue_id)
            .subquery()
        )
        note_count_sq = (
            session.query(
                PlayerSupportNote.issue_id.label("issue_id"),
                func.count(PlayerSupportNote.id).label("note_count"),
            )
            .group_by(PlayerSupportNote.issue_id)
            .subquery()
        )
        rows = (
            session.query(
                PlayerSupportIssue,
                PlayerSupportNote.next_steps,
                latest_note_sq.c.latest_at,
                note_count_sq.c.note_count,
            )
            .join(latest_note_sq, latest_note_sq.c.issue_id == PlayerSupportIssue.id)
            .join(
                PlayerSupportNote,
                (PlayerSupportNote.issue_id == PlayerSupportIssue.id)
                & (PlayerSupportNote.created_at == latest_note_sq.c.latest_at),
            )
            .join(note_count_sq, note_count_sq.c.issue_id == PlayerSupportIssue.id)
            .filter(PlayerSupportIssue.status == "open")
            .order_by(latest_note_sq.c.latest_at.desc())
            .all()
        )
        out: list[OpenIssueSummary] = []
        for issue, next_steps, latest_at, note_count in rows:
            out.append(
                OpenIssueSummary(
                    issue_id=issue.id,
                    club_id=issue.club_id,
                    club_label=club_label_for_id(session, issue.club_id),
                    gg_player_id=issue.gg_player_id,
                    status=issue.status,
                    latest_next_steps=(next_steps or "").strip(),
                    latest_note_at=latest_at,
                    note_count=int(note_count or 0),
                )
            )
        return out


def get_player_note_history(gg_player_id: str) -> list[NoteHistoryEntry]:
    gg_player_id = validate_gg_player_id(gg_player_id)
    with get_db() as session:
        rows = (
            session.query(PlayerSupportNote, PlayerSupportIssue)
            .join(PlayerSupportIssue, PlayerSupportNote.issue_id == PlayerSupportIssue.id)
            .filter(PlayerSupportIssue.gg_player_id == gg_player_id)
            .order_by(PlayerSupportNote.created_at.desc())
            .all()
        )
        return [
            NoteHistoryEntry(
                note_id=note.id,
                issue_id=issue.id,
                club_label=club_label_for_id(session, issue.club_id),
                gg_player_id=issue.gg_player_id,
                issue_status=issue.status,
                situation=note.situation,
                actions_taken=note.actions_taken,
                next_steps=note.next_steps,
                created_at=note.created_at,
            )
            for note, issue in rows
        ]


def resolve_issues_for_player(
    gg_player_id: str,
    *,
    resolved_by_telegram_user_id: int,
) -> ResolveResult:
    gg_player_id = validate_gg_player_id(gg_player_id)
    now = datetime.now(timezone.utc)
    with get_db() as session:
        issues = (
            session.query(PlayerSupportIssue)
            .filter(
                PlayerSupportIssue.gg_player_id == gg_player_id,
                PlayerSupportIssue.status == "open",
            )
            .all()
        )
        labels: list[str] = []
        for issue in issues:
            issue.status = "resolved"
            issue.resolved_at = now
            issue.resolved_by_telegram_user_id = resolved_by_telegram_user_id
            label = club_label_for_id(session, issue.club_id)
            if label not in labels:
                labels.append(label)
        return ResolveResult(
            gg_player_id=gg_player_id,
            resolved_count=len(issues),
            club_labels=tuple(labels),
        )


def _format_age(when: datetime | None) -> str:
    if when is None:
        return "unknown"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - when.astimezone(timezone.utc)
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        mins = max(1, int(delta.total_seconds() // 60))
        return f"{mins}m ago"
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _truncate(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def format_open_issues_list(summaries: list[OpenIssueSummary]) -> str:
    if not summaries:
        return "No unresolved player issues."
    lines = [f"Open issues ({len(summaries)}):"]
    for row in summaries:
        preview = _truncate(row.latest_next_steps)
        lines.append(
            f"• {row.club_label} / {row.gg_player_id} — {preview} "
            f"({row.note_count} note{'s' if row.note_count != 1 else ''}, "
            f"{_format_age(row.latest_note_at)})"
        )
    return "\n".join(lines)


def format_player_note_history(entries: list[NoteHistoryEntry]) -> str:
    if not entries:
        return "No notes found for that player id."
    lines = [f"Notes for {entries[0].gg_player_id} ({len(entries)}):"]
    for entry in entries:
        status = entry.issue_status.upper()
        lines.append(
            f"\n— Note #{entry.note_id} ({entry.club_label}, {status}, "
            f"{_format_age(entry.created_at)})\n"
            f"Situation: {entry.situation}\n"
            f"Actions: {entry.actions_taken}\n"
            f"Next: {entry.next_steps}"
        )
    return "\n".join(lines)


def format_resolve_result(result: ResolveResult) -> str:
    if result.resolved_count == 0:
        return f"No open issues for {result.gg_player_id}."
    clubs = ", ".join(result.club_labels) if result.club_labels else "unknown club"
    plural = "issue" if result.resolved_count == 1 else "issues"
    return (
        f"Resolved {result.resolved_count} open {plural} "
        f"for {result.gg_player_id} ({clubs})."
    )


def format_note_saved(
    *,
    note_id: int,
    club_label: str,
    gg_player_id: str,
    status: str,
) -> str:
    return f"Note #{note_id} saved — {club_label} / {gg_player_id} ({status})."
