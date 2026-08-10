"""Tests for idle help prompt (replaces Slack-only player_idle)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers import group_activity as ga_handler
from bot.services import escalation_notification as esc
from bot.services import group_activity as ga
from bot.services import popup_keyboard as pk
from bot.handlers.deposit import get_deposit_handler
from bot.handlers.cashout import get_cashout_handler, idlehelp_cashout_entry
from telegram.ext import ConversationHandler


class IdleHelpPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()
        esc.clear_awaiting_agent_state_for_tests()

    async def test_offer_sends_prompt_with_three_buttons_no_slack(self):
        bot = MagicMock()
        sent = SimpleNamespace(message_id=42)
        bot.send_message = AsyncMock(return_value=sent)
        context = MagicMock()
        context.chat_data = {}

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            ok = await esc.offer_idle_help_prompt(
                bot,
                99,
                club_id=1,
                title="CC / 1 / Nick",
                message_text="need help",
                context=context,
            )

        self.assertTrue(ok)
        slack.assert_not_awaited()
        bot.send_message.assert_awaited_once()
        kwargs = bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["text"], esc.IDLE_HELP_COPY)
        markup = kwargs["reply_markup"]
        labels = [
            btn.text
            for row in markup.inline_keyboard
            for btn in row
        ]
        self.assertEqual(labels, ["Deposit", "Cashout", "Talk to agent"])
        callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        self.assertEqual(
            callbacks,
            [
                esc.IDLE_HELP_CB_DEPOSIT,
                esc.IDLE_HELP_CB_CASHOUT,
                esc.IDLE_HELP_CB_AGENT,
            ],
        )
        self.assertEqual(context.chat_data["idle_help_message_text"], "need help")
        self.assertEqual(context.chat_data["idle_help_prompt_message_id"], 42)

    async def test_fire_player_idle_offers_prompt_not_slack(self):
        bot = MagicMock()
        with patch.object(
            esc, "offer_idle_help_prompt", new_callable=AsyncMock, return_value=True
        ) as offer:
            with patch.object(
                esc, "notify_escalation_slack", new_callable=AsyncMock
            ) as slack:
                await esc.fire_player_idle(
                    bot,
                    99,
                    club_id=1,
                    title="G",
                    message_text="hi",
                    context=MagicMock(chat_data={}),
                )
        offer.assert_awaited_once()
        slack.assert_not_awaited()

    async def test_talk_to_agent_acks_and_slacks_idle(self):
        query = MagicMock()
        query.data = esc.IDLE_HELP_CB_AGENT
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        query.message = SimpleNamespace(message_id=7)

        update = MagicMock()
        update.callback_query = query
        update.effective_chat = SimpleNamespace(id=99, title="CC / 1 / Nick")

        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 3,
            "idle_help_title": "CC / 1 / Nick",
            "idle_help_message_text": "need chips",
            "idle_help_prompt_message_id": 7,
        }
        context.bot.send_message = AsyncMock()
        context.job_queue = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []
        context.job_queue.run_once = MagicMock()

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            await esc.handle_idle_help_agent(update, context)

        query.answer.assert_awaited_once()
        query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
        context.bot.send_message.assert_awaited_once_with(
            chat_id=99, text=esc.IDLE_HELP_AGENT_ACK
        )
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.args[0], esc.REASON_PLAYER_IDLE)
        self.assertEqual(slack.await_args.kwargs["message_text"], "need chips")
        self.assertEqual(slack.await_args.kwargs["club_id"], 3)
        self.assertNotIn("idle_help_message_text", context.chat_data)
        self.assertTrue(esc.awaiting_agent_episode_active(99))

    async def test_talk_to_agent_ignores_other_callbacks(self):
        query = MagicMock()
        query.data = esc.IDLE_HELP_CB_DEPOSIT
        query.answer = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            await esc.handle_idle_help_agent(update, MagicMock(chat_data={}))
        query.answer.assert_not_awaited()
        slack.assert_not_awaited()

    async def test_free_text_while_prompt_up_slacks_like_agent(self):
        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()
        bot.send_message = AsyncMock()
        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 3,
            "idle_help_title": "CC / 1 / Nick",
            "idle_help_message_text": "original reach out",
            "idle_help_prompt_message_id": 42,
        }
        context.job_queue = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []
        context.job_queue.run_once = MagicMock()

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            consumed = await esc.handle_idle_help_free_text(
                bot,
                99,
                context,
                club_id=3,
                title="CC / 1 / Nick",
                message_text="when does rakeback process?",
            )

        self.assertTrue(consumed)
        bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=99,
            message_id=42,
            reply_markup=None,
        )
        bot.send_message.assert_awaited_once_with(
            chat_id=99, text=esc.IDLE_HELP_AGENT_ACK
        )
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.args[0], esc.REASON_PLAYER_IDLE)
        self.assertEqual(
            slack.await_args.kwargs["message_text"],
            "when does rakeback process?",
        )
        self.assertEqual(slack.await_args.kwargs["club_id"], 3)
        self.assertNotIn("idle_help_message_text", context.chat_data)
        self.assertNotIn("idle_help_prompt_message_id", context.chat_data)
        self.assertTrue(esc.awaiting_agent_episode_active(99))

    async def test_free_text_without_stash_does_not_slack(self):
        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()
        bot.send_message = AsyncMock()
        context = MagicMock()
        context.chat_data = {}

        with patch.object(
            esc, "notify_escalation_slack", new_callable=AsyncMock
        ) as slack:
            consumed = await esc.handle_idle_help_free_text(
                bot,
                99,
                context,
                club_id=3,
                title="G",
                message_text="hello",
            )

        self.assertFalse(consumed)
        slack.assert_not_awaited()
        bot.send_message.assert_not_awaited()
        bot.edit_message_reply_markup.assert_not_awaited()

    async def test_free_text_close_allows_new_prompt_stash(self):
        bot = MagicMock()
        bot.edit_message_reply_markup = AsyncMock()
        bot.send_message = AsyncMock(
            side_effect=[
                None,  # ack from complete_idle_help_as_agent
                SimpleNamespace(message_id=99),  # new offer
            ]
        )
        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 3,
            "idle_help_message_text": "old",
            "idle_help_prompt_message_id": 42,
        }
        context.job_queue = MagicMock()
        context.job_queue.get_jobs_by_name.return_value = []
        context.job_queue.run_once = MagicMock()

        with patch.object(esc, "notify_escalation_slack", new_callable=AsyncMock):
            await esc.handle_idle_help_free_text(
                bot,
                99,
                context,
                club_id=3,
                title="G",
                message_text="rakeback?",
            )

        self.assertFalse(esc.idle_help_prompt_active(context))

        with patch.object(esc, "notify_escalation_slack", new_callable=AsyncMock):
            ok = await esc.offer_idle_help_prompt(
                bot,
                99,
                club_id=3,
                title="G",
                message_text="later reach out",
                context=context,
            )
        self.assertTrue(ok)
        self.assertTrue(esc.idle_help_prompt_active(context))
        self.assertEqual(
            context.chat_data["idle_help_message_text"], "later reach out"
        )
        self.assertEqual(context.chat_data["idle_help_prompt_message_id"], 99)


class IdleHelpGroupActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ga.clear_activity_state_for_tests()
        esc.clear_awaiting_agent_state_for_tests()

    async def test_esc_on_skips_popup_schedule_and_strip(self):
        user = SimpleNamespace(id=55, is_bot=False, username="p")
        chat = SimpleNamespace(id=-100, type="supergroup", title="CC / 1 / P")
        message = SimpleNamespace(
            message_id=1,
            text="hello",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user

        context = MagicMock()
        context.chat_data = {}
        context.bot = MagicMock()
        context.job_queue = MagicMock()

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=True):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    with patch.object(ga, "is_support_sender", return_value=False):
                        with patch.object(
                            pk, "upsert_player_telegram_user_id"
                        ):
                            with patch.object(pk, "remember_player_message"):
                                with patch.object(
                                    ga,
                                    "record_human_message",
                                    return_value=SimpleNamespace(should_fire_idle=True),
                                ):
                                    with patch.object(
                                        esc,
                                        "handle_deposit_sent_player_followup",
                                        new_callable=AsyncMock,
                                        return_value=False,
                                    ):
                                        with patch.object(
                                            esc,
                                            "handle_deposit_player_message",
                                            new_callable=AsyncMock,
                                            return_value=False,
                                        ):
                                            with patch.object(
                                                esc,
                                                "fire_player_idle",
                                                new_callable=AsyncMock,
                                            ) as fire:
                                                with patch.object(
                                                    pk,
                                                    "silent_strip_if_installed",
                                                    new_callable=AsyncMock,
                                                ) as strip:
                                                    with patch.object(
                                                        pk,
                                                        "schedule_popup_keyboard_idle",
                                                    ) as schedule:
                                                        await ga_handler.group_activity_handler(
                                                            update, context
                                                        )

        fire.assert_awaited_once()
        strip.assert_not_awaited()
        schedule.assert_not_called()

    async def test_free_text_with_stash_skips_idle_refire(self):
        user = SimpleNamespace(id=55, is_bot=False, username="p")
        chat = SimpleNamespace(id=-100, type="supergroup", title="CC / 1 / P")
        message = SimpleNamespace(
            message_id=2,
            text="when does rakeback process?",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user
        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 1,
            "idle_help_title": "CC / 1 / P",
            "idle_help_message_text": "hi",
            "idle_help_prompt_message_id": 42,
        }
        context.bot = MagicMock()
        context.job_queue = MagicMock()

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=False):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    with patch.object(ga, "is_support_sender", return_value=False):
                        with patch.object(pk, "upsert_player_telegram_user_id"):
                            with patch.object(pk, "remember_player_message"):
                                with patch.object(
                                    ga,
                                    "record_human_message",
                                    return_value=SimpleNamespace(
                                        should_fire_idle=False
                                    ),
                                ):
                                    with patch.object(
                                        esc,
                                        "handle_deposit_sent_player_followup",
                                        new_callable=AsyncMock,
                                        return_value=False,
                                    ):
                                        with patch.object(
                                            esc,
                                            "handle_deposit_player_message",
                                            new_callable=AsyncMock,
                                            return_value=False,
                                        ):
                                            with patch.object(
                                                esc,
                                                "handle_idle_help_free_text",
                                                new_callable=AsyncMock,
                                                return_value=True,
                                            ) as free_text:
                                                with patch.object(
                                                    esc,
                                                    "fire_player_idle",
                                                    new_callable=AsyncMock,
                                                ) as fire:
                                                    await ga_handler.group_activity_handler(
                                                        update, context
                                                    )

        free_text.assert_awaited_once()
        self.assertEqual(
            free_text.await_args.kwargs["message_text"],
            "when does rakeback process?",
        )
        fire.assert_not_awaited()

    async def test_flow_command_with_stash_does_not_call_free_text(self):
        user = SimpleNamespace(id=55, is_bot=False, username="p")
        chat = SimpleNamespace(id=-100, type="supergroup", title="CC / 1 / P")
        message = SimpleNamespace(
            message_id=2,
            text="/deposit",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user
        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 1,
            "idle_help_prompt_message_id": 42,
            "idle_help_message_text": "hi",
        }
        context.bot = MagicMock()
        context.job_queue = MagicMock()

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=False):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=True
                ):
                    with patch.object(ga, "is_support_sender", return_value=False):
                        with patch.object(pk, "upsert_player_telegram_user_id"):
                            with patch.object(pk, "remember_player_message"):
                                with patch.object(
                                    pk,
                                    "is_flow_command_text",
                                    return_value=True,
                                ):
                                    with patch.object(
                                        ga,
                                        "record_human_message",
                                        return_value=SimpleNamespace(
                                            should_fire_idle=False
                                        ),
                                    ):
                                        with patch.object(
                                            esc,
                                            "handle_idle_help_free_text",
                                            new_callable=AsyncMock,
                                            return_value=True,
                                        ) as free_text:
                                            with patch.object(
                                                esc,
                                                "fire_player_idle",
                                                new_callable=AsyncMock,
                                            ) as fire:
                                                await ga_handler.group_activity_handler(
                                                    update, context
                                                )

        free_text.assert_not_awaited()
        fire.assert_not_awaited()

    async def test_esc_off_still_schedules_popup(self):
        user = SimpleNamespace(id=55, is_bot=False, username="p")
        chat = SimpleNamespace(id=-100, type="supergroup", title="CC / 1 / P")
        message = SimpleNamespace(
            message_id=1,
            text="hello",
            photo=None,
            video=None,
            document=None,
            animation=None,
            voice=None,
            video_note=None,
            audio=None,
            sticker=None,
            caption=None,
        )
        update = MagicMock()
        update.effective_message = message
        update.effective_chat = chat
        update.effective_user = user
        context = MagicMock()
        context.chat_data = {}
        context.bot = MagicMock()
        context.job_queue = MagicMock()

        with patch.object(ga_handler, "get_club_for_chat", return_value=1):
            with patch.object(pk, "popup_keyboard_eligible", return_value=True):
                with patch.object(
                    esc, "escalation_notification_eligible", return_value=False
                ):
                    with patch.object(ga, "is_support_sender", return_value=False):
                        with patch.object(pk, "upsert_player_telegram_user_id"):
                            with patch.object(pk, "remember_player_message"):
                                with patch.object(
                                    ga,
                                    "record_human_message",
                                    return_value=SimpleNamespace(
                                        should_fire_idle=False
                                    ),
                                ):
                                    with patch.object(
                                        pk,
                                        "silent_strip_if_installed",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            pk,
                                            "payment_window_gate_pending",
                                            return_value=False,
                                        ):
                                            with patch.object(
                                                pk, "schedule_popup_keyboard_idle"
                                            ) as schedule:
                                                await ga_handler.group_activity_handler(
                                                    update, context
                                                )

        schedule.assert_called_once()


class IdleHelpEntryPointTests(unittest.TestCase):
    def test_deposit_handler_has_idlehelp_callback_entry(self):
        handler = get_deposit_handler()
        patterns = []
        for ep in handler.entry_points:
            pattern = getattr(ep, "pattern", None)
            if pattern is not None:
                patterns.append(getattr(pattern, "pattern", str(pattern)))
        self.assertTrue(any("idlehelp:deposit" in p for p in patterns))

    def test_cashout_handler_has_idlehelp_callback_entry(self):
        handler = get_cashout_handler()
        patterns = []
        for ep in handler.entry_points:
            pattern = getattr(ep, "pattern", None)
            if pattern is not None:
                patterns.append(getattr(pattern, "pattern", str(pattern)))
        self.assertTrue(any("idlehelp:cashout" in p for p in patterns))



class IdleHelpCashoutDenyTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_idlehelp_cashout_slacks_player_idle(self):
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_chat = SimpleNamespace(id=-100, title="CC / 1 / P", type="supergroup")
        update.effective_user = SimpleNamespace(id=55)
        update.effective_message = MagicMock()
        update.message = None

        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 3,
            "idle_help_title": "CC / 1 / P",
            "idle_help_message_text": "yo",
            "idle_help_prompt_message_id": 9,
        }

        async def _deny_entry(upd, ctx):
            ctx.chat_data["cashout_entry_denied"] = True
            return ConversationHandler.END

        with patch(
            "bot.handlers.cashout.cashout_entry",
            new=AsyncMock(side_effect=_deny_entry),
        ):
            with patch(
                "bot.services.escalation_notification.notify_escalation_slack",
                new_callable=AsyncMock,
            ) as slack:
                result = await idlehelp_cashout_entry(update, context)

        self.assertEqual(result, ConversationHandler.END)
        slack.assert_awaited_once()
        self.assertEqual(slack.await_args.args[0], esc.REASON_PLAYER_IDLE)
        self.assertEqual(slack.await_args.kwargs["message_text"], "yo")
        self.assertEqual(slack.await_args.kwargs["club_id"], 3)
        self.assertNotIn("idle_help_message_text", context.chat_data)

    async def test_allowed_idlehelp_cashout_does_not_slack_player_idle(self):
        query = MagicMock()
        query.answer = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_chat = SimpleNamespace(id=-100, title="G", type="supergroup")
        context = MagicMock()
        context.chat_data = {
            "idle_help_club_id": 3,
            "idle_help_message_text": "yo",
        }

        with patch(
            "bot.handlers.cashout.cashout_entry",
            new_callable=AsyncMock,
            return_value=ConversationHandler.END,
        ):
            with patch(
                "bot.services.escalation_notification.notify_escalation_slack",
                new_callable=AsyncMock,
            ) as slack:
                await idlehelp_cashout_entry(update, context)

        slack.assert_not_awaited()

if __name__ == "__main__":
    unittest.main()
