"""Tests for player support notes service."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from bot.services.club import is_any_club_staff
from bot.services.player_support_notes import (
    SupportNoteValidationError,
    add_note,
    format_open_issues_list,
    format_player_note_history,
    format_resolve_result,
    get_player_note_history,
    list_open_issues,
    resolve_issues_for_player,
    validate_gg_player_id,
)
from db.models import Base, Club, ClubLinkedAccount, PlayerSupportIssue, PlayerSupportNote


class TestValidateGgPlayerId(unittest.TestCase):
    def test_accepts_valid(self) -> None:
        self.assertEqual(validate_gg_player_id("8190-5287"), "8190-5287")

    def test_rejects_invalid(self) -> None:
        with self.assertRaises(SupportNoteValidationError):
            validate_gg_player_id("abc")


class SupportNotesDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        tables = [
            Club.__table__,
            ClubLinkedAccount.__table__,
            PlayerSupportIssue.__table__,
            PlayerSupportNote.__table__,
        ]
        Base.metadata.create_all(self.engine, tables=tables)
        self.Session = sessionmaker(bind=self.engine)
        session = self.Session()
        session.add_all(
            [
                Club(id=1, name="Round Table", telegram_user_id=1001, is_active=True),
                Club(id=2, name="ClubGTO", telegram_user_id=1002, is_active=True),
            ]
        )
        session.commit()
        session.close()

    def _session(self):
        return self.Session()

    @patch("bot.services.player_support_notes.get_db")
    def test_add_note_creates_open_issue(self, mock_get_db: MagicMock) -> None:
        session = self._session()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        note, issue = add_note(
            club_id=1,
            gg_player_id="1111-2222",
            situation="Deposit stuck",
            actions_taken="Checked RT Hub",
            next_steps="Verify payment screenshot",
            created_by_telegram_user_id=999,
        )
        self.assertEqual(note.id, 1)
        self.assertEqual(issue.status, "open")
        self.assertEqual(issue.gg_player_id, "1111-2222")

        note2, issue2 = add_note(
            club_id=1,
            gg_player_id="1111-2222",
            situation="Still waiting",
            actions_taken="Pinged head admin",
            next_steps="Follow up next shift",
            created_by_telegram_user_id=999,
        )
        self.assertEqual(note2.id, 2)
        self.assertEqual(issue2.id, issue.id)

    @patch("bot.services.player_support_notes.get_db")
    def test_list_open_issues_sorted_by_latest_note(self, mock_get_db: MagicMock) -> None:
        session = self._session()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        add_note(
            club_id=1,
            gg_player_id="1111-2222",
            situation="Older issue",
            actions_taken="A",
            next_steps="Old next",
            created_by_telegram_user_id=1,
        )
        add_note(
            club_id=2,
            gg_player_id="3333-4444",
            situation="Newer issue",
            actions_taken="B",
            next_steps="Fresh next",
            created_by_telegram_user_id=1,
        )
        session.query(PlayerSupportNote).filter_by(id=1).update(
            {
                "created_at": datetime.now(timezone.utc) - timedelta(hours=2),
            }
        )
        session.commit()

        summaries = list_open_issues()
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0].gg_player_id, "3333-4444")
        self.assertIn("Fresh next", summaries[0].latest_next_steps)

    @patch("bot.services.player_support_notes.get_db")
    def test_resolve_and_history(self, mock_get_db: MagicMock) -> None:
        session = self._session()
        mock_get_db.return_value.__enter__.return_value = session
        mock_get_db.return_value.__exit__.return_value = False

        add_note(
            club_id=1,
            gg_player_id="1111-2222",
            situation="Issue A",
            actions_taken="Did X",
            next_steps="Do Y",
            created_by_telegram_user_id=1,
        )
        add_note(
            club_id=2,
            gg_player_id="1111-2222",
            situation="Issue B",
            actions_taken="Did Z",
            next_steps="Do W",
            created_by_telegram_user_id=1,
        )

        result = resolve_issues_for_player(
            "1111-2222",
            resolved_by_telegram_user_id=42,
        )
        self.assertEqual(result.resolved_count, 2)
        self.assertEqual(format_resolve_result(result), format_resolve_result(result))

        history = get_player_note_history("1111-2222")
        self.assertEqual(len(history), 2)
        text = format_player_note_history(history)
        self.assertIn("1111-2222", text)
        self.assertIn("RESOLVED", text)

        summaries = list_open_issues()
        self.assertEqual(summaries, [])


class TestIsAnyClubStaff(unittest.TestCase):
    @patch("bot.services.club.get_db")
    def test_primary_owner(self, mock_get_db: MagicMock) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        session.query.return_value.filter.return_value.first.return_value = (1,)
        self.assertTrue(is_any_club_staff(555))

    @patch("bot.services.club.get_db")
    def test_linked_account(self, mock_get_db: MagicMock) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        primary_query = MagicMock()
        linked_query = MagicMock()
        session.query.side_effect = [primary_query, linked_query]
        primary_query.filter.return_value.first.return_value = None
        linked_query.join.return_value.filter.return_value.first.return_value = (9,)
        self.assertTrue(is_any_club_staff(777))

    @patch("bot.services.club.get_db")
    def test_not_staff(self, mock_get_db: MagicMock) -> None:
        session = MagicMock()
        mock_get_db.return_value.__enter__.return_value = session
        primary_query = MagicMock()
        linked_query = MagicMock()
        session.query.side_effect = [primary_query, linked_query]
        primary_query.filter.return_value.first.return_value = None
        linked_query.join.return_value.filter.return_value.first.return_value = None
        self.assertFalse(is_any_club_staff(888))


class TestFormatOpenIssuesList(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(format_open_issues_list([]), "No unresolved player issues.")
