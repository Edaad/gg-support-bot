import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import deposit_incomplete_watch as diw


class DepositIncompleteWatchTests(unittest.TestCase):
    def test_arm_upserts_row(self):
        armed_at = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None

        with patch("bot.services.deposit_incomplete_watch.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = session
            diw.arm_deposit_incomplete_watch(
                telegram_chat_id=-100,
                club_id=1,
                customer_telegram_user_id=42,
                group_title="GTO / 1 / Nick",
                armed_at=armed_at,
            )

        session.add.assert_called_once()
        row = session.add.call_args.args[0]
        self.assertEqual(row.telegram_chat_id, -100)
        self.assertEqual(row.club_id, 1)
        self.assertEqual(row.customer_telegram_user_id, 42)
        self.assertEqual(row.group_title, "GTO / 1 / Nick")
        self.assertEqual(row.armed_at, armed_at)

    def test_delete_removes_row(self):
        session = MagicMock()
        with patch("bot.services.deposit_incomplete_watch.get_db") as mock_get_db:
            mock_get_db.return_value.__enter__.return_value = session
            diw.delete_deposit_incomplete_watch(-100)

        session.query.return_value.filter_by.return_value.delete.assert_called_once()

    def test_restore_skips_when_payment_seen(self):
        armed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = MagicMock()
        row.telegram_chat_id = -100
        row.club_id = 1
        row.armed_at = armed_at

        job_queue = MagicMock()
        with (
            patch.object(diw, "list_armed_deposit_incomplete_watches", return_value=[row]),
            patch.object(diw, "payment_or_chips_seen_since_arm", return_value=True),
            patch.object(diw, "_deposit_reminder_seconds", return_value=600),
            patch.object(diw, "delete_deposit_incomplete_watch") as delete_mock,
            patch.object(diw, "_schedule_deposit_reminder_job") as schedule_mock,
        ):
            diw.restore_deposit_incomplete_watches(job_queue)

        delete_mock.assert_called_once_with(-100)
        schedule_mock.assert_not_called()

    def test_restore_reschedules_with_remaining_delay(self):
        armed_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        row = MagicMock()
        row.telegram_chat_id = -100
        row.club_id = 2
        row.armed_at = armed_at

        job_queue = MagicMock()
        with (
            patch.object(diw, "list_armed_deposit_incomplete_watches", return_value=[row]),
            patch.object(diw, "payment_or_chips_seen_since_arm", return_value=False),
            patch.object(diw, "_deposit_reminder_seconds", return_value=600),
            patch.object(diw, "_schedule_deposit_reminder_job") as schedule_mock,
        ):
            diw.restore_deposit_incomplete_watches(job_queue)

        schedule_mock.assert_called_once()
        kwargs = schedule_mock.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], -100)
        self.assertEqual(kwargs["club_id"], 2)
        self.assertAlmostEqual(kwargs["when"], 420.0, delta=5.0)


class DepositIncompleteWatchAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_deposit_incomplete_escalation_deletes_row(self):
        with (
            patch(
                "bot.services.escalation_notification.notify_escalation_slack",
                new_callable=AsyncMock,
            ) as notify_mock,
            patch.object(diw, "delete_deposit_incomplete_watch") as delete_mock,
        ):
            await diw.notify_deposit_incomplete_escalation(
                chat_id=-100,
                club_id=1,
                title="GTO / 1 / Nick",
            )

        notify_mock.assert_awaited_once()
        delete_mock.assert_called_once_with(-100)


if __name__ == "__main__":
    unittest.main()
