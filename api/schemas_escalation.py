from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EscalationTriggerMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    telegram_message_id: int | None = None
    telegram_user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    text: str | None = None
    has_media: bool | None = None
    media_kind: str | None = None
    message_at: str | None = None


class EscalationEventRead(BaseModel):
    id: int
    created_at: datetime | None = None
    reason: str
    club_id: int | None = None
    telegram_chat_id: int
    group_title: str | None = None
    episode_id: UUID | None = None
    slack_ok: bool
    head_admin_fanout: bool
    method_slug: str | None = None
    trigger_messages: list[dict[str, Any]] = Field(default_factory=list)


class EscalationEventListResponse(BaseModel):
    items: list[EscalationEventRead]
    total: int
    limit: int
    offset: int


class EscalationEpisodeRead(BaseModel):
    id: UUID
    telegram_chat_id: int
    club_id: int | None = None
    group_title: str | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    trigger_messages: list[dict[str, Any]] = Field(default_factory=list)
    events: list[EscalationEventRead] = Field(default_factory=list)
