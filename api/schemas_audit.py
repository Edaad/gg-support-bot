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


class LedgerBreakdownSchema(BaseModel):
    deposits: str = "0"
    early_rb: str = "0"
    bonuses: str = "0"
    monday: str = "0"
    cashouts: str = "0"


class LedgerLineSchema(BaseModel):
    gg_player_id: Optional[str] = None
    member_nickname: Optional[str] = None
    source: str
    source_label: str
    amount_signed: str
    occurred_at: Optional[str] = None
    external_id: str
    detail: Optional[str] = None


class AuditReconcilePlayerResultSchema(BaseModel):
    gg_player_id: str
    member_nickname: Optional[str] = None
    net_trade_record: str
    net_ledger: str
    delta: str
    ledger_breakdown: LedgerBreakdownSchema
    status: str


class UnmatchedTradeRowSchema(BaseModel):
    line_id: int
    amount: str
    member_nickname: Optional[str] = None
    sheet_row: int


class UnmatchedLedgerEventSchema(BaseModel):
    source: str
    amount_usd: str
    external_id: str
    detail: Optional[str] = None


class AuditReconcileReportSchema(BaseModel):
    audit_date: date
    club_slug: str
    club_name: str
    status: str
    run_id: Optional[int] = None
    trade_upload_id: Optional[int] = None
    trade_upload_ids: List[int] = Field(default_factory=list)
    early_rb_snapshot_id: Optional[int] = None
    players: List[AuditReconcilePlayerResultSchema] = Field(default_factory=list)
    unmatched_trade: List[UnmatchedTradeRowSchema] = Field(default_factory=list)
    unmatched_ledger: List[UnmatchedLedgerEventSchema] = Field(default_factory=list)
    ledger_lines: List[LedgerLineSchema] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blocked_reason: Optional[str] = None
    players_matched: int = 0
    players_failed: int = 0
    unmatched_trade_count: int = 0
    unmatched_ledger_count: int = 0


class AuditReconcileRunSummary(BaseModel):
    id: int
    club_slug: str
    club_name: str
    audit_date: date
    status: str
    players_matched: int
    players_failed: int
    unmatched_trade_count: int
    unmatched_ledger_count: int
    created_at: datetime
