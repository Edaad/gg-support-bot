"""Best-effort line-level matching of trade records to ledger events."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from api.audit_ledger import LedgerLine
from api.audit_reconcile import TradeLineForMatch
from api.club_audit_timezone import zone_for_payment_display
from api.vaughn_methods import is_vaughn_method, matching_source_label
from bot.services.player_details import parse_group_title_parts

MATCH_WINDOW = timedelta(minutes=15)
CHIP_TRANSFER_WINDOW = timedelta(minutes=10)
CHIP_TRANSFER_PLAYER_LABEL = "Chip Transfer (Player)"
CHIP_TRANSFER_RT_AT_LABEL = "Chip Transfer (RT↔AT)"
CHIP_TRANSFER_AT_CC_LABEL = "Chip Transfer (AT↔CC)"
# Whole-dollar slack after round_whole_usd (e.g. early RB $18.60 ↔ ClubGG $18).
MATCH_AMOUNT_TOLERANCE_USD = Decimal("1")
_WHOLE = Decimal("1")
_RT_SLUG = "round-table"
_AT_SLUG = "aces-table"
_CC_SLUG = "creator-club"
_RT_AT_SLUGS = frozenset({_RT_SLUG, _AT_SLUG})
_AT_CC_SLUGS = frozenset({_AT_SLUG, _CC_SLUG})
_CLUB_DISPLAY = {
    _RT_SLUG: "Round Table",
    _AT_SLUG: "Aces Table",
    _CC_SLUG: "Creator Club",
}


@dataclass(frozen=True)
class MatchedTradeRow:
    trade: TradeLineForMatch
    match_name: str
    match_source: str
    match_time: str
    match_amount: Decimal | None
    variant: str
    vaughn_method: bool = False
    match_occurred_at: datetime | None = None


@dataclass(frozen=True)
class TradeLedgerMatchResult:
    rows: list[MatchedTradeRow]
    unmatched_ledger: list[LedgerLine]


def round_whole_usd(amount: Decimal) -> Decimal:
    return abs(amount).quantize(_WHOLE, rounding=ROUND_HALF_UP)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sort_key_occurred_at(occurred_at: datetime | None) -> datetime:
    """UTC-aware sort key so naive/aware ledger times can be compared."""
    return _as_utc(occurred_at) or datetime.max.replace(tzinfo=timezone.utc)


def _signs_compatible(trade_amount: Decimal, ledger: LedgerLine) -> bool:
    """Trade negative ↔ club outflows; trade positive ↔ cashout."""
    if trade_amount < 0:
        return ledger.amount_signed < 0
    if trade_amount > 0:
        return ledger.amount_signed > 0
    return False


def _format_match_time(club_slug: str, occurred_at: datetime | None) -> str:
    del club_slug  # display is always America/New_York
    if occurred_at is None:
        return ""
    dt = _as_utc(occurred_at)
    assert dt is not None
    local = dt.astimezone(zone_for_payment_display())
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


def _match_fields(
    club_slug: str,
    line: LedgerLine,
) -> tuple[str, str, str, Decimal]:
    name = _match_label(line)
    source = matching_source_label(
        source=line.source,
        variant=line.variant,
        club_slug=club_slug,
        source_label=line.source_label,
        memo=line.memo,
    )
    time_label = _format_match_time(club_slug, line.occurred_at_utc)
    dollars = round_whole_usd(line.amount_signed)
    return name, source, time_label, dollars


def _empty_match_row(trade: TradeLineForMatch) -> MatchedTradeRow:
    return MatchedTradeRow(
        trade=trade,
        match_name="",
        match_source="",
        match_time="",
        match_amount=None,
        variant="",
        vaughn_method=False,
        match_occurred_at=None,
    )


def _trade_slug(trade: TradeLineForMatch) -> str:
    return (trade.trade_club_slug or "").strip().lower()


def _trade_player_id(trade: TradeLineForMatch) -> str:
    return (trade.member_gg_player_id or "").strip()


def _player_display_name(trade: TradeLineForMatch) -> str:
    nick = (trade.member_nickname or "").strip()
    return nick or _trade_player_id(trade)


def _is_unmatched_row(row: MatchedTradeRow) -> bool:
    return not (row.match_source or "").strip()


def _chip_transfer_eligible_base(
    left: TradeLineForMatch,
    right: TradeLineForMatch,
) -> timedelta | None:
    left_at = _as_utc(left.occurred_at)
    right_at = _as_utc(right.occurred_at)
    if left_at is None or right_at is None:
        return None
    if left.amount == 0 or right.amount == 0:
        return None
    if left.amount * right.amount >= 0:
        return None
    if abs(left.amount) != abs(right.amount):
        return None
    delta = abs(left_at - right_at)
    if delta > CHIP_TRANSFER_WINDOW:
        return None
    return delta


def _is_inter_player_pair(left: TradeLineForMatch, right: TradeLineForMatch) -> bool:
    left_id = _trade_player_id(left)
    right_id = _trade_player_id(right)
    if not left_id or not right_id or left_id == right_id:
        return False
    return _trade_slug(left) == _trade_slug(right)


def _is_inter_club_pair(
    left: TradeLineForMatch,
    right: TradeLineForMatch,
    slugs: frozenset[str],
) -> bool:
    left_id = _trade_player_id(left)
    right_id = _trade_player_id(right)
    if not left_id or left_id != right_id:
        return False
    return {_trade_slug(left), _trade_slug(right)} == slugs


def _is_rt_at_pair(left: TradeLineForMatch, right: TradeLineForMatch) -> bool:
    return _is_inter_club_pair(left, right, _RT_AT_SLUGS)


def _is_at_cc_pair(left: TradeLineForMatch, right: TradeLineForMatch) -> bool:
    return _is_inter_club_pair(left, right, _AT_CC_SLUGS)


def _fill_transfer_pair(
    rows: list[MatchedTradeRow],
    i: int,
    j: int,
    *,
    source: str,
    name_i: str,
    name_j: str,
) -> None:
    a = rows[i]
    b = rows[j]
    abs_amt = abs(a.trade.amount)
    rows[i] = replace(
        a,
        match_name=name_i,
        match_source=source,
        match_time=_format_match_time("", b.trade.occurred_at),
        match_amount=abs_amt,
        variant="",
        match_occurred_at=b.trade.occurred_at,
    )
    rows[j] = replace(
        b,
        match_name=name_j,
        match_source=source,
        match_time=_format_match_time("", a.trade.occurred_at),
        match_amount=abs_amt,
        variant="",
        match_occurred_at=a.trade.occurred_at,
    )


def _pair_leftovers(
    rows: list[MatchedTradeRow],
    *,
    eligible: object,
    source: str,
    name_for,
) -> None:
    leftover = [idx for idx, row in enumerate(rows) if _is_unmatched_row(row)]
    leftover.sort(key=lambda idx: _sort_key_occurred_at(rows[idx].trade.occurred_at))
    used: set[int] = set()
    for i in leftover:
        if i in used:
            continue
        best_j: int | None = None
        best_delta: timedelta | None = None
        for j in leftover:
            if j == i or j in used:
                continue
            if not eligible(rows[i].trade, rows[j].trade):
                continue
            delta = _chip_transfer_eligible_base(rows[i].trade, rows[j].trade)
            if delta is None:
                continue
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_j = j
        if best_j is None:
            continue
        used.add(i)
        used.add(best_j)
        _fill_transfer_pair(
            rows,
            i,
            best_j,
            source=source,
            name_i=name_for(rows[i].trade, rows[best_j].trade),
            name_j=name_for(rows[best_j].trade, rows[i].trade),
        )


def is_cc_at_group_title(title: str | None) -> bool:
    """True when a support group title carries both CC and AT tokens."""
    parsed = parse_group_title_parts(title)
    if not parsed:
        return False
    return "CC" in parsed.shorthands and "AT" in parsed.shorthands


def apply_cc_at_aces_ledger_fallback(
    rows: list[MatchedTradeRow],
    cc_unmatched_ledger: list[LedgerLine],
) -> tuple[list[MatchedTradeRow], list[LedgerLine]]:
    """Match leftover Aces trades to unmatched Creator Club ledger for CC AT titles.

    Used only in all-clubs Matching, after per-club trade↔ledger matching and
    before chip-transfer pairing. Matched rows stay on the Aces sheet (trade
    upload slug). Returns updated rows and remaining unmatched CC ledger lines.
    """
    out = list(rows)
    eligible = [
        (idx, line)
        for idx, line in enumerate(cc_unmatched_ledger)
        if is_cc_at_group_title(line.detail)
    ]
    if not eligible:
        return out, list(cc_unmatched_ledger)

    used_ledger: set[int] = set()
    at_indices = [
        i
        for i, row in enumerate(out)
        if _is_unmatched_row(row) and _trade_slug(row.trade) == _AT_SLUG
    ]
    at_indices.sort(key=lambda i: _sort_key_occurred_at(out[i].trade.occurred_at))

    for i in at_indices:
        trade = out[i].trade
        best_idx: int | None = None
        best_score: tuple[int, Decimal, timedelta] | None = None
        for ledger_idx, ledger in eligible:
            if ledger_idx in used_ledger:
                continue
            score = _candidate_score(trade, ledger)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_idx = ledger_idx
        if best_idx is None:
            continue
        used_ledger.add(best_idx)
        ledger = cc_unmatched_ledger[best_idx]
        name, source, time_label, dollars = _match_fields(_CC_SLUG, ledger)
        variant = (ledger.variant or "").strip()
        out[i] = replace(
            out[i],
            match_name=name,
            match_source=source,
            match_time=time_label,
            match_amount=dollars,
            variant=variant,
            vaughn_method=is_vaughn_method(
                source=ledger.source,
                variant=variant,
                club_slug=_CC_SLUG,
                memo=ledger.memo,
            ),
            match_occurred_at=ledger.occurred_at_utc,
        )

    remaining = [
        line
        for idx, line in enumerate(cc_unmatched_ledger)
        if idx not in used_ledger
    ]
    return out, remaining


def apply_chip_transfer_matches(
    rows: list[MatchedTradeRow],
) -> list[MatchedTradeRow]:
    """Pair leftover unmatched trades: inter-player, then RT↔AT, then AT↔CC."""
    out = list(rows)

    def player_name(_self: TradeLineForMatch, counterpart: TradeLineForMatch) -> str:
        return _player_display_name(counterpart)

    def club_name(_self: TradeLineForMatch, counterpart: TradeLineForMatch) -> str:
        return _CLUB_DISPLAY.get(_trade_slug(counterpart), _trade_slug(counterpart))

    _pair_leftovers(
        out,
        eligible=_is_inter_player_pair,
        source=CHIP_TRANSFER_PLAYER_LABEL,
        name_for=player_name,
    )
    _pair_leftovers(
        out,
        eligible=_is_rt_at_pair,
        source=CHIP_TRANSFER_RT_AT_LABEL,
        name_for=club_name,
    )
    _pair_leftovers(
        out,
        eligible=_is_at_cc_pair,
        source=CHIP_TRANSFER_AT_CC_LABEL,
        name_for=club_name,
    )
    return out


def _candidate_score(
    trade: TradeLineForMatch,
    ledger: LedgerLine,
) -> tuple[int, Decimal, timedelta] | None:
    trade_at = _as_utc(trade.occurred_at)
    ledger_at = _as_utc(ledger.occurred_at_utc)
    # Monday settlement from gg-computer has no occurred_at; match on amount +
    # player only (payout times differ by club and are not in the ledger).
    skip_time_window = ledger.source == "monday_settlement" and ledger_at is None
    if trade_at is None:
        return None
    if ledger_at is None and not skip_time_window:
        return None
    if not _signs_compatible(trade.amount, ledger):
        return None
    amount_delta = abs(
        round_whole_usd(trade.amount) - round_whole_usd(ledger.amount_signed)
    )
    if amount_delta > MATCH_AMOUNT_TOLERANCE_USD:
        return None
    if skip_time_window:
        delta = timedelta(0)
    else:
        assert ledger_at is not None
        delta = abs(trade_at - ledger_at)
        if delta > MATCH_WINDOW:
            return None
    trade_gid = (trade.member_gg_player_id or "").strip()
    ledger_gid = (ledger.gg_player_id or "").strip()
    if trade_gid and ledger_gid and trade_gid != ledger_gid:
        return None
    same_player = 0 if (trade_gid and ledger_gid) else 1
    # Prefer same player, then closer dollar match, then closer time.
    return (same_player, amount_delta, delta)


def match_trade_lines_to_ledger(
    trade_lines: list[TradeLineForMatch],
    ledger_lines: list[LedgerLine],
    *,
    club_slug: str,
) -> TradeLedgerMatchResult:
    """Greedy chronological matching; each ledger line used at most once."""
    available = list(enumerate(ledger_lines))
    used: set[int] = set()
    rows: list[MatchedTradeRow] = []

    for trade in trade_lines:
        best_idx: int | None = None
        best_score: tuple[int, Decimal, timedelta] | None = None
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
            rows.append(_empty_match_row(trade))
            continue

        used.add(best_idx)
        ledger = ledger_lines[best_idx]
        name, source, time_label, dollars = _match_fields(club_slug, ledger)
        variant = (ledger.variant or "").strip()
        rows.append(
            MatchedTradeRow(
                trade=trade,
                match_name=name,
                match_source=source,
                match_time=time_label,
                match_amount=dollars,
                variant=variant,
                vaughn_method=is_vaughn_method(
                    source=ledger.source,
                    variant=variant,
                    club_slug=club_slug,
                    memo=ledger.memo,
                ),
                match_occurred_at=ledger.occurred_at_utc,
            )
        )

    unmatched = [ledger for idx, ledger in available if idx not in used]
    unmatched.sort(
        key=lambda line: (
            _sort_key_occurred_at(line.occurred_at_utc),
            line.external_id,
        )
    )
    return TradeLedgerMatchResult(rows=rows, unmatched_ledger=unmatched)
