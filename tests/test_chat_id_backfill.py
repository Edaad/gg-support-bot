"""Unit tests for chat_id backfill title matching."""

from __future__ import annotations

import unittest

from api.payments_helpers import is_analytics_excluded_group_title
from bot.services.chat_id_backfill import (
    MatchStatus,
    PlayerTarget,
    build_gg_id_index,
    build_nickname_index,
    entry_from_title,
    match_player_to_chats,
)
from bot.services.player_details import parse_group_title_parts


class ChatIdBackfillTests(unittest.TestCase):
    def _entry(self, chat_id: int, title: str, club_id: int = 2):
        entry = entry_from_title(chat_id=chat_id, club_id=club_id, title=title)
        self.assertIsNotNone(entry)
        return entry

    def test_entry_from_title_excludes_analytics_groups(self):
        self.assertIsNone(
            entry_from_title(
                chat_id=1,
                club_id=2,
                title="RT / 9090-9999 / TEST",
            )
        )
        self.assertTrue(is_analytics_excluded_group_title("CC / 8834-2222/ @jz034"))

    def test_build_gg_id_index_groups_by_club_and_player(self):
        entries = [
            self._entry(100, "RT / 1111-2222 / Alice"),
            self._entry(200, "RT / 3333-4444 / Bob"),
            self._entry(300, "GTO / 5555-6666 / Carol", club_id=4),
        ]
        index = build_gg_id_index(entries)
        self.assertEqual(index[2]["1111-2222"], [100])
        self.assertEqual(index[4]["5555-6666"], [300])

    def test_match_by_gg_id_would_bind(self):
        entries = [self._entry(100, "RT / 1111-2222 / Alice")]
        gg_index = build_gg_id_index(entries)
        nick_index = build_nickname_index(entries)
        player = PlayerTarget(
            club_id=2,
            gg_player_id="1111-2222",
            gg_nickname="Alice",
            chat_ids=(),
        )
        result = match_player_to_chats(
            player=player,
            entries=entries,
            gg_index=gg_index,
            nickname_index=nick_index,
            nickname_fallback=False,
        )
        self.assertEqual(result.status, MatchStatus.WOULD_BIND)
        self.assertEqual(result.matched_chat_ids, (100,))

    def test_match_already_had_chat(self):
        entries = [self._entry(100, "RT / 1111-2222 / Alice")]
        gg_index = build_gg_id_index(entries)
        player = PlayerTarget(
            club_id=2,
            gg_player_id="1111-2222",
            gg_nickname=None,
            chat_ids=(100,),
        )
        result = match_player_to_chats(
            player=player,
            entries=entries,
            gg_index=gg_index,
            nickname_index={},
            nickname_fallback=False,
        )
        self.assertEqual(result.status, MatchStatus.ALREADY_BOUND)

    def test_ambiguous_when_multiple_chats_share_gg_id(self):
        entries = [
            self._entry(100, "RT / 1111-2222 / Alice"),
            self._entry(101, "RT AT / 1111-2222 / Alice"),
        ]
        gg_index = build_gg_id_index(entries)
        player = PlayerTarget(
            club_id=2,
            gg_player_id="1111-2222",
            gg_nickname=None,
            chat_ids=(),
        )
        result = match_player_to_chats(
            player=player,
            entries=entries,
            gg_index=gg_index,
            nickname_index={},
            nickname_fallback=False,
        )
        self.assertEqual(result.status, MatchStatus.AMBIGUOUS)
        self.assertEqual(set(result.matched_chat_ids), {100, 101})

    def test_nickname_fallback_only_when_gg_id_missing(self):
        entries = [self._entry(100, "RT / 9999-8888 / ThePirate")]
        gg_index = build_gg_id_index(entries)
        nick_index = build_nickname_index(entries)
        player = PlayerTarget(
            club_id=2,
            gg_player_id="1111-2222",
            gg_nickname="ThePirate",
            chat_ids=(),
        )
        result = match_player_to_chats(
            player=player,
            entries=entries,
            gg_index=gg_index,
            nickname_index=nick_index,
            nickname_fallback=True,
        )
        self.assertEqual(result.status, MatchStatus.WOULD_BIND)
        self.assertEqual(result.matched_chat_ids, (100,))

    def test_nickname_fallback_not_used_when_gg_id_matches_elsewhere(self):
        entries = [
            self._entry(100, "RT / 1111-2222 / Other"),
            self._entry(200, "RT / 9999-8888 / ThePirate"),
        ]
        gg_index = build_gg_id_index(entries)
        nick_index = build_nickname_index(entries)
        player = PlayerTarget(
            club_id=2,
            gg_player_id="1111-2222",
            gg_nickname="ThePirate",
            chat_ids=(),
        )
        result = match_player_to_chats(
            player=player,
            entries=entries,
            gg_index=gg_index,
            nickname_index=nick_index,
            nickname_fallback=True,
        )
        self.assertEqual(result.status, MatchStatus.WOULD_BIND)
        self.assertEqual(result.matched_chat_ids, (100,))

    def test_parse_group_title_parts_rt_at_union(self):
        parsed = parse_group_title_parts("RT AT / 8190-5287 / ThePirate343")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.gg_player_id, "8190-5287")
        self.assertIn("RT", parsed.shorthands)
        self.assertIn("AT", parsed.shorthands)


if __name__ == "__main__":
    unittest.main()
