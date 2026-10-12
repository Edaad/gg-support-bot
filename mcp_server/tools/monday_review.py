"""Monday Review: the heads' weekly checklist (dashboard Monday Review tab)."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from api import monday_review as svc
from mcp_server.auth import actor_from_context
from mcp_server.db import read_session, write_session
from mcp_server.envelope import dry_run, page, single, write_result
from mcp_server.errors import translate_errors
from mcp_server.tools._common import (
    READ_ONLY,
    WRITE,
    WRITE_NON_IDEMPOTENT,
    clamp_limit,
    clamp_offset,
)

_WEEK_NOTES = (
    "week_of is the Monday (America/New_York) that identifies a review week",
    "persistent_notes show every Monday; week_notes are temporary notes for this "
    "week only; upcoming_notes are temporary notes queued for later Mondays",
)


@translate_errors
def get_monday_review_board(week_of: str | None = None) -> dict[str, Any]:
    """The Monday Review checklist for one week: every task heads must do that
    Monday with its links, assignees (Aiden, Edaad or another name), persistent
    notes, this week's temporary notes, notes queued for later weeks, and whether
    it is done (completion has completed_by, completed_at and notes). `week_of` is
    the Monday as YYYY-MM-DD; omit for the current week. `done`/`pending`/`total`
    summarise progress; `all_done` is true when every task is checked off."""
    week = svc.parse_week_of(week_of, default=svc.current_week_of())
    with read_session() as session:
        board = svc.build_board(session, week)
    return single(board, notes=list(_WEEK_NOTES))


@translate_errors
def list_monday_review_tasks(
    include_archived: bool = False, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Configured Monday Review tasks in board order (title, description, links
    [{label, url}], assignees, archived). Archived tasks are hidden from the
    weekly board but keep their completion history; pass include_archived=true
    to see them."""
    limit = clamp_limit(limit)
    offset = clamp_offset(offset)
    with read_session() as session:
        tasks = svc.list_tasks(session, include_archived=include_archived)
        items = [svc.task_dict(t) for t in tasks[offset : offset + limit]]
        total = len(tasks)
    return page(
        items,
        total=total,
        limit=limit,
        offset=offset,
        notes=["tasks are ordered as on the dashboard board (sort_order)"],
    )


@translate_errors
def list_monday_review_history(
    limit: int = 12, before: str | None = None
) -> dict[str, Any]:
    """Past Monday Review weeks newest first, each listing every task that was on
    the checklist with status done (who, when, notes) or missed, plus that week's
    temporary notes. `limit` is a number of weeks (max 52); `before` is a Monday
    YYYY-MM-DD to page further back (default: the current week, which is excluded;
    use get_monday_review_board for it). `next_before` continues the paging."""
    limit = max(1, min(int(limit or 12), svc.MAX_HISTORY_WEEKS))
    with read_session() as session:
        history = svc.build_history(session, limit=limit, before=before)
    return single(
        history,
        notes=[
            "weeks run back from the Monday before `before` to the week the first "
            "task was created; a week with nothing checked off shows every task "
            "as missed",
        ],
    )


def register(mcp: MCPServer) -> None:
    mcp.tool(name="get_monday_review_board", annotations=READ_ONLY)(
        get_monday_review_board
    )
    mcp.tool(name="list_monday_review_tasks", annotations=READ_ONLY)(
        list_monday_review_tasks
    )
    mcp.tool(name="list_monday_review_history", annotations=READ_ONLY)(
        list_monday_review_history
    )


# ── Write tools ──────────────────────────────────────────────────────────────


@translate_errors
def complete_monday_review_task(
    task: str,
    week_of: str | None = None,
    completed_by: str | None = None,
    notes: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Check a Monday Review task off for a week, as the dashboard checkbox does.
    `task` is the task id or (part of) its title; `week_of` is the Monday
    YYYY-MM-DD (default: the current week; future weeks are refused);
    `completed_by` is who did it (Aiden, Edaad or another name); `notes` are
    completion notes kept in the history. Re-completing an already done task
    updates who/notes. Without confirm=true returns a dry run."""
    actor = actor_from_context(ctx)
    week = svc.parse_week_of(week_of, default=svc.current_week_of())
    with read_session() as session:
        row = svc.find_task(session, task)
        task_id, title = int(row.id), str(row.title)
        existing = svc.get_completion(session, task_id, week)
        current = svc.completion_dict(existing) if existing else None
    if week > svc.current_week_of():
        raise ValueError("cannot complete a task for a future week")
    proposed = {
        "task_id": task_id,
        "title": title,
        "week_of": week,
        "completed_by": svc.normalize_completed_by(completed_by)
        if completed_by is not None
        else (current or {}).get("completed_by"),
        "notes": svc.normalize_completion_notes(notes)
        if notes is not None
        else (current or {}).get("notes"),
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=current,
            proposed=proposed,
            message=(
                f"dry run: would {'update the completion of' if current else 'complete'}"
                f" task {task_id} ({title}) for week of {week.isoformat()}"
            ),
        )
    with write_session() as session:
        row = svc.get_task(session, task_id)
        completion, created = svc.complete_task(
            session, row, week_of=week, completed_by=completed_by, notes=notes
        )
        after = svc.completion_dict(completion)
    return write_result(
        actor=actor,
        before=current,
        after=after,
        message=(
            f"{'completed' if created else 'updated completion of'} task {task_id}"
            f" ({title}) for week of {week.isoformat()}"
        ),
    )


@translate_errors
def add_monday_review_note(
    task: str,
    body: str,
    kind: str = "temporary",
    week_of: str | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Attach a reminder to a Monday Review task. kind='persistent' shows every
    Monday; kind='temporary' (default) shows only for the Monday in `week_of`
    (YYYY-MM-DD, default: next Monday, i.e. the following week's review). `task`
    is the task id or (part of) its title. Without confirm=true returns a dry
    run."""
    actor = actor_from_context(ctx)
    note_kind = svc.normalize_note_kind(kind)
    text = svc.normalize_note_body(body)
    week = (
        None
        if note_kind == svc.NOTE_KIND_PERSISTENT
        else svc.parse_week_of(week_of, default=svc.next_week_of())
    )
    with read_session() as session:
        row = svc.find_task(session, task)
        task_id, title = int(row.id), str(row.title)
    proposed = {
        "task_id": task_id,
        "title": title,
        "kind": note_kind,
        "body": text,
        "week_of": week,
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message=f"dry run: would add a {note_kind} note to task {task_id} ({title})",
        )
    with write_session() as session:
        row = svc.get_task(session, task_id)
        note = svc.add_note(session, row, kind=note_kind, body=text, week_of=week)
        after = svc.note_dict(note)
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"added {note_kind} note {after['id']} to task {task_id} ({title})",
    )


@translate_errors
def create_monday_review_task(
    title: str,
    description: str | None = None,
    links: list[dict[str, str]] | None = None,
    assignees: list[str] | None = None,
    confirm: bool = False,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Add a task to the Monday Review checklist, as the dashboard Manage tab
    does. `links` is a list of {label, url} (http/https) shown under the task;
    `assignees` is a list of names (Aiden, Edaad or any other name). The task is
    appended to the end of the board. Without confirm=true returns a dry run."""
    actor = actor_from_context(ctx)
    proposed = {
        "title": svc.normalize_title(title),
        "description": svc.normalize_description(description),
        "links": svc.normalize_links(links),
        "assignees": svc.normalize_assignees(assignees),
    }
    if not confirm:
        return dry_run(
            actor=actor,
            current=None,
            proposed=proposed,
            message="dry run: would create this Monday Review task",
        )
    with write_session() as session:
        row = svc.create_task(
            session,
            title=proposed["title"],
            description=proposed["description"],
            links=proposed["links"],
            assignees=proposed["assignees"],
        )
        after = svc.task_dict(row)
    return write_result(
        actor=actor,
        before=None,
        after=after,
        message=f"created Monday Review task {after['id']} ({after['title']})",
    )


def register_writes(mcp: MCPServer) -> None:
    mcp.tool(name="complete_monday_review_task", annotations=WRITE)(
        complete_monday_review_task
    )
    mcp.tool(name="add_monday_review_note", annotations=WRITE_NON_IDEMPOTENT)(
        add_monday_review_note
    )
    mcp.tool(name="create_monday_review_task", annotations=WRITE_NON_IDEMPOTENT)(
        create_monday_review_task
    )
