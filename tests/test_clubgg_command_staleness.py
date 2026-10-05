"""Staleness lock and Slack copy for late staff /add and /cash."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from bot.services.clubgg_rpa_events import (
    OUTCOME_OWNED,
    OUTCOME_PERSIST_FAILED,
    OUTCOME_PROCEED,
    OUTCOME_STALE,
    claim_command,
    is_command_stale,
)
from bot.services.escalation_notification import format_stale_command_slack_text


def _now() -> datetime:
    return datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc)


class StaleAgeTests(unittest.TestCase):
    def test_missing_send_time_is_stale(self) -> None:
        self.assertTrue(is_command_stale(None, now=_now()))

    def test_eleven_seconds_is_stale(self) -> None:
        sent = _now() - timedelta(seconds=11)
        self.assertTrue(is_command_stale(sent, now=_now()))

    def test_ten_seconds_still_runs(self) -> None:
        sent = _now() - timedelta(seconds=10)
        self.assertFalse(is_command_stale(sent, now=_now()))

    def test_naive_send_time_is_treated_as_utc(self) -> None:
        sent = datetime(2026, 10, 5, 15, 59, 50)
        self.assertFalse(is_command_stale(sent, now=_now()))


class _Session:
    def __init__(self, fail: Exception | None = None) -> None:
        self.added = []
        self.fail = fail

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        if self.fail is not None:
            raise self.fail


class ClaimCommandTests(unittest.TestCase):
    def _claim(self, *, sent_at, fail=None):
        session = _Session(fail)

        @contextmanager
        def _db():
            yield session

        with patch("bot.services.clubgg_rpa_events.get_db", _db):
            outcome = claim_command(
                request_id="tg-1-2",
                operation="deposit",
                source="staff_add",
                telegram_chat_id=1,
                message_id=2,
                club_id=3,
                group_title="Jacob / 88421",
                amount=Decimal("500"),
                bonus=None,
                message_sent_at_value=sent_at,
                path="telethon",
                now=_now(),
            )
        return outcome, session

    def test_fresh_command_proceeds(self) -> None:
        outcome, session = self._claim(sent_at=_now())
        self.assertEqual(outcome, OUTCOME_PROCEED)
        self.assertEqual(session.added[0].status, "started")
        self.assertTrue(session.added[0].command_lock)

    def test_stale_command_is_recorded_and_blocked(self) -> None:
        outcome, session = self._claim(sent_at=_now() - timedelta(seconds=30))
        self.assertEqual(outcome, OUTCOME_STALE)
        self.assertEqual(session.added[0].status, "stale")

    def test_duplicate_lock_is_owned(self) -> None:
        outcome, _session = self._claim(
            sent_at=_now(), fail=IntegrityError("dup", None, None)
        )
        self.assertEqual(outcome, OUTCOME_OWNED)

    def test_database_error_refuses_the_command(self) -> None:
        outcome, _session = self._claim(sent_at=_now(), fail=RuntimeError("db down"))
        self.assertEqual(outcome, OUTCOME_PERSIST_FAILED)


class StaleSlackTests(unittest.TestCase):
    def _text(self, **kwargs) -> str:
        with patch(
            "bot.services.escalation_notification._club_display_name",
            return_value="Round Table",
        ):
            return format_stale_command_slack_text(
                club_id=1,
                chat_id=-100,
                title="Jacob / 88421",
                **kwargs,
            )

    def test_add_with_bonus(self) -> None:
        text = self._text(command="add", amount=Decimal("500"), bonus=Decimal("50"))
        self.assertEqual(
            text,
            "\n".join(
                [
                    "*/add didn't work — server side issue. Add chips manually.*",
                    "Club: Round Table",
                    "`Jacob / 88421`",
                    "Amount: $500",
                    "Bonus: $50",
                ]
            ),
        )

    def test_bonus_command(self) -> None:
        text = self._text(command="bonus", amount=Decimal("50"), bonus=None)
        self.assertIn(
            "*/bonus didn't work — server side issue. Add chips manually.*",
            text,
        )
        self.assertIn("Amount: $50", text)
        self.assertNotIn("Bonus:", text)

    def test_cash_command(self) -> None:
        text = self._text(command="cash", amount=Decimal("500"), bonus=None)
        self.assertEqual(
            text,
            "\n".join(
                [
                    "*/cash didn't work — server side issue. Claim chips manually.*",
                    "Club: Round Table",
                    "`Jacob / 88421`",
                    "Amount: $500",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
