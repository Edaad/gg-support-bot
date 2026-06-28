#!/usr/bin/env python3
"""Backfill Postgres player_details.chat_ids from gg-computer Mongo → Telegram groups.

Reads **gg-computer Mongo** ``player_details`` (``clubId`` + ``gg_id`` + ``nickname``),
matches each player to a support group title in Postgres (``groups.name`` or
``support_group_chats.telegram_chat_title``), then merges the Telegram ``chat_id``
into Postgres ``player_details.chat_ids``.

Dry-run by default; pass ``--apply`` to write.

Usage:
  DATABASE_URL=... GG_COMPUTER_BASE_URL=... python scripts/backfill_player_chat_ids_from_titles.py --club-slug aces-table
  DATABASE_URL=... GG_COMPUTER_BASE_URL=... python scripts/backfill_player_chat_ids_from_titles.py --club-id 2 --apply
  DATABASE_URL=... GG_COMPUTER_BASE_URL=... python scripts/backfill_player_chat_ids_from_titles.py --all-clubs --json
  DATABASE_URL=... GG_COMPUTER_BASE_URL=... python scripts/backfill_player_chat_ids_from_titles.py \\
    --club-slug round-table --nickname-fallback --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

load_dotenv()

from api.club_slug import ALL_GG_COMPUTER_CLUB_SLUGS, resolve_club_id, slug_for_club_id
from bot.services.chat_id_backfill import (
    BackfillSummary,
    GroupTitleEntry,
    MatchStatus,
    PlayerMatchResult,
    PlayerTarget,
    build_gg_id_index,
    build_nickname_index,
    entry_from_title,
    match_player_to_chats,
)
from bot.services.gg_computer import gg_computer_base_url, list_player_details_for_club
from bot.services.gg_computer_mongo import gg_computer_mongodb_uri
from bot.services.player_details import bind_chat_to_player, get_existing_chat_ids
from club_gc_settings import get_club_gc_config_by_link_club_id
from db.connection import get_db, init_engine
from db.models import Group, SupportGroupChat


@dataclass
class ClubRunResult:
    club_id: int
    club_slug: str
    summary: BackfillSummary
    players: list[PlayerMatchResult]
    mongo_players: int


def _resolve_club_targets(
    *,
    club_id: int | None,
    club_slug: str | None,
    all_clubs: bool,
) -> list[tuple[int, str]]:
    if sum(bool(x) for x in (club_id is not None, club_slug, all_clubs)) != 1:
        raise SystemExit("Specify exactly one of --club-id, --club-slug, or --all-clubs")

    if club_slug:
        with get_db() as session:
            return [(resolve_club_id(session, club_slug), club_slug)]

    if club_id is not None:
        with get_db() as session:
            slug = slug_for_club_id(session, int(club_id))
        if not slug:
            raise SystemExit(
                f"No gg-computer slug for club_id={club_id}; use --club-slug instead"
            )
        return [(int(club_id), slug)]

    out: list[tuple[int, str]] = []
    with get_db() as session:
        for slug in ALL_GG_COMPUTER_CLUB_SLUGS:
            cid = resolve_club_id(session, slug)
            out.append((cid, slug))
    return out


def _collect_chat_ids_for_club(session, club_id: int) -> set[int]:
    chat_ids = {
        int(row[0])
        for row in session.query(Group.chat_id).filter(Group.club_id == int(club_id))
    }
    cfg = get_club_gc_config_by_link_club_id(int(club_id))
    if cfg:
        for (cid,) in session.query(SupportGroupChat.telegram_chat_id).filter(
            SupportGroupChat.club_key == cfg.club_key
        ):
            chat_ids.add(int(cid))
    return chat_ids


def _resolve_title(session, chat_id: int) -> tuple[str | None, int | None]:
    group = session.query(Group).filter_by(chat_id=int(chat_id)).first()
    if group and (group.name or "").strip():
        return group.name.strip(), int(group.club_id)

    sgc = (
        session.query(SupportGroupChat)
        .filter(SupportGroupChat.telegram_chat_id == int(chat_id))
        .order_by(SupportGroupChat.created_at.desc())
        .first()
    )
    if sgc and (sgc.telegram_chat_title or "").strip():
        club_id = int(group.club_id) if group else None
        return sgc.telegram_chat_title.strip(), club_id

    if group:
        return None, int(group.club_id)
    return None, None


def _scan_group_entries(session, club_id: int) -> tuple[list[GroupTitleEntry], int, int]:
    scanned = 0
    excluded = 0
    entries: list[GroupTitleEntry] = []
    for chat_id in sorted(_collect_chat_ids_for_club(session, club_id)):
        title, resolved_club_id = _resolve_title(session, chat_id)
        if not title:
            continue
        scanned += 1
        effective_club_id = resolved_club_id if resolved_club_id is not None else int(club_id)
        if effective_club_id != int(club_id):
            continue
        entry = entry_from_title(
            chat_id=chat_id,
            club_id=effective_club_id,
            title=title,
        )
        if entry is None:
            excluded += 1
            continue
        entries.append(entry)
    return entries, scanned, excluded


def _load_mongo_player_targets(
    *,
    club_id: int,
    club_slug: str,
    include_all: bool,
) -> tuple[list[PlayerTarget], int]:
    """Load players from gg-computer Mongo; attach existing Postgres chat_ids."""
    mongo_rows = list_player_details_for_club(club_slug)
    targets: list[PlayerTarget] = []
    for row in mongo_rows:
        gg_id = str(row.get("gg_id") or "").strip()
        if not gg_id:
            continue
        nick = row.get("nickname")
        nickname = nick.strip() if isinstance(nick, str) and nick.strip() else None
        existing = get_existing_chat_ids(club_id=club_id, gg_player_id=gg_id) or []
        chat_ids = tuple(int(x) for x in existing)
        if not include_all and chat_ids:
            continue
        targets.append(
            PlayerTarget(
                club_id=int(club_id),
                gg_player_id=gg_id,
                gg_nickname=nickname,
                chat_ids=chat_ids,
            )
        )
    return targets, len(mongo_rows)


def _run_club(
    *,
    club_id: int,
    club_slug: str,
    include_all: bool,
    nickname_fallback: bool,
    apply: bool,
) -> ClubRunResult:
    with get_db() as session:
        entries, scanned, excluded = _scan_group_entries(session, club_id)

    targets, mongo_total = _load_mongo_player_targets(
        club_id=club_id,
        club_slug=club_slug,
        include_all=include_all,
    )

    gg_index = build_gg_id_index(entries)
    nick_index = build_nickname_index(entries) if nickname_fallback else {}

    results: list[PlayerMatchResult] = []
    for target in targets:
        result = match_player_to_chats(
            player=target,
            entries=entries,
            gg_index=gg_index,
            nickname_index=nick_index,
            nickname_fallback=nickname_fallback,
        )
        if apply and result.status == MatchStatus.WOULD_BIND and result.matched_chat_ids:
            bind_chat_to_player(
                club_id=result.club_id,
                gg_player_id=result.gg_player_id,
                chat_id=int(result.matched_chat_ids[0]),
            )
            result = PlayerMatchResult(
                club_id=result.club_id,
                gg_player_id=result.gg_player_id,
                status=MatchStatus.BOUND,
                matched_chat_ids=result.matched_chat_ids,
                titles=result.titles,
            )
        results.append(result)

    summary = BackfillSummary(
        clubs_processed=1,
        groups_scanned=scanned,
        groups_excluded=excluded,
        players_considered=len(results),
        bound=sum(1 for r in results if r.status == MatchStatus.BOUND),
        would_bind=sum(1 for r in results if r.status == MatchStatus.WOULD_BIND),
        already_had_chat=sum(1 for r in results if r.status == MatchStatus.ALREADY_BOUND),
        ambiguous=sum(1 for r in results if r.status == MatchStatus.AMBIGUOUS),
        unmatched=sum(1 for r in results if r.status == MatchStatus.UNMATCHED),
    )
    return ClubRunResult(
        club_id=club_id,
        club_slug=club_slug,
        summary=summary,
        players=results,
        mongo_players=mongo_total,
    )


def _merge_summaries(runs: list[ClubRunResult]) -> BackfillSummary:
    return BackfillSummary(
        clubs_processed=len(runs),
        groups_scanned=sum(r.summary.groups_scanned for r in runs),
        groups_excluded=sum(r.summary.groups_excluded for r in runs),
        players_considered=sum(r.summary.players_considered for r in runs),
        bound=sum(r.summary.bound for r in runs),
        would_bind=sum(r.summary.would_bind for r in runs),
        already_had_chat=sum(r.summary.already_had_chat for r in runs),
        ambiguous=sum(r.summary.ambiguous for r in runs),
        unmatched=sum(r.summary.unmatched for r in runs),
    )


def _print_human(runs: list[ClubRunResult], *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    total = _merge_summaries(runs)
    mongo_total = sum(r.mongo_players for r in runs)
    print(f"Chat id backfill: Mongo player_details → Telegram groups ({mode})")
    print(
        f"clubs={total.clubs_processed} mongo_players={mongo_total} "
        f"groups_scanned={total.groups_scanned} excluded={total.groups_excluded} "
        f"players_considered={total.players_considered}"
    )
    print(
        f"bound={total.bound} would_bind={total.would_bind} "
        f"already_had_chat={total.already_had_chat} ambiguous={total.ambiguous} "
        f"unmatched={total.unmatched}"
    )
    print()

    for run in runs:
        label = f"club_id={run.club_id} slug={run.club_slug} mongo={run.mongo_players}"
        print(f"--- {label} ---")
        actionable = [
            r
            for r in run.players
            if r.status
            in (
                MatchStatus.WOULD_BIND,
                MatchStatus.BOUND,
                MatchStatus.AMBIGUOUS,
            )
        ]
        if not actionable:
            print("  (no binds or ambiguities)")
            continue
        for row in actionable:
            chats = ",".join(str(x) for x in row.matched_chat_ids)
            title = row.titles[0] if row.titles else ""
            print(
                f"  {row.gg_player_id} {row.status.value} chat_id={chats} title={title!r}"
            )
        print()


def _json_payload(runs: list[ClubRunResult], *, apply: bool) -> dict[str, Any]:
    return {
        "apply": apply,
        "summary": asdict(_merge_summaries(runs)),
        "clubs": [
            {
                "club_id": run.club_id,
                "club_slug": run.club_slug,
                "mongo_players": run.mongo_players,
                "summary": asdict(run.summary),
                "players": [
                    {
                        **asdict(row),
                        "status": row.status.value,
                    }
                    for row in run.players
                ],
            }
            for run in runs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Postgres chat_ids from gg-computer Mongo player_details → TG group titles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    club = parser.add_mutually_exclusive_group(required=True)
    club.add_argument("--club-id", type=int, help="Postgres clubs.id")
    club.add_argument("--club-slug", help="gg-computer club slug (e.g. aces-table)")
    club.add_argument(
        "--all-clubs",
        action="store_true",
        help=f"All weekly clubs ({', '.join(ALL_GG_COMPUTER_CLUB_SLUGS)})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-process Mongo players who already have Postgres chat_ids (default: only unlinked).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write bindings (default: dry-run).",
    )
    parser.add_argument(
        "--nickname-fallback",
        action="store_true",
        help="Match title tail to nickname when gg_id match is absent.",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary to stdout.")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("error: set DATABASE_URL", file=sys.stderr)
        return 1

    if not gg_computer_base_url() and not gg_computer_mongodb_uri():
        print(
            "error: set GG_COMPUTER_BASE_URL or GG_COMPUTER_MONGODB_URI / MONGODB_URI",
            file=sys.stderr,
        )
        return 1

    init_engine()
    club_targets = _resolve_club_targets(
        club_id=args.club_id,
        club_slug=args.club_slug,
        all_clubs=args.all_clubs,
    )

    runs: list[ClubRunResult] = []
    for club_id, club_slug in club_targets:
        runs.append(
            _run_club(
                club_id=club_id,
                club_slug=club_slug,
                include_all=args.all,
                nickname_fallback=args.nickname_fallback,
                apply=args.apply,
            )
        )

    if args.json:
        print(json.dumps(_json_payload(runs, apply=args.apply), indent=2))
    else:
        _print_human(runs, apply=args.apply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
