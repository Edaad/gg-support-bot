"""Parse ClubGG trade record XLSX (Trade Record tab) for audit ingest."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO, Literal
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from api.club_slug import CLUB_LABEL_TO_SLUG, CLUB_SLUG_TO_NAME

SHEET_NAME = "Trade Record"
DATA_START_ROW = 6
_EASTERN = ZoneInfo("America/New_York")
_GG_ID_RE = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")

# Column indices (1-based) — validated against Aces-21.xlsx layout (KAN-6)
COL_TIME = 2  # B
COL_AMOUNT = 6  # F
COL_SA_ID = 11  # K
COL_SA_NICK = 12  # L
COL_AGENT_ID = 13  # M
COL_AGENT_NICK = 14  # N
COL_MEMBER_ID = 15  # O
COL_MEMBER_NICK = 16  # P

Role = Literal["member", "agent", "superAgent"]

ROLE_COLUMNS: dict[Role, tuple[int, int]] = {
    "superAgent": (COL_SA_ID, COL_SA_NICK),
    "agent": (COL_AGENT_ID, COL_AGENT_NICK),
    "member": (COL_MEMBER_ID, COL_MEMBER_NICK),
}


@dataclass(frozen=True)
class TradeRecordMetadata:
    club_text: str
    date_text: str


@dataclass(frozen=True)
class ParsedIdentity:
    role: Role
    gg_player_id: str
    nickname: str


@dataclass(frozen=True)
class ParsedTransaction:
    sheet_row: int
    occurred_at: datetime | None
    amount: Decimal
    member_gg_player_id: str | None
    member_nickname: str | None
    agent_gg_player_id: str | None
    super_agent_gg_player_id: str | None


@dataclass
class TradeRecordParseResult:
    metadata: TradeRecordMetadata
    identities: list[ParsedIdentity] = field(default_factory=list)
    transactions: list[ParsedTransaction] = field(default_factory=list)
    skipped_rows: list[str] = field(default_factory=list)


class TradeRecordParseError(ValueError):
    pass


class TradeRecordValidationError(ValueError):
    pass


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value).strip()


def _normalize_gg_id(raw: Any) -> str | None:
    s = _cell_str(raw)
    if not s or s == "-":
        return None
    if _GG_ID_RE.match(s):
        return s
    return None


def _parse_amount(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    s = _cell_str(raw).replace(",", "")
    if not s or s == "-":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_row_datetime(raw: Any, audit_date: date) -> datetime | None:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            return dt.replace(tzinfo=_EASTERN)
        return dt.astimezone(_EASTERN)
    s = _cell_str(raw)
    if not s:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ):
        try:
            parsed = datetime.strptime(s[:19] if " " in s else s, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_EASTERN)
            return parsed
        except ValueError:
            continue
    # Time-only on audit day
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt).time()
            return datetime(
                audit_date.year,
                audit_date.month,
                audit_date.day,
                t.hour,
                t.minute,
                t.second,
                tzinfo=_EASTERN,
            )
        except ValueError:
            continue
    return None


def _metadata_from_sheet(ws) -> TradeRecordMetadata:
    club_text = ""
    date_text = ""
    fallback_parts: list[str] = []

    for row in range(1, 4):
        label = _cell_str(ws.cell(row=row, column=1).value).lower()
        value = _cell_str(ws.cell(row=row, column=2).value)
        if value:
            fallback_parts.append(value)
        if "club" in label and value:
            club_text = value
        elif "date" in label and value:
            date_text = value

    if not club_text and fallback_parts:
        club_text = fallback_parts[0]
    if not date_text and len(fallback_parts) > 1:
        date_text = fallback_parts[1]
    elif not date_text and fallback_parts:
        date_text = fallback_parts[-1]

    if not club_text:
        raise TradeRecordParseError("Trade Record metadata rows 1–3 are empty")
    if not date_text:
        date_text = club_text

    return TradeRecordMetadata(club_text=club_text, date_text=date_text)


# Display labels for trade-record metadata validation (match dashboard clubMap.ts)
CLUB_SLUG_DISPLAY_LABELS: dict[str, str] = {
    "clubgto": "ClubGTO",
    "round-table": "Round Table",
    "aces-table": "Aces Table",
    "creator-club": "Creator Club",
}


def _club_labels_for_slug(club_slug: str) -> set[str]:
    slug = club_slug.strip().lower()
    labels: set[str] = {slug}
    display = CLUB_SLUG_DISPLAY_LABELS.get(slug) or CLUB_SLUG_TO_NAME.get(slug)
    if display:
        labels.add(display)
        labels.add(display.lower())
    full = CLUB_SLUG_TO_NAME.get(slug)
    if full:
        labels.add(full)
        labels.add(full.lower())
    for label, mapped in CLUB_LABEL_TO_SLUG.items():
        if mapped == slug:
            labels.add(label)
            labels.add(label.title())
    if slug == "aces-table":
        labels.update({"aces", "Aces"})
    return {x.strip().lower() for x in labels if x.strip()}


def _metadata_matches_club(metadata: TradeRecordMetadata, club_slug: str) -> bool:
    hay = metadata.club_text.lower()
    labels = _club_labels_for_slug(club_slug)
    return any(label in hay for label in labels)


def _parse_metadata_date(text: str) -> date | None:
    raw = text.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw[:10] if fmt == "%Y-%m-%d" else raw, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def _metadata_matches_date(metadata: TradeRecordMetadata, audit_date: date) -> bool:
    combined = f"{metadata.club_text} {metadata.date_text}"
    parsed = _parse_metadata_date(metadata.date_text) or _parse_metadata_date(combined)
    if parsed:
        return parsed == audit_date
    # Filename-style fallback: day number in metadata
    return str(audit_date.day) in combined and str(audit_date.year) in combined


def validate_metadata(
    metadata: TradeRecordMetadata,
    *,
    club_slug: str,
    audit_date: date,
) -> None:
    if not _metadata_matches_club(metadata, club_slug):
        expected = CLUB_SLUG_DISPLAY_LABELS.get(
            club_slug.strip().lower(),
            CLUB_SLUG_TO_NAME.get(club_slug.strip().lower(), club_slug),
        )
        raise TradeRecordValidationError(
            f"File club metadata {metadata.club_text!r} does not match selected club {expected!r}."
        )
    if not _metadata_matches_date(metadata, audit_date):
        raise TradeRecordValidationError(
            f"File date metadata {metadata.date_text!r} does not match selected date {audit_date.isoformat()}."
        )


def _row_is_blank(ws, row: int) -> bool:
    member_id = _cell_str(ws.cell(row=row, column=COL_MEMBER_ID).value)
    amount = ws.cell(row=row, column=COL_AMOUNT).value
    time_val = ws.cell(row=row, column=COL_TIME).value
    return not member_id and amount in (None, "") and time_val in (None, "")


def parse_trade_record_workbook(
    source: BinaryIO | bytes,
    *,
    audit_date: date,
) -> TradeRecordParseResult:
    if isinstance(source, bytes):
        stream: BinaryIO = io.BytesIO(source)
    else:
        stream = source
        stream.seek(0)

    wb = load_workbook(stream, read_only=True, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        wb.close()
        raise TradeRecordParseError(f'Missing "{SHEET_NAME}" sheet')

    ws = wb[SHEET_NAME]
    metadata = _metadata_from_sheet(ws)

    identities_by_key: dict[tuple[Role, str], ParsedIdentity] = {}
    transactions: list[ParsedTransaction] = []
    skipped_rows: list[str] = []

    row = DATA_START_ROW
    while row <= (ws.max_row or DATA_START_ROW):
        if _row_is_blank(ws, row):
            break

        member_id = _normalize_gg_id(ws.cell(row=row, column=COL_MEMBER_ID).value)
        member_nick = _cell_str(ws.cell(row=row, column=COL_MEMBER_NICK).value)
        agent_id = _normalize_gg_id(ws.cell(row=row, column=COL_AGENT_ID).value)
        sa_id = _normalize_gg_id(ws.cell(row=row, column=COL_SA_ID).value)

        amount = _parse_amount(ws.cell(row=row, column=COL_AMOUNT).value)
        if amount is None:
            skipped_rows.append(f"row {row}: missing or invalid amount")
            row += 1
            continue

        occurred_at = _parse_row_datetime(
            ws.cell(row=row, column=COL_TIME).value,
            audit_date,
        )

        for role, (id_col, nick_col) in ROLE_COLUMNS.items():
            gid = _normalize_gg_id(ws.cell(row=row, column=id_col).value)
            nick = _cell_str(ws.cell(row=row, column=nick_col).value)
            if not gid:
                continue
            if not nick:
                nick = gid
            key = (role, gid)
            identities_by_key[key] = ParsedIdentity(
                role=role,
                gg_player_id=gid,
                nickname=nick,
            )

        transactions.append(
            ParsedTransaction(
                sheet_row=row,
                occurred_at=occurred_at,
                amount=amount,
                member_gg_player_id=member_id,
                member_nickname=member_nick or None,
                agent_gg_player_id=agent_id,
                super_agent_gg_player_id=sa_id,
            )
        )
        row += 1

    wb.close()

    if not transactions:
        raise TradeRecordParseError("No transaction rows found from row 6")

    return TradeRecordParseResult(
        metadata=metadata,
        identities=list(identities_by_key.values()),
        transactions=transactions,
        skipped_rows=skipped_rows,
    )
