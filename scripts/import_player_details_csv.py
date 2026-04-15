#!/usr/bin/env python3
"""
Import player_data_mapped.csv into player_details (PostgreSQL).

- Parses chat_id, gg_player_id, club_id (supports [4] and "[2, 3]" multi-club rows).
- Aggregates chat_ids per (gg_player_id, club_id); merges duplicates in the CSV.
- On conflict, merges new chat_ids into existing arrays (distinct).

Usage (dry run — default, no DB writes):
    python scripts/import_player_details_csv.py --csv player_data_mapped.csv

Apply to database (requires DATABASE_URL):
    DATABASE_URL=postgresql://... python scripts/import_player_details_csv.py --csv player_data_mapped.csv --apply

Sanitization: strict gg_player_id pattern, Telegram chat_id rules, club_id bounds, control-char stripping,
CSV formula-injection prefixes, optional club allowlist from DB when applying.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

# Project root (parent of scripts/)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.connection import init_engine


GG_PLAYER_ID_MAX = 255
# Telegram group/supergroup chat ids are typically negative 64-bit
CHAT_ID_MIN = -(2**63)
CHAT_ID_MAX = 2**63 - 1
# Strict: GG ids look like "2494-5329" (digits-hyphen-digits only)
GG_PLAYER_ID_STRICT = re.compile(r"^[0-9]{1,48}-[0-9]{1,48}$")
CLUB_ID_MIN = 1
CLUB_ID_MAX = 1_000_000


def _strip_cell(s: str | None) -> str:
    if s is None:
        return ""
    return s.replace("\x00", "").strip()


def _strip_csv_formula_prefix(s: str) -> str:
    """Mitigate CSV/Excel formula injection (=cmd, +cmd, @cmd). Do not strip leading '-' — conflicts with negative numbers."""
    s = s.lstrip()
    while s and s[0] in "=\t\r":
        s = s[1:].lstrip()
    if s and s[0] == "+":
        s = s[1:].lstrip()
    if s and s[0] == "@":
        s = s[1:].lstrip()
    return s


def _remove_control_chars(s: str) -> str:
    return "".join(
        ch for ch in s if unicodedata.category(ch) != "Cc" or ch in ("\t", "\n", "\r")
    )


def parse_chat_id_field(raw: str) -> tuple[int | None, list[int]]:
    """
    Parse chat_id cell. Handles merged typo like -4864335196[2] (chat + club suffix).
    Returns (chat_id, extra_club_ids_from_suffix).
    """
    raw = _strip_cell(raw)
    merged = re.match(r"^(-?\d+)\[(\d+(?:,\s*\d+)*)\]$", raw)
    if merged:
        cid = int(merged.group(1))
        extra = [int(x.strip()) for x in merged.group(2).split(",") if x.strip()]
        return cid, extra
    try:
        return int(raw), []
    except ValueError:
        return None, []


def parse_club_ids(raw: str) -> list[int]:
    """Parse [4], "[2, 3]", or '4' into a list of positive club ids."""
    raw = _strip_cell(raw).strip('"').strip("'")
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        out.append(int(p))
    return out


def validate_gg_player_id_strict(s: str) -> str | None:
    """
    Strict GG player id: ASCII digits, single hyphen, two non-empty parts (e.g. 2494-5329).
    Returns normalized string or None if invalid.
    """
    s = _strip_cell(s)
    s = _remove_control_chars(s)
    s = _strip_csv_formula_prefix(s)
    s = s.strip()
    if not s or len(s) > GG_PLAYER_ID_MAX:
        return None
    if not GG_PLAYER_ID_STRICT.match(s):
        return None
    return s


def load_csv(
    path: Path,
    *,
    require_negative_chat_id: bool = True,
) -> tuple[dict[tuple[str, int], set[int]], list[str]]:
    """
    Returns aggregated map (gg_player_id, club_id) -> set of chat_ids, and list of warning lines.
    """
    warnings: list[str] = []
    agg: dict[tuple[str, int], set[int]] = defaultdict(set)

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        expected = {"chat_id", "gg_player_id", "club_id"}
        if reader.fieldnames:
            fn = {h.strip().lower() for h in reader.fieldnames}
            if not expected.issubset(fn):
                raise SystemExit(
                    f"CSV must have columns {expected}, got {reader.fieldnames}"
                )

        for lineno, row in enumerate(reader, start=2):
            # chat_id: do not use formula-prefix strip (would break negative Telegram ids)
            chat_raw = _strip_cell(row.get("chat_id", "") or row.get("Chat_ID", ""))
            gg_raw = _strip_csv_formula_prefix(
                _strip_cell(row.get("gg_player_id", "") or row.get("GG_Player_ID", ""))
            )
            club_raw = _strip_csv_formula_prefix(
                _strip_cell(row.get("club_id", "") or row.get("Club_ID", ""))
            )

            if not chat_raw and not _strip_cell(gg_raw) and not _strip_cell(club_raw):
                continue

            if not chat_raw:
                warnings.append(f"line {lineno}: missing chat_id, skipped")
                continue

            chat_id, extra_clubs_from_chat = parse_chat_id_field(chat_raw)
            if chat_id is None:
                warnings.append(f"line {lineno}: invalid chat_id {chat_raw!r}, skipped")
                continue

            if chat_id < CHAT_ID_MIN or chat_id > CHAT_ID_MAX:
                warnings.append(f"line {lineno}: chat_id out of range, skipped")
                continue

            if require_negative_chat_id and chat_id >= 0:
                warnings.append(
                    f"line {lineno}: chat_id must be negative (Telegram group chat), got {chat_id}, skipped"
                )
                continue

            gg = validate_gg_player_id_strict(str(gg_raw))
            if not gg:
                warnings.append(
                    f"line {lineno}: gg_player_id must match strict pattern digits-digits "
                    f"(e.g. 2494-5329), got {str(gg_raw)!r}, skipped"
                )
                continue

            try:
                club_ids = parse_club_ids(club_raw)
            except ValueError as e:
                warnings.append(f"line {lineno}: bad club_id {club_raw!r} ({e}), skipped")
                continue

            club_ids = list(dict.fromkeys(club_ids + extra_clubs_from_chat))

            if not club_ids:
                warnings.append(f"line {lineno}: no club ids, skipped")
                continue

            for cid in club_ids:
                if cid < CLUB_ID_MIN or cid > CLUB_ID_MAX:
                    warnings.append(
                        f"line {lineno}: club_id {cid} outside allowed range "
                        f"[{CLUB_ID_MIN}, {CLUB_ID_MAX}], skipped"
                    )
                    continue
                agg[(gg, cid)].add(chat_id)

    return agg, warnings


def fetch_club_ids(engine) -> set[int]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM clubs")).fetchall()
    return {r[0] for r in rows}


def apply_upserts(engine, agg: dict[tuple[str, int], set[int]], valid_clubs: set[int]) -> tuple[int, list[str]]:
    """Returns (rows_written, skip_messages for unknown club_id)."""
    skipped: list[str] = []
    upsert = text(
        """
        INSERT INTO player_details (chat_ids, gg_player_id, club_id)
        VALUES (:chat_ids, :gg_player_id, :club_id)
        ON CONFLICT (gg_player_id, club_id) DO UPDATE SET
            chat_ids = (
                SELECT ARRAY(
                    SELECT DISTINCT unnest(
                        COALESCE(player_details.chat_ids, '{}'::bigint[])
                        || COALESCE(EXCLUDED.chat_ids, '{}'::bigint[])
                    )
                    ORDER BY 1
                )
            )
        """
    )
    n = 0
    with engine.begin() as conn:
        for (gg, cid), chats in sorted(agg.items()):
            if cid not in valid_clubs:
                skipped.append(f"unknown club_id {cid} for gg_player_id {gg!r} ({len(chats)} chats)")
                continue
            chat_list = sorted(chats)
            conn.execute(
                upsert,
                {
                    "chat_ids": chat_list,
                    "gg_player_id": gg,
                    "club_id": cid,
                },
            )
            n += 1
    return n, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--csv",
        type=Path,
        default=ROOT / "player_data_mapped.csv",
        help="Path to CSV (default: player_data_mapped.csv in repo root)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write to the database (otherwise dry run only)",
    )
    ap.add_argument(
        "--allow-nonnegative-chat-id",
        action="store_true",
        help="Allow chat_id >= 0 (default: require negative, typical Telegram group chats)",
    )
    args = ap.parse_args()

    if not args.csv.is_file():
        raise SystemExit(f"File not found: {args.csv}")

    agg, warns = load_csv(
        args.csv,
        require_negative_chat_id=not args.allow_nonnegative_chat_id,
    )
    print(f"Aggregated keys (gg_player_id, club_id): {len(agg)}")
    total_chats = sum(len(s) for s in agg.values())
    print(f"Total chat id placements (with duplicates across keys counted per key): {total_chats}")

    if warns:
        print(f"\nWarnings ({len(warns)}):")
        for w in warns[:50]:
            print(f"  {w}")
        if len(warns) > 50:
            print(f"  ... and {len(warns) - 50} more")

    if not args.apply:
        print("\nDry run only. Pass --apply with DATABASE_URL set to insert/merge.")
        return

    if not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL must be set for --apply")

    engine = init_engine()
    valid_clubs = fetch_club_ids(engine)
    print(f"\nClubs in database: {len(valid_clubs)} ids")

    n, skipped = apply_upserts(engine, agg, valid_clubs)
    print(f"Upserted {n} player_details row(s).")
    if skipped:
        print(f"Skipped unknown club_id ({len(skipped)} issue(s)), first 20:")
        for s in skipped[:20]:
            print(f"  {s}")
        if len(skipped) > 20:
            print(f"  ... and {len(skipped) - 20} more")


if __name__ == "__main__":
    main()
