"""Admin-only Monday Review: weekly head checklist, notes, completions, history."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api import monday_review as svc
from api.auth import require_admin
from api.schemas_monday_review import (
    MondayReviewBoard,
    MondayReviewCompleteRequest,
    MondayReviewCompletionRead,
    MondayReviewHistory,
    MondayReviewMeta,
    MondayReviewNoteCreate,
    MondayReviewNoteRead,
    MondayReviewNoteUpdate,
    MondayReviewReorder,
    MondayReviewTaskCreate,
    MondayReviewTaskRead,
    MondayReviewTaskUpdate,
)
from db.connection import get_db_dependency

router = APIRouter(
    prefix="/api/monday-review",
    tags=["monday-review"],
    dependencies=[Depends(require_admin)],
)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    return HTTPException(400, str(exc))


@router.get("/meta", response_model=MondayReviewMeta)
def meta():
    return MondayReviewMeta(
        assignee_presets=list(svc.ASSIGNEE_PRESETS),
        current_week_of=svc.current_week_of(),
        next_week_of=svc.next_week_of(),
        today_et=svc.today_et(),
    )


@router.get("/board", response_model=MondayReviewBoard)
def board(
    week_of: Optional[date] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    try:
        return svc.build_board(db, week_of)
    except (ValueError, LookupError) as exc:
        raise _http(exc)


@router.get("/history", response_model=MondayReviewHistory)
def history(
    limit: int = Query(12, ge=1, le=svc.MAX_HISTORY_WEEKS),
    before: Optional[date] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    try:
        return svc.build_history(db, limit=limit, before=before)
    except (ValueError, LookupError) as exc:
        raise _http(exc)


# ── Tasks ────────────────────────────────────────────────────────────────────


@router.get("/tasks", response_model=List[MondayReviewTaskRead])
def list_tasks(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db_dependency),
):
    return [
        svc.task_dict(t) for t in svc.list_tasks(db, include_archived=include_archived)
    ]


@router.post("/tasks", response_model=MondayReviewTaskRead, status_code=201)
def create_task(body: MondayReviewTaskCreate, db: Session = Depends(get_db_dependency)):
    try:
        task = svc.create_task(
            db,
            title=body.title,
            description=body.description,
            links=[link.model_dump() for link in body.links],
            assignees=body.assignees,
        )
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return svc.task_dict(task)


@router.post("/tasks/reorder", response_model=List[MondayReviewTaskRead])
def reorder_tasks(body: MondayReviewReorder, db: Session = Depends(get_db_dependency)):
    try:
        ordered = svc.reorder_tasks(db, body.ordered_ids)
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return [svc.task_dict(t) for t in ordered if t.archived_at is None]


@router.patch("/tasks/{task_id}", response_model=MondayReviewTaskRead)
def update_task(
    task_id: int,
    body: MondayReviewTaskUpdate,
    db: Session = Depends(get_db_dependency),
):
    try:
        task = svc.get_task(db, task_id)
        changes = body.model_dump(exclude_unset=True)
        if "links" in changes and changes["links"] is not None:
            changes["links"] = [link.model_dump() for link in body.links or []]
        task = svc.update_task(db, task, changes)
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return svc.task_dict(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db_dependency)):
    try:
        svc.delete_task(db, svc.get_task(db, task_id))
    except (ValueError, LookupError) as exc:
        raise _http(exc)


# ── Notes ────────────────────────────────────────────────────────────────────


@router.post(
    "/tasks/{task_id}/notes", response_model=MondayReviewNoteRead, status_code=201
)
def add_note(
    task_id: int,
    body: MondayReviewNoteCreate,
    db: Session = Depends(get_db_dependency),
):
    try:
        task = svc.get_task(db, task_id)
        note = svc.add_note(
            db, task, kind=body.kind, body=body.body, week_of=body.week_of
        )
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return svc.note_dict(note)


@router.patch("/notes/{note_id}", response_model=MondayReviewNoteRead)
def update_note(
    note_id: int,
    body: MondayReviewNoteUpdate,
    db: Session = Depends(get_db_dependency),
):
    try:
        note = svc.get_note(db, note_id)
        note = svc.update_note(db, note, body.model_dump(exclude_unset=True))
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return svc.note_dict(note)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db_dependency)):
    try:
        svc.delete_note(db, svc.get_note(db, note_id))
    except (ValueError, LookupError) as exc:
        raise _http(exc)


# ── Completions ──────────────────────────────────────────────────────────────


@router.post("/tasks/{task_id}/complete", response_model=MondayReviewCompletionRead)
def complete_task(
    task_id: int,
    body: MondayReviewCompleteRequest,
    db: Session = Depends(get_db_dependency),
):
    """Check a task off for a week (default: the current week). Calling again
    updates who/notes on the existing completion."""
    try:
        task = svc.get_task(db, task_id)
        data = body.model_dump(exclude_unset=True)
        row, _created = svc.complete_task(
            db,
            task,
            week_of=body.week_of,
            completed_by=data.get("completed_by"),
            notes=data.get("notes"),
        )
    except (ValueError, LookupError) as exc:
        raise _http(exc)
    return svc.completion_dict(row)


@router.delete("/tasks/{task_id}/complete", status_code=204)
def uncomplete_task(
    task_id: int,
    week_of: Optional[date] = Query(None),
    db: Session = Depends(get_db_dependency),
):
    try:
        task = svc.get_task(db, task_id)
        if not svc.uncomplete_task(db, task, week_of=week_of):
            raise LookupError("task is not completed for that week")
    except (ValueError, LookupError) as exc:
        raise _http(exc)
