"""Read ``groups`` rows from a local ``pg_dump`` custom-format backup (no live DB writes).

Used to find chats that were basic groups before ``upgrade_groups_to_supergroup.py``
and map them to current supergroup ids via a read-only live lookup.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from bot.services.chat_id_remap import is_legacy_basic_chat_id


@dataclass(frozen=True)
class BackupGroupRow:
    chat_id: int
    club_id: int
    name: str


@dataclass(frozen=True)
class AffectedMigratedGroup:
    club_id: int
    title: str
    old_chat_id: int
    current_chat_id: int | None
    status: str


def find_earliest_upgrade_backup(repo_root: Path) -> Path:
    """Return ``backups/upgrade_supergroup_<ts>/database.dump`` with earliest timestamp."""
    candidates = sorted(repo_root.glob("backups/upgrade_supergroup_*/database.dump"))
    if not candidates:
        raise FileNotFoundError(
            "No upgrade_supergroup_*/database.dump found under backups/ "
            "(run upgrade_groups_to_supergroup.py --apply once with --skip-backup unset)."
        )
    return candidates[0]


def parse_groups_table_from_dump(dump_path: Path) -> list[BackupGroupRow]:
    """Extract ``groups`` COPY rows from a ``pg_restore`` custom-format dump."""
    if not dump_path.is_file():
        raise FileNotFoundError(f"Backup not found: {dump_path}")

    if subprocess.run(["which", "pg_restore"], capture_output=True).returncode != 0:
        raise SystemExit("pg_restore not found on PATH (install PostgreSQL client tools).")

    proc = subprocess.run(
        ["pg_restore", "--data-only", "-t", "groups", "-f", "-", str(dump_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "pg_restore failed").strip()
        raise RuntimeError(f"pg_restore groups failed: {err}")

    rows: list[BackupGroupRow] = []
    in_copy = False
    for line in proc.stdout.splitlines():
        if line.startswith("COPY public.groups"):
            in_copy = True
            continue
        if not in_copy:
            continue
        if line == r"\.":
            break
        if not line or line.startswith("--"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            chat_id = int(parts[0])
            club_id = int(parts[1])
        except ValueError:
            continue
        name = (parts[3] or "").strip()
        rows.append(BackupGroupRow(chat_id=chat_id, club_id=club_id, name=name))
    return rows


def basic_groups_from_backup(dump_path: Path) -> list[BackupGroupRow]:
    """Groups that were basic (not ``-100…``) in the backup snapshot."""
    return [r for r in parse_groups_table_from_dump(dump_path) if is_legacy_basic_chat_id(r.chat_id)]


def resolve_affected_from_backup(
    dump_path: Path,
    *,
    mtproto_club_ids: frozenset[int],
    club_id_filter: int | None = None,
    chat_id_filter: int | None = None,
) -> list[AffectedMigratedGroup]:
    """Map backup basic groups to live ``groups`` rows (read-only).

    ``migrated`` — was basic in backup, live row is a supergroup (new chat id).
    ``not_migrated_yet`` — still basic in live DB (upgrade not applied for this chat).
    ``missing_in_live_db`` — no ``groups`` row with same club + title.
    """
    from db.connection import get_db
    from db.models import Group

    basics = basic_groups_from_backup(dump_path)
    if club_id_filter is not None:
        basics = [r for r in basics if r.club_id == int(club_id_filter)]
    basics = [r for r in basics if r.club_id in mtproto_club_ids]

    out: list[AffectedMigratedGroup] = []
    with get_db() as session:
        for row in basics:
            title = (row.name or "").strip()
            if not title:
                out.append(
                    AffectedMigratedGroup(
                        club_id=int(row.club_id),
                        title="",
                        old_chat_id=int(row.chat_id),
                        current_chat_id=None,
                        status="empty_title",
                    )
                )
                continue

            live = (
                session.query(Group.chat_id)
                .filter(Group.club_id == int(row.club_id), Group.name == title)
                .first()
            )
            if live is None:
                out.append(
                    AffectedMigratedGroup(
                        club_id=int(row.club_id),
                        title=title,
                        old_chat_id=int(row.chat_id),
                        current_chat_id=None,
                        status="missing_in_live_db",
                    )
                )
                continue

            current_cid = int(live[0])
            if is_legacy_basic_chat_id(current_cid):
                status = "not_migrated_yet"
            elif _is_supergroup_chat_id(current_cid):
                status = "migrated" if current_cid != int(row.chat_id) else "unchanged_id"
            else:
                status = "unexpected_chat_id"

            out.append(
                AffectedMigratedGroup(
                    club_id=int(row.club_id),
                    title=title,
                    old_chat_id=int(row.chat_id),
                    current_chat_id=current_cid,
                    status=status,
                )
            )

    if chat_id_filter is not None:
        cid = int(chat_id_filter)
        out = [
            r
            for r in out
            if r.current_chat_id == cid or r.old_chat_id == cid
        ]
    return out


def _is_supergroup_chat_id(chat_id: int) -> bool:
    cid = int(chat_id)
    return cid < 0 and str(cid).startswith("-100")
