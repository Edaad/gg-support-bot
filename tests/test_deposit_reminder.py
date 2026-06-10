import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import deposit as deposit_module
from bot.handlers import add as add_module


class DepositReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_reminder_deletes_tracked_messages_before_follow_up(self):
        chat_id = -100123
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [11, 12, 13]

        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        job = MagicMock()
        job.chat_id = chat_id
        job.data = {"club_id": 1}

        context = MagicMock()
        context.job = job
        context.bot = bot

        deposit_module.get_deposit_method_names = MagicMock(return_value=["Venmo"])

        await deposit_module._deposit_reminder_callback(context)

        self.assertEqual(bot.delete_message.await_count, 3)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=11)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=12)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=13)
        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("Hey! Just checking in", sent_text)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    def test_cancel_reminder_clears_tracked_messages(self):
        chat_id = -100456
        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 99
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [21, 22]

        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []

        deposit_module._cancel_deposit_reminder(context, chat_id)

        self.assertNotIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    def test_public_cancel_after_schedule_removes_job(self):
        chat_id = -100789
        pending_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [pending_job]

        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 42
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [31]

        deposit_module.cancel_deposit_reminder(context, chat_id)

        context.job_queue.get_jobs_by_name.assert_called_once_with(
            deposit_module._reminder_job_name(chat_id)
        )
        pending_job.schedule_removal.assert_called_once()
        self.assertNotIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    async def test_execute_add_cancels_deposit_reminder(self):
        chat_id = -100321
        update = MagicMock()
        update.effective_chat = MagicMock(id=chat_id)
        update.effective_user = MagicMock(id=493310710)
        update.message = MagicMock()

        context = MagicMock()
        context.application.create_task = MagicMock()

        with (
            patch.object(add_module, "cancel_deposit_reminder") as cancel_mock,
            patch.object(add_module, "record_activity_for_chat"),
            patch.object(add_module, "invalidate_pending_one_time_bypasses"),
            patch.object(add_module, "get_club_config_for_admin", return_value=None),
            patch.object(add_module, "_add_bot_api_path", new=AsyncMock()),
        ):
            await add_module._execute_add(
                update,
                context,
                club_id=2,
                amount=Decimal("500"),
                bonus=None,
                name=None,
            )

        cancel_mock.assert_called_once_with(context, chat_id)


if __name__ == "__main__":
    unittest.main()
