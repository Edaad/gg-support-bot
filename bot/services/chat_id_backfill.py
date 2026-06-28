"""Pure helpers for backfilling player_details.chat_ids from stored group titles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

from api.payments_helpers import is_analytics_excluded_group_title
from bot.services.player_details import GroupTitleParts, parse_group_title_parts


@dataclass(frozen=True)
class GroupTitleEntry:
    chat_id: int
    club_id: int
    title: str
    parsed: GroupTitleParts | None


@dataclass(frozen=True)
class PlayerTarget:
    club_id: int
    gg_player_id: str
    gg_nickname: str | None
    chat_ids: tuple[int, ...]


class MatchStatus(str, Enum):
    ALREADY_BOUND = "already_had_chat"
    WOULD_BIND = "would_bind"
    BOUND = "bound"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class PlayerMatchResult:
    club_id: int
    gg_player_id: str
    status: MatchStatus
    matched_chat_ids: tuple[int, ...] = ()
    titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackfillSummary:
    clubs_processed: int
    groups_scanned: int
    groups_excluded: int
    players_considered: int
    bound: int
    would_bind: int
    already_had_chat: int
    ambiguous: int
    unmatched: int


def normalize_nickname(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw.startswith("@"):
        raw = raw[1:]
    return raw


def entry_from_title(*, chat_id: int, club_id: int, title: str) -> GroupTitleEntry | None:
    """Return a scan entry or None if the title is excluded or unparseable."""
    cleaned = (title or "").strip()
    if not cleaned or is_analytics_excluded_group_title(cleaned):
        return None
    return GroupTitleEntry(
        chat_id=int(chat_id),
        club_id=int(club_id),
        title=cleaned,
        parsed=parse_group_title_parts(cleaned),
    )


def build_gg_id_index(
    entries: Iterable[GroupTitleEntry],
) -> dict[int, dict[str, list[int]]]:
    """Map club_id -> gg_player_id -> chat_ids (parseable titles only)."""
    out: dict[int, dict[str, list[int]]] = {}
    for entry in entries:
        if not entry.parsed:
            continue
        by_gg = out.setdefault(entry.club_id, {})
        by_gg.setdefault(entry.parsed.gg_player_id, []).append(entry.chat_id)
    return out


def build_nickname_index(
    entries: Iterable[GroupTitleEntry],
) -> dict[int, dict[str, list[int]]]:
    """Map club_id -> normalized tail -> chat_ids (parseable titles with tails only)."""
    out: dict[int, dict[str, list[int]]] = {}
    for entry in entries:
        if not entry.parsed or not entry.parsed.tail:
            continue
        key = normalize_nickname(entry.parsed.tail)
        if not key:
            continue
        by_nick = out.setdefault(entry.club_id, {})
        by_nick.setdefault(key, []).append(entry.chat_id)
    return out


def _titles_for_chats(
    entries: Sequence[GroupTitleEntry],
    chat_ids: Sequence[int],
) -> tuple[str, ...]:
    by_chat = {e.chat_id: e.title for e in entries}
    return tuple(by_chat.get(int(cid), "") for cid in chat_ids if by_chat.get(int(cid)))


def match_player_to_chats(
    *,
    player: PlayerTarget,
    entries: Sequence[GroupTitleEntry],
    gg_index: Mapping[int, Mapping[str, Sequence[int]]],
    nickname_index: Mapping[int, Mapping[str, Sequence[int]]],
    nickname_fallback: bool,
) -> PlayerMatchResult:
    """Resolve chat binding for one player_details row."""
    club = int(player.club_id)
    gg_id = player.gg_player_id.strip()
    existing = {int(x) for x in player.chat_ids}

    gg_hits = list(gg_index.get(club, {}).get(gg_id, ()))
    if len(gg_hits) > 1:
        return PlayerMatchResult(
            club_id=club,
            gg_player_id=gg_id,
            status=MatchStatus.AMBIGUOUS,
            matched_chat_ids=tuple(sorted(set(gg_hits))),
            titles=_titles_for_chats(entries, gg_hits),
        )

    matched: list[int] = []
    if len(gg_hits) == 1:
        matched = gg_hits
    elif nickname_fallback and player.gg_nickname:
        nick_key = normalize_nickname(player.gg_nickname)
        nick_hits = list(nickname_index.get(club, {}).get(nick_key, ()))
        if len(nick_hits) > 1:
            return PlayerMatchResult(
                club_id=club,
                gg_player_id=gg_id,
                status=MatchStatus.AMBIGUOUS,
                matched_chat_ids=tuple(sorted(set(nick_hits))),
                titles=_titles_for_chats(entries, nick_hits),
            )
        if len(nick_hits) == 1:
            matched = nick_hits

    if not matched:
        return PlayerMatchResult(
            club_id=club,
            gg_player_id=gg_id,
            status=MatchStatus.UNMATCHED,
        )

    chat_id = int(matched[0])
    if chat_id in existing:
        return PlayerMatchResult(
            club_id=club,
            gg_player_id=gg_id,
            status=MatchStatus.ALREADY_BOUND,
            matched_chat_ids=(chat_id,),
            titles=_titles_for_chats(entries, (chat_id,)),
        )

    return PlayerMatchResult(
        club_id=club,
        gg_player_id=gg_id,
        status=MatchStatus.WOULD_BIND,
        matched_chat_ids=(chat_id,),
        titles=_titles_for_chats(entries, (chat_id,)),
    )
