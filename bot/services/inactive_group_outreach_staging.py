"""Manual staging for inactive group outreach (phase 1 — no DMs or entity resolution)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from club_gc_settings import (
    INACTIVE_OUTREACH_CLUB_KEYS,
    get_club_gc_config_by_link_club_id,
)
from bot.services.club import get_club_for_chat
from bot.services.player_details import (
    gg_player_id_from_title,
    parse_tracking_title,
    resolve_club_id_from_shorthand,
)
from db.connection import get_db
from db.models import InactiveGroupOutreachRow

STAGE_STATUS_STAGED = "staged"
STAGE_STATUS_UNSTAGED = "unstaged"


@dataclass(frozen=True)
class StageResult:
    ok: bool
    error: str | None = None
    row_id: int | None = None
    club_key: str | None = None
    telegram_chat_id: int | None = None
    group_title: str | None = None
    already_staged: bool = False
    has_scan_data: bool = False
    inactive_90d: bool | None = None
    inactive_180d: bool | None = None


@dataclass(frozen=True)
class StagedGroupSummary:
    id: int
    club_key: str
    telegram_chat_id: int
    group_title: str
    staged_at: datetime | None
    has_scan_data: bool
    inactive_90d: bool
    inactive_180d: bool
    stage_note: str | None


def is_megagroup_chat_id(chat_id: int) -> bool:
    cid = int(chat_id)
    return cid < 0 and str(cid).startswith("-100")


def _club_key_from_link_club_id(club_id: int) -> str | None:
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg is None:
        return None
    club_key = str(cfg.club_key)
    if club_key not in INACTIVE_OUTREACH_CLUB_KEYS:
        return None
    return club_key


def resolve_club_key_for_chat(chat_id: int, title: str | None) -> str | None:
    """Resolve outreach club_key from groups link or tracking title shorthand."""

    club_id = get_club_for_chat(int(chat_id))
    if club_id is not None:
        key = _club_key_from_link_club_id(club_id)
        if key:
            return key

    parsed = parse_tracking_title(title)
    if not parsed:
        return None
    shorthand, _ = parsed
    club_id = resolve_club_id_from_shorthand(shorthand)
    if club_id is None:
        return None
    return _club_key_from_link_club_id(club_id)


def _has_scan_data(row: InactiveGroupOutreachRow) -> bool:
    return row.scanned_at is not None


def _row_to_stage_result(
    row: InactiveGroupOutreachRow,
    *,
    already_staged: bool,
) -> StageResult:
    return StageResult(
        ok=True,
        row_id=int(row.id),
        club_key=str(row.club_key),
        telegram_chat_id=int(row.telegram_chat_id),
        group_title=str(row.group_title),
        already_staged=already_staged,
        has_scan_data=_has_scan_data(row),
        inactive_90d=bool(row.inactive_90d) if _has_scan_data(row) else None,
        inactive_180d=bool(row.inactive_180d) if _has_scan_data(row) else None,
    )


def stage_inactive_group(
    *,
    club_key: str,
    telegram_chat_id: int,
    group_title: str,
    staged_by_user_id: int,
    note: str | None = None,
) -> StageResult:
    """Upsert outreach row and mark ``stage_status=staged``."""

    if club_key not in INACTIVE_OUTREACH_CLUB_KEYS:
        return StageResult(ok=False, error=f"Unknown club_key: {club_key!r}.")
    if not is_megagroup_chat_id(telegram_chat_id):
        return StageResult(
            ok=False,
            error="Only megagroup chat ids (-100…) can be staged.",
        )

    title = (group_title or "").strip() or "(untitled)"
    gg_player_id = gg_player_id_from_title(title)
    now = datetime.now(timezone.utc)
    trimmed_note = (note or "").strip() or None

    with get_db() as session:
        row = (
            session.query(InactiveGroupOutreachRow)
            .filter_by(club_key=club_key, telegram_chat_id=int(telegram_chat_id))
            .first()
        )
        already_staged = row is not None and row.stage_status == STAGE_STATUS_STAGED

        if row is None:
            row = InactiveGroupOutreachRow(
                club_key=club_key,
                telegram_chat_id=int(telegram_chat_id),
                group_title=title,
                legacy_chat_id=None,
                gg_player_id=gg_player_id,
                scan_status="pending",
            )
            session.add(row)
        else:
            row.group_title = title
            if gg_player_id and not row.gg_player_id:
                row.gg_player_id = gg_player_id

        row.stage_status = STAGE_STATUS_STAGED
        row.staged_at = now
        row.staged_by_telegram_user_id = int(staged_by_user_id)
        row.stage_note = trimmed_note
        session.commit()
        session.refresh(row)
        return _row_to_stage_result(row, already_staged=already_staged)


def stage_inactive_group_by_row_id(
    *,
    row_id: int,
    staged_by_user_id: int,
    note: str | None = None,
) -> StageResult:
    with get_db() as session:
        row = session.get(InactiveGroupOutreachRow, int(row_id))
        if row is None:
            return StageResult(ok=False, error=f"No outreach row id={row_id}.")

    return stage_inactive_group(
        club_key=str(row.club_key),
        telegram_chat_id=int(row.telegram_chat_id),
        group_title=str(row.group_title),
        staged_by_user_id=staged_by_user_id,
        note=note,
    )


def lookup_outreach_row_title(telegram_chat_id: int) -> str | None:
    with get_db() as session:
        row = (
            session.query(InactiveGroupOutreachRow)
            .filter_by(telegram_chat_id=int(telegram_chat_id))
            .order_by(InactiveGroupOutreachRow.id.desc())
            .first()
        )
        if row is None:
            return None
        return str(row.group_title)


def unstage_inactive_group(
    *,
    club_key: str | None = None,
    telegram_chat_id: int | None = None,
    row_id: int | None = None,
) -> StageResult:
    """Remove a group from the staged queue without deleting the audit row."""

    with get_db() as session:
        if row_id is not None:
            row = session.get(InactiveGroupOutreachRow, int(row_id))
        elif club_key is not None and telegram_chat_id is not None:
            row = (
                session.query(InactiveGroupOutreachRow)
                .filter_by(
                    club_key=club_key,
                    telegram_chat_id=int(telegram_chat_id),
                )
                .first()
            )
        else:
            return StageResult(ok=False, error="Provide row_id or club_key + chat_id.")

        if row is None:
            return StageResult(ok=False, error="Outreach row not found.")
        if row.stage_status != STAGE_STATUS_STAGED:
            return StageResult(
                ok=False,
                error="Row is not staged.",
                row_id=int(row.id),
                club_key=str(row.club_key),
                telegram_chat_id=int(row.telegram_chat_id),
                group_title=str(row.group_title),
            )

        row.stage_status = STAGE_STATUS_UNSTAGED
        row.staged_at = None
        row.staged_by_telegram_user_id = None
        session.commit()
        return StageResult(
            ok=True,
            row_id=int(row.id),
            club_key=str(row.club_key),
            telegram_chat_id=int(row.telegram_chat_id),
            group_title=str(row.group_title),
        )


def list_staged_groups(
    *,
    club_key: str | None = None,
    limit: int = 50,
) -> list[StagedGroupSummary]:
    capped = max(1, min(int(limit), 200))
    with get_db() as session:
        query = session.query(InactiveGroupOutreachRow).filter(
            InactiveGroupOutreachRow.stage_status == STAGE_STATUS_STAGED
        )
        if club_key:
            query = query.filter(InactiveGroupOutreachRow.club_key == club_key)
        rows = (
            query.order_by(
                InactiveGroupOutreachRow.staged_at.asc().nullsfirst(),
                InactiveGroupOutreachRow.id.asc(),
            )
            .limit(capped)
            .all()
        )
        return [
            StagedGroupSummary(
                id=int(row.id),
                club_key=str(row.club_key),
                telegram_chat_id=int(row.telegram_chat_id),
                group_title=str(row.group_title),
                staged_at=row.staged_at,
                has_scan_data=_has_scan_data(row),
                inactive_90d=bool(row.inactive_90d),
                inactive_180d=bool(row.inactive_180d),
                stage_note=row.stage_note,
            )
            for row in rows
        ]


def format_stage_success_message(result: StageResult) -> str:
    if not result.ok or result.row_id is None:
        return result.error or "Staging failed."

    prefix = "Already staged" if result.already_staged else "Staged"
    scan_line = (
        f"inactive_90d={result.inactive_90d}, inactive_180d={result.inactive_180d}"
        if result.has_scan_data
        else "scan pending — activity flags not loaded yet"
    )
    return (
        f"{prefix} (row {result.row_id})\n"
        f"{result.group_title}\n"
        f"chat_id={result.telegram_chat_id} club={result.club_key}\n"
        f"{scan_line}"
    )


def format_staged_list_message(rows: list[StagedGroupSummary], *, club_key: str | None) -> str:
    if not rows:
        scope = f" for {club_key}" if club_key else ""
        return f"No staged inactive groups{scope}."

    lines = [f"Staged inactive groups ({len(rows)}):"]
    for row in rows:
        scan = (
            f"90d={row.inactive_90d} 180d={row.inactive_180d}"
            if row.has_scan_data
            else "scan pending"
        )
        lines.append(
            f"• {row.id}: {row.group_title}\n"
            f"  {row.telegram_chat_id} ({row.club_key}) — {scan}"
        )
    return "\n".join(lines)
