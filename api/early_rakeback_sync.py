"""Sync early-rakeback data from aon-beta into Postgres snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from api.aon_beta_client import (
    AonBetaConfigError,
    fetch_early_rakeback_archives,
    fetch_early_rakeback_entries,
)
from api.club_audit_timezone import audit_date_for_occurred_at, audit_day_window_utc
from api.club_slug import ALL_GG_COMPUTER_CLUB_SLUGS, CLUB_SLUG_TO_NAME, resolve_club_id
from db.models import EarlyRakebackLine, EarlyRakebackSnapshot

SKIP_REASON_MISSING_GG_PLAYER_ID = "missing_gg_player_id"

SKIP_REASON_LABELS: dict[str, str] = {
    SKIP_REASON_MISSING_GG_PLAYER_ID: "unmapped — no GG player ID",
}


@dataclass
class EarlyRakebackSkip:
    reason: str
    nickname: str = ""
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "nickname": self.nickname,
            "reason": self.reason,
            "count": self.count,
        }

    @property
    def reason_label(self) -> str:
        return SKIP_REASON_LABELS.get(self.reason, self.reason)


@dataclass
class EarlyRakebackClubSyncResult:
    club_slug: str
    club_name: str
    snapshot_id: int | None = None
    lines_fetched: int = 0
    lines_stored: int = 0
    lines_skipped_unmapped: int = 0
    skipped_nicknames: list[str] = field(default_factory=list)
    skips: list[EarlyRakebackSkip] = field(default_factory=list)
    error: str | None = None


@dataclass
class EarlyRakebackSyncReport:
    audit_date: date
    clubs_synced: int = 0
    clubs_failed: int = 0
    total_lines_fetched: int = 0
    total_lines_stored: int = 0
    total_lines_skipped_unmapped: int = 0
    clubs: list[EarlyRakebackClubSyncResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _record_skip(
    skips: list[EarlyRakebackSkip],
    *,
    reason: str,
    nickname: str,
    count: int = 1,
) -> None:
    nick = (nickname or "").strip()
    for skip in skips:
        if skip.reason == reason and skip.nickname == nick:
            skip.count += count
            return
    skips.append(EarlyRakebackSkip(reason=reason, nickname=nick, count=count))


def _merge_skips(
    left: list[EarlyRakebackSkip], right: list[EarlyRakebackSkip]
) -> list[EarlyRakebackSkip]:
    out: list[EarlyRakebackSkip] = [
        EarlyRakebackSkip(reason=s.reason, nickname=s.nickname, count=s.count)
        for s in left
    ]
    for skip in right:
        _record_skip(
            out, reason=skip.reason, nickname=skip.nickname, count=skip.count
        )
    return out


def _skips_to_nicknames(skips: list[EarlyRakebackSkip]) -> list[str]:
    out: list[str] = []
    for skip in skips:
        if skip.nickname and skip.nickname not in out:
            out.append(skip.nickname)
    return out


def _skips_total(skips: list[EarlyRakebackSkip]) -> int:
    return sum(skip.count for skip in skips)


def _serialize_skips(skips: list[EarlyRakebackSkip]) -> str | None:
    if not skips:
        return None
    return json.dumps([skip.to_dict() for skip in skips])


def parse_skipped_nicknames_json(raw: str | None) -> list[EarlyRakebackSkip]:
    """Parse snapshot skipped_nicknames JSON (objects or legacy string list)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    skips: list[EarlyRakebackSkip] = []
    for item in data:
        if isinstance(item, str):
            nick = item.strip()
            if nick:
                _record_skip(
                    skips,
                    reason=SKIP_REASON_MISSING_GG_PLAYER_ID,
                    nickname=nick,
                    count=1,
                )
            continue
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or SKIP_REASON_MISSING_GG_PLAYER_ID).strip()
        nickname = str(item.get("nickname") or "").strip()
        try:
            count = int(item.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        if count < 1:
            count = 1
        _record_skip(skips, reason=reason, nickname=nickname, count=count)
    return skips


def _format_skip_warning(club_name: str, skips: list[EarlyRakebackSkip]) -> str:
    total = _skips_total(skips)
    parts: list[str] = []
    for skip in skips[:10]:
        who = skip.nickname or "(no nickname)"
        parts.append(f"{who} [{skip.reason_label}]×{skip.count}")
    suffix = "…" if len(skips) > 10 else ""
    return (
        f"{club_name}: {total} Early RB record(s) are unmapped (no GG player ID) "
        f"— still included in the export: "
        + ", ".join(parts)
        + suffix
    )


def club_sync_result_to_dict(result: EarlyRakebackClubSyncResult) -> dict[str, Any]:
    return {
        "club_slug": result.club_slug,
        "club_name": result.club_name,
        "snapshot_id": result.snapshot_id,
        "lines_fetched": result.lines_fetched,
        "lines_stored": result.lines_stored,
        "lines_skipped_unmapped": result.lines_skipped_unmapped,
        "skipped_nicknames": result.skipped_nicknames,
        "skips": [
            {
                "nickname": skip.nickname,
                "reason": skip.reason,
                "count": skip.count,
                "reason_label": skip.reason_label,
            }
            for skip in result.skips
        ],
        "error": result.error,
    }


def sync_report_to_dict(report: EarlyRakebackSyncReport) -> dict[str, Any]:
    return {
        "audit_date": report.audit_date,
        "clubs_synced": report.clubs_synced,
        "clubs_failed": report.clubs_failed,
        "total_lines_fetched": report.total_lines_fetched,
        "total_lines_stored": report.total_lines_stored,
        "total_lines_skipped_unmapped": report.total_lines_skipped_unmapped,
        "clubs": [club_sync_result_to_dict(c) for c in report.clubs],
        "warnings": report.warnings,
    }


def _entry_id(entry: dict[str, Any]) -> str:
    raw = entry.get("_id") or entry.get("id")
    return str(raw) if raw is not None else ""


def _record_id(record: dict[str, Any]) -> str:
    raw = record.get("_id") or record.get("id")
    return str(raw) if raw is not None else ""


def _parse_timestamp(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _line_from_record(
    *,
    entry_id: str,
    record_id: str,
    gg_player_id: str,
    member_nickname: str,
    member_type: str,
    record: dict[str, Any],
    occurred_at: datetime | None = None,
) -> dict[str, Any] | None:
    amount = record.get("calculatedAmount")
    if amount is None:
        return None
    ts = (
        occurred_at
        if occurred_at is not None
        else _parse_timestamp(record.get("timestamp"))
    )
    return {
        "source_entry_id": entry_id,
        "source_record_id": record_id,
        "gg_player_id": gg_player_id,
        "member_nickname": member_nickname or None,
        "member_type": member_type or None,
        "amount_usd": Decimal(str(amount)),
        "rake": _decimal_or_none(record.get("rake")),
        "pl": _decimal_or_none(record.get("pl")),
        "rakeback_percentage": _decimal_or_none(record.get("rakebackPercentage")),
        "occurred_at": ts,
    }


def _flatten_entries(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[EarlyRakebackSkip]]:
    """Return (stored lines including unmapped, unmapped skips for warnings)."""
    stored: list[dict[str, Any]] = []
    skips: list[EarlyRakebackSkip] = []

    for entry in entries:
        entry_id = _entry_id(entry)
        gg_player_id = (entry.get("gg_player_id") or "").strip()
        member_nickname = (entry.get("memberNickname") or "").strip()
        member_type = (entry.get("memberType") or "").strip()
        records = entry.get("records") or []
        mapped = bool(gg_player_id)
        before = len(stored)

        for record in records:
            if not isinstance(record, dict):
                continue
            line = _line_from_record(
                entry_id=entry_id,
                record_id=_record_id(record),
                gg_player_id=gg_player_id,
                member_nickname=member_nickname,
                member_type=member_type,
                record=record,
            )
            if line is not None:
                stored.append(line)

        stored_count = len(stored) - before
        if not mapped:
            if stored_count:
                _record_skip(
                    skips,
                    reason=SKIP_REASON_MISSING_GG_PLAYER_ID,
                    nickname=member_nickname,
                    count=stored_count,
                )
            elif not records:
                _record_skip(
                    skips,
                    reason=SKIP_REASON_MISSING_GG_PLAYER_ID,
                    nickname=member_nickname,
                    count=1,
                )

    return stored, skips


def _flatten_archive_entries(
    archives: list[dict[str, Any]],
    from_utc: datetime,
    to_utc: datetime,
) -> tuple[list[dict[str, Any]], list[EarlyRakebackSkip]]:
    """Flatten archived early-RB records whose timestamps fall in [from_utc, to_utc]."""
    stored: list[dict[str, Any]] = []
    skips: list[EarlyRakebackSkip] = []

    for archive in archives:
        archive_id = str(archive.get("_id") or archive.get("id") or "")
        entries = archive.get("entries") or []
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            gg_player_id = (entry.get("gg_player_id") or "").strip()
            member_nickname = (entry.get("memberNickname") or "").strip()
            member_type = (entry.get("memberType") or "").strip()
            records = entry.get("records") or []
            entry_id = f"archive:{archive_id}:{entry_index}"
            mapped = bool(gg_player_id)
            before = len(stored)

            for record_index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                occurred_at = _parse_timestamp(record.get("timestamp"))
                if occurred_at is None or not (from_utc <= occurred_at <= to_utc):
                    continue
                line = _line_from_record(
                    entry_id=entry_id,
                    record_id=str(record_index),
                    gg_player_id=gg_player_id,
                    member_nickname=member_nickname,
                    member_type=member_type,
                    record=record,
                    occurred_at=occurred_at,
                )
                if line is not None:
                    stored.append(line)

            stored_count = len(stored) - before
            if not mapped and stored_count:
                _record_skip(
                    skips,
                    reason=SKIP_REASON_MISSING_GG_PLAYER_ID,
                    nickname=member_nickname,
                    count=stored_count,
                )

    return stored, skips


def _audit_dates_in_archives(
    archives: list[dict[str, Any]],
    club_slug: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> set[date]:
    dates: set[date] = set()
    for archive in archives:
        for entry in archive.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            for record in entry.get("records") or []:
                if not isinstance(record, dict):
                    continue
                occurred_at = _parse_timestamp(record.get("timestamp"))
                if occurred_at is None:
                    continue
                audit_d = audit_date_for_occurred_at(occurred_at, club_slug)
                if from_date and audit_d < from_date:
                    continue
                if to_date and audit_d > to_date:
                    continue
                dates.add(audit_d)
    return dates


def backfill_early_rakeback_from_archives(
    session: Session,
    *,
    club_slugs: list[str] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[EarlyRakebackSyncReport]:
    """Sync every audit day that has archived early-RB records (plus live for each day)."""
    slugs = club_slugs or list(ALL_GG_COMPUTER_CLUB_SLUGS)
    dates_by_slug: dict[str, set[date]] = {}

    for slug in slugs:
        slug = slug.strip().lower()
        try:
            archives = fetch_early_rakeback_archives(slug)
        except Exception:
            continue
        dates_by_slug[slug] = _audit_dates_in_archives(
            archives, slug, from_date=from_date, to_date=to_date
        )

    all_dates: set[date] = set()
    for dates in dates_by_slug.values():
        all_dates.update(dates)

    reports: list[EarlyRakebackSyncReport] = []
    for audit_d in sorted(all_dates):
        active_slugs = [s for s in slugs if audit_d in dates_by_slug.get(s, set())]
        if not active_slugs:
            continue
        reports.append(
            sync_early_rakeback_for_date(session, audit_d, club_slugs=active_slugs)
        )
    return reports


def _replace_snapshot(
    session: Session,
    *,
    club_id: int,
    club_slug: str,
    audit_date: date,
    from_utc: datetime,
    to_utc: datetime,
    lines: list[dict[str, Any]],
    lines_fetched: int,
    lines_skipped_unmapped: int,
    skips: list[EarlyRakebackSkip],
) -> EarlyRakebackSnapshot:
    skipped_json = _serialize_skips(skips)
    existing = (
        session.query(EarlyRakebackSnapshot)
        .filter_by(club_slug=club_slug, audit_date=audit_date)
        .first()
    )
    if existing:
        session.query(EarlyRakebackLine).filter_by(snapshot_id=existing.id).delete(
            synchronize_session=False
        )
        snapshot = existing
        snapshot.club_id = club_id
        snapshot.fetch_from_utc = from_utc
        snapshot.fetch_to_utc = to_utc
        snapshot.lines_fetched = lines_fetched
        snapshot.lines_stored = len(lines)
        snapshot.lines_skipped_unmapped = lines_skipped_unmapped
        snapshot.skipped_nicknames = skipped_json
        snapshot.synced_at = datetime.utcnow()
        session.flush()
    else:
        snapshot = EarlyRakebackSnapshot(
            club_id=club_id,
            club_slug=club_slug,
            audit_date=audit_date,
            fetch_from_utc=from_utc,
            fetch_to_utc=to_utc,
            lines_fetched=lines_fetched,
            lines_stored=len(lines),
            lines_skipped_unmapped=lines_skipped_unmapped,
            skipped_nicknames=skipped_json,
        )
        session.add(snapshot)
        session.flush()

    for line in lines:
        session.add(
            EarlyRakebackLine(
                snapshot_id=snapshot.id,
                source_entry_id=line["source_entry_id"],
                source_record_id=line["source_record_id"],
                gg_player_id=line["gg_player_id"],
                member_nickname=line["member_nickname"],
                member_type=line["member_type"],
                amount_usd=line["amount_usd"],
                rake=line["rake"],
                pl=line["pl"],
                rakeback_percentage=line["rakeback_percentage"],
                occurred_at=line["occurred_at"],
            )
        )

    return snapshot


def trigger_early_rakeback_sync_for_occurred_at(
    session: Session,
    club_slug: str,
    occurred_at: datetime | None = None,
) -> EarlyRakebackSyncReport:
    """Sync one club for the audit day containing occurred_at (defaults to now UTC)."""
    slug = club_slug.strip().lower()
    if slug not in CLUB_SLUG_TO_NAME:
        raise ValueError(f"Unknown club slug: {club_slug!r}")
    ts = occurred_at or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    audit_date = audit_date_for_occurred_at(ts, slug)
    return sync_early_rakeback_for_date(session, audit_date, club_slugs=[slug])


def sync_early_rakeback_for_date(
    session: Session,
    audit_date: date,
    *,
    club_slugs: list[str] | None = None,
) -> EarlyRakebackSyncReport:
    slugs = club_slugs or list(ALL_GG_COMPUTER_CLUB_SLUGS)
    report = EarlyRakebackSyncReport(audit_date=audit_date)

    for slug in slugs:
        slug = slug.strip().lower()
        club_name = CLUB_SLUG_TO_NAME.get(slug, slug)
        result = EarlyRakebackClubSyncResult(club_slug=slug, club_name=club_name)

        try:
            club_id = resolve_club_id(session, slug)
            from_utc, to_utc = audit_day_window_utc(slug, audit_date)
            entries = fetch_early_rakeback_entries(slug, from_utc, to_utc)
            archives = fetch_early_rakeback_archives(slug)
            lines, skips = _flatten_entries(entries)
            archive_lines, arch_skips = _flatten_archive_entries(
                archives, from_utc, to_utc
            )
            live_fetched = sum(
                len(e.get("records") or []) for e in entries if isinstance(e, dict)
            )
            lines = lines + archive_lines
            skips = _merge_skips(skips, arch_skips)
            skipped = _skips_total(skips)
            skipped_nicknames = _skips_to_nicknames(skips)
            # live_fetched counts all live records; archive_lines includes unmapped stored rows
            lines_fetched = live_fetched + len(archive_lines)
            snapshot = _replace_snapshot(
                session,
                club_id=club_id,
                club_slug=slug,
                audit_date=audit_date,
                from_utc=from_utc,
                to_utc=to_utc,
                lines=lines,
                lines_fetched=lines_fetched,
                lines_skipped_unmapped=skipped,
                skips=skips,
            )

            result.snapshot_id = snapshot.id
            result.lines_fetched = lines_fetched
            result.lines_stored = len(lines)
            result.lines_skipped_unmapped = skipped
            result.skipped_nicknames = skipped_nicknames
            result.skips = skips

            report.clubs_synced += 1
            report.total_lines_fetched += lines_fetched
            report.total_lines_stored += len(lines)
            report.total_lines_skipped_unmapped += skipped

            if skips:
                report.warnings.append(_format_skip_warning(club_name, skips))
        except AonBetaConfigError as exc:
            result.error = str(exc)
            report.clubs_failed += 1
            report.warnings.append(f"{club_name}: {exc}")
        except httpx.HTTPStatusError as exc:
            result.error = f"aon-beta HTTP {exc.response.status_code}"
            report.clubs_failed += 1
            report.warnings.append(f"{club_name}: {result.error}")
        except Exception as exc:
            result.error = str(exc)
            report.clubs_failed += 1
            report.warnings.append(f"{club_name}: {exc}")

        report.clubs.append(result)

    return report
