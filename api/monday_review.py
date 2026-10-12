"""Monday Review: the weekly head checklist (tasks, notes, completions, history).

Shared by the dashboard router (``api/routes/monday_review.py``) and the MCP tools
(``mcp_server/tools/monday_review.py``). Every function takes an open SQLAlchemy
``Session`` and never commits; callers own the transaction. Validation raises
``ValueError`` (bad input) or ``LookupError`` (missing row) so each caller can map
them to its own error shape.

A "week" is identified by its Monday (``week_of``) on the America/New_York
calendar. Persistent notes show every Monday; temporary notes show only for the
Monday in ``week_of``. Completions are one row per task per Monday.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import (
    MondayReviewCompletion,
    MondayReviewTask,
    MondayReviewTaskNote,
)

ET = ZoneInfo("America/New_York")

ASSIGNEE_PRESETS: tuple[str, ...] = ("Aiden", "Edaad")
NOTE_KIND_PERSISTENT = "persistent"
NOTE_KIND_TEMPORARY = "temporary"
NOTE_KINDS: tuple[str, ...] = (NOTE_KIND_PERSISTENT, NOTE_KIND_TEMPORARY)

MAX_TITLE = 200
MAX_DESCRIPTION = 4000
MAX_LINKS = 25
MAX_LINK_LABEL = 120
MAX_LINK_URL = 2048
MAX_ASSIGNEES = 10
MAX_ASSIGNEE_NAME = 64
MAX_NOTE_BODY = 4000
MAX_COMPLETION_NOTES = 4000
MAX_HISTORY_WEEKS = 52


# ── Week helpers ─────────────────────────────────────────────────────────────


def today_et() -> date:
    return datetime.now(ET).date()


def monday_of(day: date) -> date:
    """The Monday that starts the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def current_week_of(today: Optional[date] = None) -> date:
    """Monday of the review week in progress (today if today is a Monday)."""
    return monday_of(today or today_et())


def next_week_of(today: Optional[date] = None) -> date:
    """The first Monday strictly after today: where temporary notes default to."""
    return current_week_of(today) + timedelta(days=7)


def ensure_monday(day: date) -> date:
    if day.weekday() != 0:
        raise ValueError(
            f"week_of must be a Monday; {day.isoformat()} is a "
            f"{day.strftime('%A')} (use {monday_of(day).isoformat()})"
        )
    return day


def parse_week_of(value: Any, *, default: Optional[date] = None) -> date:
    """Accept a ``date`` or ``YYYY-MM-DD`` string; empty falls back to ``default``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise ValueError("week_of is required")
        return ensure_monday(default)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return ensure_monday(value)
    try:
        parsed = date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"expected a YYYY-MM-DD date for week_of, got {value!r}")
    return ensure_monday(parsed)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_et_date(value: Optional[datetime]) -> Optional[date]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ET).date()


# ── Validation / normalization ───────────────────────────────────────────────


def normalize_title(raw: Any) -> str:
    title = " ".join(str(raw or "").split())
    if not title:
        raise ValueError("title is required")
    if len(title) > MAX_TITLE:
        raise ValueError(f"title must be at most {MAX_TITLE} characters")
    return title


def normalize_description(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) > MAX_DESCRIPTION:
        raise ValueError(f"description must be at most {MAX_DESCRIPTION} characters")
    return text


def normalize_url(raw: Any) -> str:
    url = str(raw or "").strip()
    if not url:
        raise ValueError("link url is required")
    lowered = url.lower()
    if not (lowered.startswith("https://") or lowered.startswith("http://")):
        # Also blocks javascript:/data: URLs that would be unsafe as hyperlinks.
        if "://" in url or lowered.startswith(("javascript:", "data:", "mailto:")):
            raise ValueError("link url must start with http:// or https://")
        url = f"https://{url}"
    if len(url) > MAX_LINK_URL:
        raise ValueError(f"link url must be at most {MAX_LINK_URL} characters")
    if any(ch.isspace() for ch in url):
        raise ValueError("link url must not contain spaces")
    return url


def normalize_links(raw: Any) -> list[dict[str, str]]:
    """``[{"label", "url"}, ...]``; label falls back to the URL host when blank."""
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("links must be a list of {label, url} objects")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            item = {"label": "", "url": item}
        if not isinstance(item, dict):
            raise ValueError("each link must be a {label, url} object")
        url = normalize_url(item.get("url"))
        label = " ".join(str(item.get("label") or "").split())
        if not label:
            label = url.split("://", 1)[-1].split("/", 1)[0] or url
        if len(label) > MAX_LINK_LABEL:
            raise ValueError(f"link label must be at most {MAX_LINK_LABEL} characters")
        out.append({"label": label, "url": url})
    if len(out) > MAX_LINKS:
        raise ValueError(f"at most {MAX_LINKS} links per task")
    return out


def normalize_assignee(raw: Any) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise ValueError("assignee name must not be blank")
    if len(name) > MAX_ASSIGNEE_NAME:
        raise ValueError(
            f"assignee name must be at most {MAX_ASSIGNEE_NAME} characters"
        )
    for preset in ASSIGNEE_PRESETS:
        if name.lower() == preset.lower():
            return preset
    return name


def normalize_assignees(raw: Any) -> list[str]:
    """Names, de-duplicated case-insensitively; presets keep canonical casing."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("assignees must be a list of names")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item is None or not str(item).strip():
            continue
        name = normalize_assignee(item)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    if len(out) > MAX_ASSIGNEES:
        raise ValueError(f"at most {MAX_ASSIGNEES} assignees per task")
    return out


def normalize_note_kind(raw: Any) -> str:
    kind = str(raw or "").strip().lower()
    if kind not in NOTE_KINDS:
        raise ValueError("kind must be 'persistent' or 'temporary'")
    return kind


def normalize_note_body(raw: Any) -> str:
    body = str(raw or "").strip()
    if not body:
        raise ValueError("note body is required")
    if len(body) > MAX_NOTE_BODY:
        raise ValueError(f"note body must be at most {MAX_NOTE_BODY} characters")
    return body


def normalize_completion_notes(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) > MAX_COMPLETION_NOTES:
        raise ValueError(
            f"completion notes must be at most {MAX_COMPLETION_NOTES} characters"
        )
    return text


def normalize_completed_by(raw: Any) -> Optional[str]:
    text = " ".join(str(raw or "").split())
    if not text:
        return None
    return normalize_assignee(text)


# ── Serialization ────────────────────────────────────────────────────────────


def task_dict(task: MondayReviewTask) -> dict[str, Any]:
    return {
        "id": int(task.id),
        "title": task.title,
        "description": task.description,
        "links": list(task.links_json or []),
        "assignees": list(task.assignees_json or []),
        "sort_order": int(task.sort_order or 0),
        "archived": task.archived_at is not None,
        "archived_at": task.archived_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def note_dict(note: MondayReviewTaskNote) -> dict[str, Any]:
    return {
        "id": int(note.id),
        "task_id": int(note.task_id),
        "kind": note.kind,
        "body": note.body,
        "week_of": note.week_of,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def completion_dict(row: MondayReviewCompletion) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "task_id": int(row.task_id),
        "week_of": row.week_of,
        "completed_by": row.completed_by,
        "notes": row.notes,
        "completed_at": row.completed_at,
        "updated_at": row.updated_at,
    }


# ── Tasks ────────────────────────────────────────────────────────────────────


def list_tasks(
    session: Session, *, include_archived: bool = False
) -> list[MondayReviewTask]:
    query = session.query(MondayReviewTask)
    if not include_archived:
        query = query.filter(MondayReviewTask.archived_at.is_(None))
    return query.order_by(
        MondayReviewTask.archived_at.isnot(None),
        MondayReviewTask.sort_order.asc(),
        MondayReviewTask.id.asc(),
    ).all()


def get_task(session: Session, task_id: int) -> MondayReviewTask:
    task = session.get(MondayReviewTask, int(task_id))
    if task is None:
        raise LookupError(f"Monday Review task {task_id} not found")
    return task


def find_task(session: Session, ref: Any) -> MondayReviewTask:
    """Resolve a task by id or by (case-insensitive) exact or unique-prefix title."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        return get_task(session, int(ref))
    needle = " ".join(str(ref or "").split()).lower()
    if not needle:
        raise ValueError("task reference is required")
    tasks = list_tasks(session, include_archived=True)
    exact = [t for t in tasks if (t.title or "").lower() == needle]
    if len(exact) == 1:
        return exact[0]
    partial = [t for t in tasks if needle in (t.title or "").lower()]
    active_partial = [t for t in partial if t.archived_at is None]
    candidates = active_partial or partial
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise LookupError(f"no Monday Review task matching {ref!r}")
    titles = ", ".join(f"{t.id}: {t.title}" for t in candidates[:8])
    raise ValueError(f"task reference {ref!r} is ambiguous; candidates: {titles}")


def create_task(
    session: Session,
    *,
    title: Any,
    description: Any = None,
    links: Any = None,
    assignees: Any = None,
) -> MondayReviewTask:
    max_order = (
        session.query(func.coalesce(func.max(MondayReviewTask.sort_order), -1))
        .filter(MondayReviewTask.archived_at.is_(None))
        .scalar()
    )
    task = MondayReviewTask(
        title=normalize_title(title),
        description=normalize_description(description),
        links_json=normalize_links(links),
        assignees_json=normalize_assignees(assignees),
        sort_order=int(max_order if max_order is not None else -1) + 1,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(task)
    session.flush()
    return task


def update_task(
    session: Session, task: MondayReviewTask, changes: dict[str, Any]
) -> MondayReviewTask:
    """Apply only the keys present in ``changes``."""
    if "title" in changes:
        task.title = normalize_title(changes["title"])
    if "description" in changes:
        task.description = normalize_description(changes["description"])
    if "links" in changes:
        task.links_json = normalize_links(changes["links"])
    if "assignees" in changes:
        task.assignees_json = normalize_assignees(changes["assignees"])
    if "archived" in changes and changes["archived"] is not None:
        if bool(changes["archived"]):
            task.archived_at = task.archived_at or _now()
        else:
            task.archived_at = None
    task.updated_at = _now()
    session.flush()
    return task


def reorder_tasks(
    session: Session, ordered_ids: Iterable[Any]
) -> list[MondayReviewTask]:
    """Set ``sort_order`` from the given id order; ids not listed keep relative order
    after the listed ones."""
    wanted: list[int] = []
    for raw in ordered_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ValueError("ordered_ids must be integers")
        if value not in wanted:
            wanted.append(value)
    tasks = list_tasks(session, include_archived=True)
    by_id = {int(t.id): t for t in tasks}
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise LookupError(f"unknown task ids: {missing}")
    ordered = [by_id[i] for i in wanted] + [t for t in tasks if int(t.id) not in wanted]
    for position, task in enumerate(ordered):
        if int(task.sort_order or 0) != position:
            task.sort_order = position
            task.updated_at = _now()
    session.flush()
    return ordered


def delete_task(session: Session, task: MondayReviewTask) -> None:
    session.delete(task)
    session.flush()


# ── Notes ────────────────────────────────────────────────────────────────────


def get_note(session: Session, note_id: int) -> MondayReviewTaskNote:
    note = session.get(MondayReviewTaskNote, int(note_id))
    if note is None:
        raise LookupError(f"Monday Review note {note_id} not found")
    return note


def add_note(
    session: Session,
    task: MondayReviewTask,
    *,
    kind: Any,
    body: Any,
    week_of: Any = None,
) -> MondayReviewTaskNote:
    kind = normalize_note_kind(kind)
    if kind == NOTE_KIND_PERSISTENT:
        week: Optional[date] = None
    else:
        week = parse_week_of(week_of, default=next_week_of())
    note = MondayReviewTaskNote(
        task_id=int(task.id),
        kind=kind,
        body=normalize_note_body(body),
        week_of=week,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(note)
    session.flush()
    return note


def update_note(
    session: Session, note: MondayReviewTaskNote, changes: dict[str, Any]
) -> MondayReviewTaskNote:
    kind = normalize_note_kind(changes.get("kind") or note.kind)
    if "body" in changes:
        note.body = normalize_note_body(changes["body"])
    if kind == NOTE_KIND_PERSISTENT:
        note.week_of = None
    else:
        raw_week = changes.get("week_of") if "week_of" in changes else note.week_of
        note.week_of = parse_week_of(raw_week, default=next_week_of())
    note.kind = kind
    note.updated_at = _now()
    session.flush()
    return note


def delete_note(session: Session, note: MondayReviewTaskNote) -> None:
    session.delete(note)
    session.flush()


# ── Completions ──────────────────────────────────────────────────────────────


def get_completion(
    session: Session, task_id: int, week_of: date
) -> Optional[MondayReviewCompletion]:
    return (
        session.query(MondayReviewCompletion)
        .filter(
            MondayReviewCompletion.task_id == int(task_id),
            MondayReviewCompletion.week_of == week_of,
        )
        .first()
    )


def complete_task(
    session: Session,
    task: MondayReviewTask,
    *,
    week_of: Any = None,
    completed_by: Any = None,
    notes: Any = None,
) -> tuple[MondayReviewCompletion, bool]:
    """Check a task off for a week. Re-completing updates who/notes in place.

    Returns ``(completion, created)``.
    """
    week = parse_week_of(week_of, default=current_week_of())
    if week > current_week_of():
        raise ValueError("cannot complete a task for a future week")
    who = normalize_completed_by(completed_by)
    text = normalize_completion_notes(notes)
    existing = get_completion(session, int(task.id), week)
    if existing is not None:
        if completed_by is not None:
            existing.completed_by = who
        if notes is not None:
            existing.notes = text
        existing.updated_at = _now()
        session.flush()
        return existing, False
    row = MondayReviewCompletion(
        task_id=int(task.id),
        week_of=week,
        completed_by=who,
        notes=text,
        completed_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row, True


def uncomplete_task(session: Session, task: MondayReviewTask, *, week_of: Any) -> bool:
    week = parse_week_of(week_of, default=current_week_of())
    existing = get_completion(session, int(task.id), week)
    if existing is None:
        return False
    session.delete(existing)
    session.flush()
    return True


# ── Board / history ──────────────────────────────────────────────────────────


def _task_expected_for_week(task: MondayReviewTask, week_of: date) -> bool:
    """Was this task part of the checklist in the week starting ``week_of``?

    A task counts from the week it was created (created mid-week still shows that
    Monday) until the week it was archived (archive week included).
    """
    created_day = _as_et_date(task.created_at) or today_et()
    if monday_of(created_day) > week_of:
        return False
    archived_day = _as_et_date(task.archived_at)
    if archived_day is not None and monday_of(archived_day) < week_of:
        return False
    return True


def _week_meta(week_of: date, today: Optional[date] = None) -> dict[str, Any]:
    now_week = current_week_of(today)
    return {
        "week_of": week_of,
        "week_end": week_of + timedelta(days=6),
        "is_current_week": week_of == now_week,
        "is_past_week": week_of < now_week,
        "is_future_week": week_of > now_week,
        "current_week_of": now_week,
        "next_week_of": now_week + timedelta(days=7),
        "today_et": today or today_et(),
    }


def board_tasks(session: Session, week_of: date) -> list[MondayReviewTask]:
    """Tasks shown on the board for ``week_of`` (ordered), including any task that
    has a completion for that week even if it has since been archived."""
    tasks = list_tasks(session, include_archived=True)
    completed_ids = {
        int(task_id)
        for (task_id,) in session.query(MondayReviewCompletion.task_id)
        .filter(MondayReviewCompletion.week_of == week_of)
        .all()
    }
    return [
        t
        for t in tasks
        if _task_expected_for_week(t, week_of) or int(t.id) in completed_ids
    ]


def build_board(
    session: Session, week_of: Any = None, *, today: Optional[date] = None
) -> dict[str, Any]:
    """Everything the Monday board needs for one week."""
    week = parse_week_of(week_of, default=current_week_of(today))
    tasks = board_tasks(session, week)
    task_ids = [int(t.id) for t in tasks]

    notes_by_task: dict[int, list[MondayReviewTaskNote]] = {i: [] for i in task_ids}
    if task_ids:
        for note in (
            session.query(MondayReviewTaskNote)
            .filter(MondayReviewTaskNote.task_id.in_(task_ids))
            .order_by(
                MondayReviewTaskNote.created_at.asc(), MondayReviewTaskNote.id.asc()
            )
            .all()
        ):
            notes_by_task.setdefault(int(note.task_id), []).append(note)

    completions: dict[int, MondayReviewCompletion] = {}
    if task_ids:
        for row in (
            session.query(MondayReviewCompletion)
            .filter(
                MondayReviewCompletion.week_of == week,
                MondayReviewCompletion.task_id.in_(task_ids),
            )
            .all()
        ):
            completions[int(row.task_id)] = row

    items: list[dict[str, Any]] = []
    for task in tasks:
        notes = notes_by_task.get(int(task.id), [])
        persistent = [n for n in notes if n.kind == NOTE_KIND_PERSISTENT]
        this_week = [
            n for n in notes if n.kind == NOTE_KIND_TEMPORARY and n.week_of == week
        ]
        upcoming = [
            n
            for n in notes
            if n.kind == NOTE_KIND_TEMPORARY
            and n.week_of is not None
            and n.week_of > week
        ]
        completion = completions.get(int(task.id))
        item = task_dict(task)
        item.update(
            {
                "persistent_notes": [note_dict(n) for n in persistent],
                "week_notes": [note_dict(n) for n in this_week],
                "upcoming_notes": [note_dict(n) for n in upcoming],
                "completion": completion_dict(completion) if completion else None,
                "done": completion is not None,
            }
        )
        items.append(item)

    done = sum(1 for i in items if i["done"])
    meta = _week_meta(week, today)
    meta.update(
        {
            "tasks": items,
            "total": len(items),
            "done": done,
            "pending": len(items) - done,
            "all_done": bool(items) and done == len(items),
        }
    )
    return meta


def build_history(
    session: Session,
    *,
    limit: int = 12,
    before: Any = None,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """Past weeks (newest first), each with done/missed per task and its notes.

    Weeks run back from the Monday before ``before`` (default: the current week)
    and stop at the week the first task was created, so a week with nothing
    checked off still appears as missed rather than disappearing.
    """
    limit = max(1, min(int(limit or 12), MAX_HISTORY_WEEKS))
    now_week = current_week_of(today)
    end_week = parse_week_of(before, default=now_week)
    tasks = list_tasks(session, include_archived=True)
    if not tasks:
        return {"weeks": [], "has_more": False, "next_before": None}
    earliest = min(monday_of(_as_et_date(t.created_at) or today_et()) for t in tasks)
    # Completions or notes logged for weeks before any task existed (catching up
    # on past Mondays) still count as history.
    for model in (MondayReviewCompletion, MondayReviewTaskNote):
        first_week = session.query(func.min(model.week_of)).scalar()
        if first_week is not None:
            if isinstance(first_week, str):
                first_week = date.fromisoformat(first_week[:10])
            earliest = min(earliest, monday_of(first_week))
    weeks: list[date] = []
    cursor = end_week - timedelta(days=7)
    while cursor >= earliest and len(weeks) < limit:
        weeks.append(cursor)
        cursor -= timedelta(days=7)
    has_more = cursor >= earliest
    if not weeks:
        return {"weeks": [], "has_more": False, "next_before": None}

    oldest, newest = weeks[-1], weeks[0]
    completions_by_week: dict[date, dict[int, MondayReviewCompletion]] = {}
    for row in (
        session.query(MondayReviewCompletion)
        .filter(
            MondayReviewCompletion.week_of >= oldest,
            MondayReviewCompletion.week_of <= newest,
        )
        .all()
    ):
        completions_by_week.setdefault(row.week_of, {})[int(row.task_id)] = row
    notes_by_week: dict[date, dict[int, list[MondayReviewTaskNote]]] = {}
    for note in (
        session.query(MondayReviewTaskNote)
        .filter(
            MondayReviewTaskNote.kind == NOTE_KIND_TEMPORARY,
            MondayReviewTaskNote.week_of >= oldest,
            MondayReviewTaskNote.week_of <= newest,
        )
        .order_by(MondayReviewTaskNote.created_at.asc(), MondayReviewTaskNote.id.asc())
        .all()
    ):
        notes_by_week.setdefault(note.week_of, {}).setdefault(
            int(note.task_id), []
        ).append(note)

    out: list[dict[str, Any]] = []
    for week in weeks:
        week_completions = completions_by_week.get(week, {})
        rows: list[dict[str, Any]] = []
        for task in tasks:
            completion = week_completions.get(int(task.id))
            if completion is None and not _task_expected_for_week(task, week):
                continue
            rows.append(
                {
                    "task_id": int(task.id),
                    "title": task.title,
                    "assignees": list(task.assignees_json or []),
                    "archived": task.archived_at is not None,
                    "status": "done" if completion else "missed",
                    "completion": completion_dict(completion) if completion else None,
                    "week_notes": [
                        note_dict(n)
                        for n in notes_by_week.get(week, {}).get(int(task.id), [])
                    ],
                }
            )
        done = sum(1 for r in rows if r["status"] == "done")
        entry = _week_meta(week, today)
        entry.update(
            {
                "tasks": rows,
                "total": len(rows),
                "done": done,
                "missed": len(rows) - done,
            }
        )
        out.append(entry)
    return {
        "weeks": out,
        "has_more": has_more,
        "next_before": weeks[-1] if has_more else None,
    }
