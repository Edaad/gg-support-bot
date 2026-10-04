"""Decisions for the unread-group Pushover. No Telegram connection."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from bot.services.unread_group_alert import (
    THRESHOLD,
    DialogSnap,
    GroupRow,
    alert_action,
    format_unread_alert,
    ready_to_evaluate,
    visible_unreads,
)

NOW = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)


def _group(
    chat_id: int,
    club_key: str,
    *,
    name: str = "Player",
    internal: bool = False,
    club_display_name: str | None = None,
) -> GroupRow:
    return GroupRow(
        chat_id=chat_id,
        club_key=club_key,
        club_display_name=club_display_name or club_key,
        name=name,
        is_internal=internal,
    )


class AlertActionTests(unittest.TestCase):
    def test_below_threshold_clears(self) -> None:
        self.assertEqual(
            alert_action(THRESHOLD - 1, NOW - timedelta(minutes=1), NOW),
            "clear",
        )

    def test_first_crossing_sends(self) -> None:
        self.assertEqual(alert_action(THRESHOLD, None, NOW), "send")

    def test_restart_inside_window_waits(self) -> None:
        sent = NOW - timedelta(minutes=4)
        self.assertEqual(alert_action(7, sent, NOW), "wait")

    def test_sends_again_after_five_minutes(self) -> None:
        sent = NOW - timedelta(minutes=5)
        self.assertEqual(alert_action(THRESHOLD, sent, NOW), "send")


class StartupGateTests(unittest.TestCase):
    def test_waits_until_every_connected_club_has_snapshotted(self) -> None:
        self.assertFalse(ready_to_evaluate({"round_table", "clubgto"}, {"round_table"}))

    def test_empty_connected_set_does_not_evaluate(self) -> None:
        self.assertFalse(ready_to_evaluate(set(), set()))

    def test_ready_when_connected_clubs_have_snapshotted(self) -> None:
        connected = {"round_table"}
        self.assertTrue(ready_to_evaluate(connected, connected | {"clubgto"}))


class VisibleUnreadTests(unittest.TestCase):
    def test_disconnected_club_is_omitted(self) -> None:
        groups = [
            _group(1, "round_table", name="RT player", club_display_name="Round Table"),
            _group(2, "clubgto", name="GTO player", club_display_name="ClubGTO"),
        ]
        dialogs = {
            ("round_table", 1): DialogSnap(2, "RT live", archived=False),
            ("clubgto", 2): DialogSnap(9, "GTO live", archived=False),
        }
        rows = visible_unreads(groups, dialogs, {"round_table"})
        self.assertEqual(rows, [("Round Table", "RT live")])

    def test_internal_chat_excluded(self) -> None:
        groups = [_group(1, "round_table", internal=True)]
        dialogs = {("round_table", 1): DialogSnap(4, "Test", archived=False)}
        self.assertEqual(visible_unreads(groups, dialogs, {"round_table"}), [])

    def test_archived_chat_included(self) -> None:
        groups = [_group(1, "round_table", name="Fallback")]
        dialogs = {("round_table", 1): DialogSnap(1, "Archived title", archived=True)}
        self.assertEqual(
            visible_unreads(groups, dialogs, {"round_table"}),
            [("round_table", "Archived title")],
        )

    def test_zero_unread_and_missing_dialog_do_not_count(self) -> None:
        groups = [
            _group(1, "round_table", name="Read"),
            _group(2, "round_table", name="Gone"),
        ]
        dialogs = {
            ("round_table", 1): DialogSnap(0, "Read", archived=False),
            ("round_table", 2): None,
        }
        self.assertEqual(visible_unreads(groups, dialogs, {"round_table"}), [])

    def test_blank_telegram_title_uses_group_name(self) -> None:
        groups = [_group(1, "creator_club", name="Stored name")]
        dialogs = {("creator_club", 1): DialogSnap(3, "  ", archived=False)}
        self.assertEqual(
            visible_unreads(groups, dialogs, {"creator_club"}),
            [("creator_club", "Stored name")],
        )

    def test_sorts_by_club_then_title(self) -> None:
        groups = [
            _group(1, "clubgto", club_display_name="ClubGTO"),
            _group(2, "round_table", club_display_name="Round Table"),
        ]
        dialogs = {
            ("clubgto", 1): DialogSnap(1, "Zed", archived=False),
            ("round_table", 2): DialogSnap(1, "Amy", archived=False),
        }
        self.assertEqual(
            visible_unreads(groups, dialogs, {"clubgto", "round_table"}),
            [("ClubGTO", "Zed"), ("Round Table", "Amy")],
        )


class FormatTests(unittest.TestCase):
    def test_header_and_titles(self) -> None:
        text = format_unread_alert(6, ["Amy", "Zed"])
        self.assertEqual(
            text,
            "There are 6 unread group chats. Admin might not be active right now.\n"
            "Amy\n"
            "Zed",
        )

    def test_truncation_keeps_the_count_sentence(self) -> None:
        header = "There are 40 unread group chats. Admin might not be active right now."
        titles = ["T" * 80 for _ in range(40)]
        text = format_unread_alert(40, titles)
        self.assertLessEqual(len(text), 1024)
        self.assertTrue(text.startswith(header))
        self.assertIn("\n", text)
