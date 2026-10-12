"""Pydantic shapes for the Monday Review router."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class MondayReviewLink(BaseModel):
    label: str = ""
    url: str


class MondayReviewTaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    links: List[MondayReviewLink] = []
    assignees: List[str] = []


class MondayReviewTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    links: Optional[List[MondayReviewLink]] = None
    assignees: Optional[List[str]] = None
    archived: Optional[bool] = None


class MondayReviewReorder(BaseModel):
    ordered_ids: List[int]


class MondayReviewTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    links: List[MondayReviewLink]
    assignees: List[str]
    sort_order: int
    archived: bool
    archived_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MondayReviewNoteCreate(BaseModel):
    body: str
    kind: Literal["persistent", "temporary"] = "temporary"
    week_of: Optional[date] = None


class MondayReviewNoteUpdate(BaseModel):
    body: Optional[str] = None
    kind: Optional[Literal["persistent", "temporary"]] = None
    week_of: Optional[date] = None


class MondayReviewNoteRead(BaseModel):
    id: int
    task_id: int
    kind: str
    body: str
    week_of: Optional[date] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MondayReviewCompleteRequest(BaseModel):
    week_of: Optional[date] = None
    completed_by: Optional[str] = None
    notes: Optional[str] = None


class MondayReviewCompletionRead(BaseModel):
    id: int
    task_id: int
    week_of: date
    completed_by: Optional[str] = None
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MondayReviewBoardTask(MondayReviewTaskRead):
    persistent_notes: List[MondayReviewNoteRead]
    week_notes: List[MondayReviewNoteRead]
    upcoming_notes: List[MondayReviewNoteRead]
    completion: Optional[MondayReviewCompletionRead] = None
    done: bool


class MondayReviewBoard(BaseModel):
    week_of: date
    week_end: date
    is_current_week: bool
    is_past_week: bool
    is_future_week: bool
    current_week_of: date
    next_week_of: date
    today_et: date
    tasks: List[MondayReviewBoardTask]
    total: int
    done: int
    pending: int
    all_done: bool


class MondayReviewHistoryTask(BaseModel):
    task_id: int
    title: str
    assignees: List[str]
    archived: bool
    status: Literal["done", "missed"]
    completion: Optional[MondayReviewCompletionRead] = None
    week_notes: List[MondayReviewNoteRead]


class MondayReviewHistoryWeek(BaseModel):
    week_of: date
    week_end: date
    is_current_week: bool
    is_past_week: bool
    is_future_week: bool
    tasks: List[MondayReviewHistoryTask]
    total: int
    done: int
    missed: int


class MondayReviewHistory(BaseModel):
    weeks: List[MondayReviewHistoryWeek]
    has_more: bool
    next_before: Optional[date] = None


class MondayReviewMeta(BaseModel):
    assignee_presets: List[str]
    current_week_of: date
    next_week_of: date
    today_et: date
