"""Tests for daily support-group activity tracking."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import group_chat_daily_activity as gcda


def _make_update(
    *,
    chat_id: int = -100123,
    user_id: int = 111222,
    is_bot: bool = False,
    message_date: datetime | None = None,
    has_message: bool = True,
    has_user: bool = True,
    has_chat: bool = True,
) -> MagicMock:
    update = MagicMock()
    if has_message:
        update.message = MagicMock()
        update.message.date = message_date or datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc)
    else:
        update.message = None
    if has_chat:
        update.effective_chat = MagicMock(id=chat_id)
    else:
        update.effective_chat = None
    if has_user:
        update.effective_user = MagicMock(id=user_id, is_bot=is_bot)
    else:
        update.effective_user = None
    return update


class ActivityDateForMessageTest(unittest.TestCase):
    def test_buckets_to_america_new_york_calendar_day(self):
        # 2026-07-16 03:30 UTC = 2026-07-15 23:30 EDT
        message_at = datetime(2026, 7, 16, 3, 30, tzinfo=timezone.utc)
        self.assertEqual(
            gcda.activity_date_for_message(message_at),
            date(2026, 7, 15),
        )

    def test_naive_message_date_treated_as_utc(self):
        message_at = datetime(2026, 7, 16, 4, 30)
        self.assertEqual(
            gcda.activity_date_for_message(message_at),
            date(2026, 7, 16),
        )


class RecordGroupChatDailyActivityTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_first_message_for_linked_group(self):
        update = _make_update()
        context = MagicMock()

        with (
            patch.object(gcda, "is_test_bot_worker", return_value=False),
            patch.object(gcda, "get_club_for_chat", return_value=2),
            patch.object(gcda, "upsert_group_chat_daily_activity") as upsert_mock,
        ):
            await gcda.record_group_chat_daily_activity(update, context)

        upsert_mock.assert_called_once_with(
            chat_id=-100123,
            club_id=2,
            message_at=update.message.date,
        )

    async def test_skips_bot_messages(self):
        update = _make_update(is_bot=True)
        context = MagicMock()

        with (
            patch.object(gcda, "is_test_bot_worker", return_value=False),
            patch.object(gcda, "get_club_for_chat", return_value=2),
            patch.object(gcda, "upsert_group_chat_daily_activity") as upsert_mock,
        ):
            await gcda.record_group_chat_daily_activity(update, context)

        upsert_mock.assert_not_called()

    async def test_skips_unlinked_groups(self):
        update = _make_update()
        context = MagicMock()

        with (
            patch.object(gcda, "is_test_bot_worker", return_value=False),
            patch.object(gcda, "get_club_for_chat", return_value=None),
            patch.object(gcda, "upsert_group_chat_daily_activity") as upsert_mock,
        ):
            await gcda.record_group_chat_daily_activity(update, context)

        upsert_mock.assert_not_called()

    async def test_skips_test_worker(self):
        update = _make_update()
        context = MagicMock()

        with (
            patch.object(gcda, "is_test_bot_worker", return_value=True),
            patch.object(gcda, "upsert_group_chat_daily_activity") as upsert_mock,
        ):
            await gcda.record_group_chat_daily_activity(update, context)

        upsert_mock.assert_not_called()

    async def test_skips_when_message_missing(self):
        update = _make_update(has_message=False)
        context = MagicMock()

        with patch.object(gcda, "upsert_group_chat_daily_activity") as upsert_mock:
            await gcda.record_group_chat_daily_activity(update, context)

        upsert_mock.assert_not_called()

    async def test_db_failure_does_not_raise(self):
        update = _make_update()
        context = MagicMock()

        with (
            patch.object(gcda, "is_test_bot_worker", return_value=False),
            patch.object(gcda, "get_club_for_chat", return_value=2),
            patch.object(
                gcda,
                "upsert_group_chat_daily_activity",
                side_effect=RuntimeError("db down"),
            ),
        ):
            await gcda.record_group_chat_daily_activity(update, context)


class UpsertGroupChatDailyActivityTest(unittest.TestCase):
    def test_upsert_executes_sql_with_expected_params(self):
        message_at = datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc)
        session = MagicMock()
        db_cm = MagicMock()
        db_cm.__enter__.return_value = session
        db_cm.__exit__.return_value = False

        with patch.object(gcda, "get_db", return_value=db_cm):
            gcda.upsert_group_chat_daily_activity(
                chat_id=-100123,
                club_id=2,
                message_at=message_at,
            )

        session.execute.assert_called_once()
        params = session.execute.call_args.args[1]
        self.assertEqual(params["chat_id"], -100123)
        self.assertEqual(params["club_id"], 2)
        self.assertEqual(params["activity_date"], date(2026, 7, 16))
        self.assertEqual(params["message_at"], message_at)


if __name__ == "__main__":
    unittest.main()
