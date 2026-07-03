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
    audit_timezone_policy: str
    audit_timezone_label: str
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
    warnings: List[str] = Field(default_factory=list)


class TradeRecordUploadSummary(BaseModel):
    id: int
    club_slug: str
    club_name: str
    audit_date: date
    audit_timezone_policy: Optional[str] = None
    audit_timezone_label: Optional[str] = None
    filename: str
    transaction_count: int
    created_at: datetime


class EarlyRakebackClubSyncResult(BaseModel):
    club_slug: str
    club_name: str
    snapshot_id: Optional[int] = None
    lines_fetched: int = 0
    lines_stored: int = 0
    lines_skipped_unmapped: int = 0
    skipped_nicknames: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class EarlyRakebackSyncReport(BaseModel):
    audit_date: date
    clubs_synced: int = 0
    clubs_failed: int = 0
    total_lines_fetched: int = 0
    total_lines_stored: int = 0
    total_lines_skipped_unmapped: int = 0
    clubs: List[EarlyRakebackClubSyncResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class EarlyRakebackSnapshotSummary(BaseModel):
    id: int
    club_slug: str
    club_name: str
    audit_date: date
    lines_fetched: int
    lines_stored: int
    lines_skipped_unmapped: int
    synced_at: datetime
