"""Best-effort line-level matching of trade records to ledger events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from api.audit_ledger import LedgerLine
from api.audit_reconcile import TradeLineForMatch
from api.club_audit_timezone import zone_for_slug

MATCH_WINDOW = timedelta(minutes=15)
_WHOLE = Decimal("1")


@dataclass(frozen=True)
class MatchedTradeRow:
    trade: TradeLineForMatch
    match_text: str
    variant: str


def round_whole_usd(amount: Decimal) -> Decimal:
    return abs(amount).quantize(_WHOLE, rounding=ROUND_HALF_UP)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _signs_compatible(trade_amount: Decimal, ledger: LedgerLine) -> bool:
    """Trade negative ↔ club outflows; trade positive ↔ cashout."""
    if trade_amount < 0:
        return ledger.amount_signed < 0
    if trade_amount > 0:
        return ledger.amount_signed > 0
    return False


def _format_match_time(club_slug: str, occurred_at: datetime | None) -> str:
    if occurred_at is None:
        return ""
    dt = _as_utc(occurred_at)
    assert dt is not None
    local = dt.astimezone(zone_for_slug(club_slug))
    return local.strftime("%Y-%m-%d %H:%M")


def _match_label(line: LedgerLine) -> str:
    for candidate in (
        line.display_name,
        line.member_nickname,
        line.gg_player_id,
    ):
        text = (candidate or "").strip()
        if text:
            return text
    return ""


def format_match_text(club_slug: str, line: LedgerLine) -> str:
    label = _match_label(line)
    source = (line.source_label or line.source or "").strip()
    time_label = _format_match_time(club_slug, line.occurred_at_utc)
    dollars = int(round_whole_usd(line.amount_signed))
    parts = [p for p in (label, source, time_label, f"${dollars}") if p]
    return ", ".join(parts)


def _candidate_score(
    trade: TradeLineForMatch,
    ledger: LedgerLine,
) -> tuple[int, timedelta] | None:
    trade_at = _as_utc(trade.occurred_at)
    ledger_at = _as_utc(ledger.occurred_at_utc)
    if trade_at is None or ledger_at is None:
        return None
    if not _signs_compatible(trade.amount, ledger):
        return None
    if round_whole_usd(trade.amount) != round_whole_usd(ledger.amount_signed):
        return None
    delta = abs(trade_at - ledger_at)
    if delta > MATCH_WINDOW:
        return None
    trade_gid = (trade.member_gg_player_id or "").strip()
    ledger_gid = (ledger.gg_player_id or "").strip()
    same_player = 0 if (trade_gid and ledger_gid and trade_gid == ledger_gid) else 1
    return (same_player, delta)


def match_trade_lines_to_ledger(
    trade_lines: list[TradeLineForMatch],
    ledger_lines: list[LedgerLine],
    *,
    club_slug: str,
) -> list[MatchedTradeRow]:
    """Greedy chronological matching; each ledger line used at most once."""
    available = list(enumerate(ledger_lines))
    used: set[int] = set()
    rows: list[MatchedTradeRow] = []

    for trade in trade_lines:
        best_idx: int | None = None
        best_score: tuple[int, timedelta] | None = None
        for idx, ledger in available:
            if idx in used:
                continue
            score = _candidate_score(trade, ledger)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx

        if best_idx is None:
            rows.append(MatchedTradeRow(trade=trade, match_text="", variant=""))
            continue

        used.add(best_idx)
        ledger = ledger_lines[best_idx]
        variant = ""
        if ledger.source == "bonus":
            variant = (ledger.variant or "").strip()
        rows.append(
            MatchedTradeRow(
                trade=trade,
                match_text=format_match_text(club_slug, ledger),
                variant=variant,
            )
        )

    return rows
