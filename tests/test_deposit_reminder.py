import unittest
from unittest.mock import AsyncMock, MagicMock

from bot.handlers import deposit as deposit_module


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


if __name__ == "__main__":
    unittest.main()
