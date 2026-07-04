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


@dataclass
class EarlyRakebackClubSyncResult:
    club_slug: str
    club_name: str
    snapshot_id: int | None = None
    lines_fetched: int = 0
    lines_stored: int = 0
    lines_skipped_unmapped: int = 0
    skipped_nicknames: list[str] = field(default_factory=list)
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


def _flatten_entries(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Return (stored lines, skipped count, skipped nicknames)."""
    stored: list[dict[str, Any]] = []
    skipped = 0
    skipped_nicknames: list[str] = []

    for entry in entries:
        entry_id = _entry_id(entry)
        gg_player_id = (entry.get("gg_player_id") or "").strip()
        member_nickname = (entry.get("memberNickname") or "").strip()
        member_type = (entry.get("memberType") or "").strip()
        records = entry.get("records") or []

        if not gg_player_id:
            skipped += len(records) if records else 1
            if member_nickname and member_nickname not in skipped_nicknames:
                skipped_nicknames.append(member_nickname)
            continue

        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = _record_id(record)
            amount = record.get("calculatedAmount")
            if amount is None:
                continue
            stored.append(
                {
                    "source_entry_id": entry_id,
                    "source_record_id": record_id,
                    "gg_player_id": gg_player_id,
                    "member_nickname": member_nickname or None,
                    "member_type": member_type or None,
                    "amount_usd": Decimal(str(amount)),
                    "rake": _decimal_or_none(record.get("rake")),
                    "pl": _decimal_or_none(record.get("pl")),
                    "rakeback_percentage": _decimal_or_none(
                        record.get("rakebackPercentage")
                    ),
                    "occurred_at": _parse_timestamp(record.get("timestamp")),
                }
            )

    return stored, skipped, skipped_nicknames


def _flatten_archive_entries(
    archives: list[dict[str, Any]],
    from_utc: datetime,
    to_utc: datetime,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Flatten archived early-RB records whose timestamps fall in [from_utc, to_utc]."""
    stored: list[dict[str, Any]] = []
    skipped = 0
    skipped_nicknames: list[str] = []

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

            if not gg_player_id:
                in_window = 0
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    occurred_at = _parse_timestamp(record.get("timestamp"))
                    if occurred_at and from_utc <= occurred_at <= to_utc:
                        in_window += 1
                if in_window:
                    skipped += in_window
                    if member_nickname and member_nickname not in skipped_nicknames:
                        skipped_nicknames.append(member_nickname)
                continue

            for record_index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                occurred_at = _parse_timestamp(record.get("timestamp"))
                if occurred_at is None or not (from_utc <= occurred_at <= to_utc):
                    continue
                amount = record.get("calculatedAmount")
                if amount is None:
                    continue
                stored.append(
                    {
                        "source_entry_id": entry_id,
                        "source_record_id": str(record_index),
                        "gg_player_id": gg_player_id,
                        "member_nickname": member_nickname or None,
                        "member_type": member_type or None,
                        "amount_usd": Decimal(str(amount)),
                        "rake": _decimal_or_none(record.get("rake")),
                        "pl": _decimal_or_none(record.get("pl")),
                        "rakeback_percentage": _decimal_or_none(
                            record.get("rakebackPercentage")
                        ),
                        "occurred_at": occurred_at,
                    }
                )

    return stored, skipped, skipped_nicknames


def _merge_skipped_nicknames(
    left: list[str], right: list[str]
) -> list[str]:
    out = list(left)
    for name in right:
        if name not in out:
            out.append(name)
    return out


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
    skipped_nicknames: list[str],
) -> EarlyRakebackSnapshot:
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
        snapshot.skipped_nicknames = (
            json.dumps(skipped_nicknames) if skipped_nicknames else None
        )
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
            skipped_nicknames=json.dumps(skipped_nicknames)
            if skipped_nicknames
            else None,
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
            lines, skipped, skipped_nicknames = _flatten_entries(entries)
            archive_lines, arch_skipped, arch_nicknames = _flatten_archive_entries(
                archives, from_utc, to_utc
            )
            live_fetched = sum(
                len(e.get("records") or []) for e in entries if isinstance(e, dict)
            )
            lines = lines + archive_lines
            skipped += arch_skipped
            skipped_nicknames = _merge_skipped_nicknames(
                skipped_nicknames, arch_nicknames
            )
            lines_fetched = live_fetched + len(archive_lines) + arch_skipped
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
                skipped_nicknames=skipped_nicknames,
            )

            result.snapshot_id = snapshot.id
            result.lines_fetched = lines_fetched
            result.lines_stored = len(lines)
            result.lines_skipped_unmapped = skipped
            result.skipped_nicknames = skipped_nicknames

            report.clubs_synced += 1
            report.total_lines_fetched += lines_fetched
            report.total_lines_stored += len(lines)
            report.total_lines_skipped_unmapped += skipped

            if skipped_nicknames:
                report.warnings.append(
                    f"{club_name}: {skipped} record(s) skipped (unmapped identity): "
                    + ", ".join(skipped_nicknames[:10])
                    + ("…" if len(skipped_nicknames) > 10 else "")
                )
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
