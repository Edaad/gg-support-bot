"""Pydantic schemas for audit trade record upload."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TradeRecordUploadReport(BaseModel):
    upload_id: int
    club_slug: str
    club_name: str
    audit_date: date
    filename: str
    replaced_previous: bool = False
    transaction_rows_parsed: int
    identities_extracted: int
    postgres_inserted: int
    postgres_updated: int
    gg_computer_upserted: int = 0
    gg_computer_modified: int = 0
    gg_computer_skipped: int = 0
    gg_computer_error: Optional[str] = None
    skipped_rows: List[str] = Field(default_factory=list)


class TradeRecordUploadSummary(BaseModel):
    id: int
    club_slug: str
    club_name: str
    audit_date: date
    filename: str
    transaction_count: int
    created_at: datetime
