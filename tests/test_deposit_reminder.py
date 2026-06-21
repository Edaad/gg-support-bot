import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import deposit as deposit_module
from bot.handlers import add as add_module
from bot.services import payment_group_notify as pgn_module
from bot.services import mtproto_group_add as mtproto_add_module


class DepositReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_reminder_deletes_tracked_messages_before_follow_up(self):
        chat_id = -100123
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [11, 12, 13]

        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        job = MagicMock()
        job.chat_id = chat_id
        job.data = {
            "club_id": 1,
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
        }

        context = MagicMock()
        context.job = job
        context.bot = bot

        deposit_module.get_deposit_method_names = MagicMock(return_value=["Venmo"])

        with patch.object(deposit_module, "_should_skip_deposit_reminder", return_value=False):
            await deposit_module._deposit_reminder_callback(context)

        self.assertEqual(bot.delete_message.await_count, 3)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=11)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=12)
        bot.delete_message.assert_any_await(chat_id=chat_id, message_id=13)
        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args.kwargs["text"]
        self.assertIn("Hey! Just checking in", sent_text)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    async def test_reminder_skips_when_payment_bound_since_schedule(self):
        chat_id = -100124
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [21]

        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        scheduled_at = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.chat_id = chat_id
        job.data = {"club_id": 1, "scheduled_at": scheduled_at.isoformat()}

        context = MagicMock()
        context.job = job
        context.bot = bot

        with patch.object(deposit_module, "_should_skip_deposit_reminder", return_value=True):
            await deposit_module._deposit_reminder_callback(context)

        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_awaited_once_with(chat_id=chat_id, message_id=21)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    async def test_reminder_skips_when_stripe_checkout_completed_since_schedule(self):
        chat_id = -100125
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [22]

        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        scheduled_at = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.chat_id = chat_id
        job.data = {"club_id": 1, "scheduled_at": scheduled_at.isoformat()}

        context = MagicMock()
        context.job = job
        context.bot = bot

        with (
            patch.object(deposit_module, "_chat_has_payment_bound_since", return_value=False),
            patch.object(
                deposit_module, "_chat_has_stripe_checkout_completed_since", return_value=True
            ),
        ):
            await deposit_module._deposit_reminder_callback(context)

        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_awaited_once_with(chat_id=chat_id, message_id=22)
        self.assertNotIn(chat_id, deposit_module._DEPOSIT_INFO_MESSAGE_IDS)

    async def test_reminder_skips_when_deposit_activity_since_schedule(self):
        chat_id = -100126
        deposit_module._DEPOSIT_INFO_MESSAGE_IDS[chat_id] = [23]

        bot = AsyncMock()
        bot.send_message = AsyncMock()
        bot.delete_message = AsyncMock()

        scheduled_at = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)
        job = MagicMock()
        job.chat_id = chat_id
        job.data = {"club_id": 1, "scheduled_at": scheduled_at.isoformat()}

        context = MagicMock()
        context.job = job
        context.bot = bot

        with (
            patch.object(deposit_module, "_chat_has_payment_bound_since", return_value=False),
            patch.object(
                deposit_module, "_chat_has_stripe_checkout_completed_since", return_value=False
            ),
            patch.object(deposit_module, "_chat_has_deposit_activity_since", return_value=True),
        ):
            await deposit_module._deposit_reminder_callback(context)

        bot.send_message.assert_not_awaited()
        bot.delete_message.assert_awaited_once_with(chat_id=chat_id, message_id=23)
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

    def test_cancel_for_chat_uses_registered_app_job_queue(self):
        chat_id = -100790
        pending_job = MagicMock()
        app = MagicMock()
        app.job_queue.get_jobs_by_name.return_value = [pending_job]

        deposit_module.register_deposit_reminder_runtime(app)
        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 7

        deposit_module.cancel_deposit_reminder_for_chat(chat_id)

        pending_job.schedule_removal.assert_called_once()
        self.assertNotIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)

    async def test_group_activity_cancels_on_bot_payment_received_message(self):
        chat_id = -100200
        pending_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [pending_job]
        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 55

        update = MagicMock()
        update.effective_chat = MagicMock(id=chat_id)
        update.effective_user = MagicMock(id=999, is_bot=True)
        update.message = MagicMock(
            text="We have received your payment for $500, chips will be loaded shortly!!",
            caption=None,
        )

        await deposit_module.cancel_deposit_reminder_on_group_activity(update, context)

        pending_job.schedule_removal.assert_called_once()
        self.assertNotIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)

    async def test_group_activity_cancels_on_staff_added_message(self):
        chat_id = -100201
        pending_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [pending_job]
        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 56

        update = MagicMock()
        update.effective_chat = MagicMock(id=chat_id)
        update.effective_user = MagicMock(id=493310710, is_bot=False)
        update.message = MagicMock(text="Added 500 boss! Enjoy!", caption=None)

        with (
            patch.object(deposit_module, "get_club_for_chat", return_value=2),
            patch.object(deposit_module, "_can_cancel_reminder_as_staff", return_value=True),
        ):
            await deposit_module.cancel_deposit_reminder_on_group_activity(update, context)

        pending_job.schedule_removal.assert_called_once()
        self.assertNotIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)

    async def test_group_activity_does_not_cancel_on_player_added_message(self):
        chat_id = -100202
        pending_job = MagicMock()
        context = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = [pending_job]
        deposit_module._PENDING_DEPOSIT_REMINDERS[chat_id] = 57

        update = MagicMock()
        update.effective_chat = MagicMock(id=chat_id)
        update.effective_user = MagicMock(id=111222, is_bot=False)
        update.message = MagicMock(text="I added money on Venmo", caption=None)

        with (
            patch.object(deposit_module, "get_club_for_chat", return_value=2),
            patch.object(deposit_module, "_can_cancel_reminder_as_staff", return_value=False),
        ):
            await deposit_module.cancel_deposit_reminder_on_group_activity(update, context)

        pending_job.schedule_removal.assert_not_called()
        self.assertIn(chat_id, deposit_module._PENDING_DEPOSIT_REMINDERS)

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

    async def test_notify_payment_received_cancels_deposit_reminder(self):
        chat_id = -100999
        with (
            patch.object(
                pgn_module,
                "support_bot_tokens_to_try",
                return_value=["token123"],
            ),
            patch("bot.services.payment_group_notify.Bot") as bot_cls,
            patch.object(
                deposit_module,
                "cancel_deposit_reminder_for_chat",
            ) as cancel_mock,
        ):
            bot = AsyncMock()
            bot.send_message = AsyncMock()
            bot_cls.return_value = bot

            ok = await pgn_module.notify_player_group_payment_received(
                telegram_chat_id=chat_id,
                amount_cents=50_000,
            )

        self.assertTrue(ok)
        cancel_mock.assert_called_once_with(chat_id)

    async def test_mtproto_add_cancels_deposit_reminder(self):
        chat_id = -100888
        event = MagicMock()
        event.is_private = False
        event.chat_id = chat_id
        event.raw_text = "/add 29"
        event.client = AsyncMock()
        event.client.send_message = AsyncMock()
        event.message = MagicMock(id=99)
        event.delete = AsyncMock()

        cfg = MagicMock()
        cfg.club_key = "round_table"
        cfg.link_club_id = 2

        with (
            patch.object(mtproto_add_module, "get_club_for_chat", return_value=2),
            patch.object(mtproto_add_module, "_delete_add_command_message", new=AsyncMock()),
            patch.object(mtproto_add_module, "record_activity_for_chat"),
            patch.object(mtproto_add_module, "invalidate_pending_one_time_bypasses"),
            patch.object(
                deposit_module,
                "cancel_deposit_reminder_for_chat",
            ) as cancel_mock,
        ):
            await mtproto_add_module.handle_group_add_outgoing(
                event,
                cfg,
                listener_label="test",
            )

        cancel_mock.assert_called_once_with(chat_id)


if __name__ == "__main__":
    unittest.main()
