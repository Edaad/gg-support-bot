"""Response models for GET /api/kpis (see api/kpis.py for the counting rules)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel

# Rates are fractions (0.45 = 45%); None when the denominator is zero.


class KpiPeriod(BaseModel):
    start: date
    end: date
    label: str
    days: int
    is_partial: bool
    tickets_pending: bool


class KpiRailAmount(BaseModel):
    rail: str
    label: str
    count: int
    amount_usd: float


class KpiDeposits(BaseModel):
    count: int
    amount_usd: float
    avg_usd: Optional[float] = None
    by_rail: list[KpiRailAmount]


class KpiNewPlayers(BaseModel):
    joined: int
    converted: int
    conversion_rate: Optional[float] = None
    first_deposits: int


class KpiFrtBucket(BaseModel):
    key: str
    label: str
    count: int


class KpiSlowTicket(BaseModel):
    ticket_id: int
    activity_date: date
    chat_id: int
    club_id: Optional[int] = None
    group_title: Optional[str] = None
    category: str
    frt_seconds: int
    brief_summary: Optional[str] = None


class KpiResponseTime(BaseModel):
    tickets: int
    human_answered: int
    no_human_reply: int
    median_seconds: Optional[float] = None
    p90_seconds: Optional[float] = None
    over_5m: int
    pct_over_5m: Optional[float] = None
    distribution: list[KpiFrtBucket]
    slowest: list[KpiSlowTicket]


class KpiCategoryCount(BaseModel):
    category: str
    label: str
    count: int


class KpiTickets(BaseModel):
    total: int
    by_category: list[KpiCategoryCount]


class KpiFunnelStep(BaseModel):
    step: str
    label: str
    count: int
    rate: Optional[float] = None


class KpiDepositFunnel(BaseModel):
    started: int
    confirmed: int
    rate: Optional[float] = None
    first_started: int
    first_confirmed: int
    first_rate: Optional[float] = None
    steps: list[KpiFunnelStep]


class KpiAutomationRow(BaseModel):
    source: str
    label: str
    attempted: int
    completed: int
    failed: int
    stuck: int
    stale: int
    in_progress: int
    rate: Optional[float] = None


class KpiAutomation(BaseModel):
    overall: KpiAutomationRow
    by_source: list[KpiAutomationRow]


class KpiSnapshot(BaseModel):
    deposits: KpiDeposits
    new_players: KpiNewPlayers
    response_time: KpiResponseTime
    tickets: KpiTickets
    deposit_funnel: KpiDepositFunnel
    automation: KpiAutomation


class KpiHeadline(BaseModel):
    deposits_count: int
    deposits_usd: float
    joined: int
    converted: int
    conversion_rate: Optional[float] = None
    first_deposits: int
    tickets: int
    median_frt_seconds: Optional[float] = None
    pct_over_5m: Optional[float] = None
    funnel_started: int
    funnel_rate: Optional[float] = None
    automation_attempted: int
    automation_rate: Optional[float] = None


class KpiTrendPoint(KpiPeriod, KpiHeadline):
    pass


class KpiClubHeadline(KpiHeadline):
    club_id: int
    club_name: str


class KpiFreshness(BaseModel):
    generated_at: datetime
    today: date
    tickets_complete_through: Optional[date] = None
    stuck_after_minutes: int


class KpiResponse(BaseModel):
    granularity: str
    club_id: Optional[int] = None
    club_name: Optional[str] = None
    period: KpiPeriod
    previous: KpiPeriod
    previous_truncated: bool
    current: KpiSnapshot
    prior: KpiSnapshot
    trend: list[KpiTrendPoint]
    by_club: list[KpiClubHeadline]
    freshness: KpiFreshness
