"""The /transfer conversation: gating, actor rules, off-script escalation, copy."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot.handlers import transfer as tr

RT_ID = 2
CC_ID = 3
CHAT_ID = -100999
PLAYER_ID = 555
ADMIN_ID = 111
STAFF_ID = 222


_CLUBGG_NAMES = {"RT": "Round Table", "AT": "Aces Table", "CC": "Creator Club"}

_UNION_PAIR = (
    {"shorthand": "RT", "label": "Round Table (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)


def _plan(club_id=RT_ID, src="RT", dst="AT"):
    return SimpleNamespace(
        club_id=club_id,
        chat_id=CHAT_ID,
        source_shorthand=src,
        destination_shorthand=dst,
        source_label=f"{_CLUBGG_NAMES[src]} (Union)",
        destination_label=f"{_CLUBGG_NAMES[dst]} (Union)",
        source_clubgg=_CLUBGG_NAMES[src],
        destination_clubgg=_CLUBGG_NAMES[dst],
    )


def _chat():
    return SimpleNamespace(
        id=CHAT_ID,
        type="supergroup",
        title="RT AT / 1234-5678 / Player",
        send_message=AsyncMock(),
    )


def _command_update(user_id=PLAYER_ID):
    chat = _chat()
    message = SimpleNamespace(
        chat=chat,
        date=datetime.now(timezone.utc),
        message_id=10,
        text="/transfer",
        reply_text=AsyncMock(return_value=SimpleNamespace(message_id=11)),
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )


def _text_update(text, user_id=PLAYER_ID):
    chat = _chat()
    message = SimpleNamespace(
        chat=chat,
        date=datetime.now(timezone.utc),
        message_id=12,
        text=text,
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
    )


def _callback_update(data="trdest:AT", user_id=PLAYER_ID):
    chat = _chat()
    message = SimpleNamespace(
        chat=chat, date=datetime.now(timezone.utc), message_id=11
    )
    query = SimpleNamespace(
        data=data,
        message=message,
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=user_id),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        message=None,
        effective_message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=user_id),
        callback_query=query,
    )


def _context(**chat_data):
    return SimpleNamespace(
        chat_data=dict(chat_data),
        user_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
        job_queue=None,
    )


class EntryGatingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for target, kwargs in (
            ("is_update_too_old", {"return_value": False}),
            ("get_club_for_chat", {"return_value": RT_ID}),
            ("get_transfer_enabled", {"return_value": True}),
            ("get_club_allows_admin_commands", {"return_value": True}),
            ("transfer_blocked_reason", {"return_value": None}),
            ("deposit_unions_for_club", {"return_value": _UNION_PAIR}),
            ("mark_active_flow", {"return_value": None}),
            ("register_flow_callback_message", {"return_value": None}),
            ("get_group_title_for_chat", {"return_value": ("RT AT / 1-2 / P", RT_ID)}),
        ):
            p = patch.object(tr, target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(
            tr, "block_if_group_money_flow_active", AsyncMock(return_value=False)
        )
        p.start()
        self.addCleanup(p.stop)

    async def test_player_entry_prompts_for_destination(self):
        update = _command_update()
        context = _context()
        result = await tr.transfer_entry(update, context)

        self.assertEqual(result, tr.TRANSFER_DEST)
        text, kwargs = update.message.reply_text.await_args
        self.assertIn("transfer your chips to", text[0])
        labels = [
            row[0].text for row in kwargs["reply_markup"].inline_keyboard
        ]
        self.assertEqual(len(labels), 2)
        self.assertEqual(context.chat_data["transfer_user_id"], PLAYER_ID)

    async def test_flag_off_is_a_silent_no_op(self):
        with patch.object(tr, "get_transfer_enabled", return_value=False):
            update = _command_update()
            result = await tr.transfer_entry(update, _context())
        self.assertEqual(result, ConversationHandler.END)
        update.message.reply_text.assert_not_awaited()

    async def test_club_without_unions_is_a_silent_no_op(self):
        with patch.object(tr, "get_club_for_chat", return_value=4), patch.object(
            tr, "deposit_unions_for_club", return_value=None
        ):
            update = _command_update()
            result = await tr.transfer_entry(update, _context())
        self.assertEqual(result, ConversationHandler.END)
        update.message.reply_text.assert_not_awaited()

    async def test_unlinked_chat_is_a_silent_no_op(self):
        with patch.object(tr, "get_club_for_chat", return_value=None):
            result = await tr.transfer_entry(_command_update(), _context())
        self.assertEqual(result, ConversationHandler.END)

    async def test_private_chat_is_a_silent_no_op(self):
        update = _command_update()
        update.effective_chat.type = "private"
        result = await tr.transfer_entry(update, _context())
        self.assertEqual(result, ConversationHandler.END)

    async def test_setup_problem_escalates_before_anything_moves(self):
        with patch.object(
            tr, "transfer_blocked_reason", return_value="auto claim is disabled"
        ), patch(
            "bot.services.escalation_notification.notify_transfer_escalation",
            AsyncMock(),
        ) as mock_notify:
            update = _command_update()
            result = await tr.transfer_entry(update, _context())

        self.assertEqual(result, ConversationHandler.END)
        update.effective_chat.send_message.assert_awaited_once_with(
            tr.AGENT_SHORTLY_COPY
        )
        self.assertIn("auto claim is disabled", mock_notify.await_args.kwargs["detail"])
        # No chips were claimed, so no owed amount is reported.
        self.assertIsNone(mock_notify.await_args.kwargs["claimed_amount"])

    async def test_admin_entry_does_not_pin_the_customer(self):
        update = _command_update(user_id=ADMIN_ID)
        context = _context()
        with patch.object(tr, "ADMIN_USER_IDS", [ADMIN_ID]):
            result = await tr.transfer_entry(update, context)
        self.assertEqual(result, tr.TRANSFER_DEST)
        self.assertTrue(context.chat_data["transfer_admin_initiated"])
        self.assertIsNone(context.chat_data.get("transfer_user_id"))

    async def test_another_open_money_flow_blocks_entry(self):
        with patch.object(
            tr, "block_if_group_money_flow_active", AsyncMock(return_value=True)
        ):
            result = await tr.transfer_entry(_command_update(), _context())
        self.assertEqual(result, ConversationHandler.END)


class DestinationChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        p = patch.object(
            tr, "handle_stale_flow_callback", AsyncMock(return_value=False)
        )
        p.start()
        self.addCleanup(p.stop)

    async def test_choice_prompts_for_amount_naming_the_destination(self):
        update = _callback_update("trdest:AT")
        context = _context(transfer_club_id=RT_ID, transfer_chat_id=CHAT_ID)
        with patch.object(tr, "build_transfer_plan", return_value=_plan()):
            result = await tr.transfer_dest_chosen(update, context)

        self.assertEqual(result, tr.TRANSFER_AMOUNT)
        self.assertEqual(context.chat_data["transfer_destination"], "AT")
        text, _kwargs = update.callback_query.edit_message_text.await_args
        self.assertIn("Aces Table", text[0])

    async def test_unresolvable_destination_ends_the_flow(self):
        update = _callback_update("trdest:RT")
        context = _context(transfer_club_id=CC_ID, transfer_chat_id=CHAT_ID)
        with patch.object(tr, "build_transfer_plan", return_value=None):
            result = await tr.transfer_dest_chosen(update, context)

        self.assertEqual(result, ConversationHandler.END)
        text, _kwargs = update.callback_query.edit_message_text.await_args
        self.assertIn("no longer available", text[0])

    async def test_expired_session_ends_the_flow(self):
        result = await tr.transfer_dest_chosen(_callback_update(), _context())
        self.assertEqual(result, ConversationHandler.END)


class AmountAndRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for target, kwargs in (
            ("is_update_too_old", {"return_value": False}),
            ("set_aces_join_ack", {"return_value": None}),
            ("is_creator_club", {"return_value": False}),
        ):
            p = patch.object(tr, target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def _ready_context(self):
        return _context(
            transfer_club_id=RT_ID,
            transfer_chat_id=CHAT_ID,
            transfer_user_id=PLAYER_ID,
            transfer_destination="AT",
        )

    async def test_success_posts_all_three_messages_in_order(self):
        update = _text_update("200")
        context = self._ready_context()
        result = SimpleNamespace(
            ok=True, failed_leg=None, reason="", claimed_amount=Decimal("200")
        )

        async def fake_run(*, on_claimed=None, **_kw):
            await on_claimed()
            return result

        with patch.object(tr, "build_transfer_plan", return_value=_plan()), patch.object(
            tr, "run_transfer", AsyncMock(side_effect=fake_run)
        ):
            out = await tr.transfer_amount_received(update, context)

        self.assertEqual(out, ConversationHandler.END)
        posted = [c.args[0] for c in update.effective_chat.send_message.await_args_list]
        self.assertEqual(len(posted), 3)
        self.assertIn("Claiming 200 chips from Round Table", posted[0])
        self.assertIn("this will just take a minute!", posted[0])
        self.assertIn("Adding 200 chips to Aces Table", posted[1])
        self.assertEqual(
            posted[2],
            "Successfully transferred 200 chips from Round Table to Aces Table!",
        )

    async def test_failed_transfer_escalates_with_limbo_context(self):
        update = _text_update("200")
        context = self._ready_context()
        failed = SimpleNamespace(
            ok=False,
            failed_leg="add",
            reason="Add to Aces Table failed (fail).",
            claimed_amount=Decimal("200"),
        )
        with patch.object(tr, "build_transfer_plan", return_value=_plan()), patch.object(
            tr, "run_transfer", AsyncMock(return_value=failed)
        ), patch(
            "bot.services.escalation_notification.notify_transfer_escalation",
            AsyncMock(),
        ) as mock_notify:
            out = await tr.transfer_amount_received(update, context)

        self.assertEqual(out, ConversationHandler.END)
        posted = [c.args[0] for c in update.effective_chat.send_message.await_args_list]
        self.assertEqual(posted[-1], tr.AGENT_SHORTLY_COPY)
        kwargs = mock_notify.await_args.kwargs
        self.assertEqual(kwargs["claimed_amount"], Decimal("200"))
        self.assertEqual(kwargs["source_club"], "Round Table")
        self.assertEqual(kwargs["destination_club"], "Aces Table")

    async def test_wrong_sender_amount_is_ignored(self):
        update = _text_update("200", user_id=999)
        context = self._ready_context()
        with patch.object(tr, "run_transfer", AsyncMock()) as mock_run:
            out = await tr.transfer_amount_received(update, context)
        self.assertEqual(out, tr.TRANSFER_AMOUNT)
        mock_run.assert_not_awaited()

    async def test_admin_started_flow_accepts_the_customers_amount(self):
        """Admin runs /transfer, the customer answers — the transfer must run."""
        update = _text_update("50", user_id=PLAYER_ID)
        context = _context(
            transfer_club_id=RT_ID,
            transfer_chat_id=CHAT_ID,
            transfer_destination="AT",
            transfer_admin_initiated=True,
        )
        ok = SimpleNamespace(
            ok=True, failed_leg=None, reason="", claimed_amount=Decimal("50")
        )
        with patch.object(tr, "build_transfer_plan", return_value=_plan()), patch.object(
            tr, "run_transfer", AsyncMock(return_value=ok)
        ) as mock_run:
            out = await tr.transfer_amount_received(update, context)

        self.assertEqual(out, ConversationHandler.END)
        self.assertEqual(mock_run.await_args.kwargs["amount"], Decimal("50"))

    async def test_admin_started_flow_ignores_an_admins_own_amount(self):
        update = _text_update("50", user_id=ADMIN_ID)
        context = _context(
            transfer_club_id=RT_ID,
            transfer_chat_id=CHAT_ID,
            transfer_destination="AT",
            transfer_admin_initiated=True,
        )
        with patch(
            "bot.handlers.flow_staleness.ADMIN_USER_IDS", [ADMIN_ID]
        ), patch.object(tr, "run_transfer", AsyncMock()) as mock_run:
            out = await tr.transfer_amount_received(update, context)

        self.assertEqual(out, tr.TRANSFER_AMOUNT)
        mock_run.assert_not_awaited()

    async def test_transfer_into_aces_records_the_ack_for_creator_club(self):
        update = _text_update("75")
        context = _context(
            transfer_club_id=CC_ID,
            transfer_chat_id=CHAT_ID,
            transfer_user_id=PLAYER_ID,
            transfer_destination="AT",
        )
        ok = SimpleNamespace(
            ok=True, failed_leg=None, reason="", claimed_amount=Decimal("75")
        )
        with patch.object(tr, "is_creator_club", return_value=True), patch.object(
            tr, "set_aces_join_ack"
        ) as mock_ack, patch.object(
            tr, "build_transfer_plan", return_value=_plan(CC_ID, "CC", "AT")
        ), patch.object(tr, "run_transfer", AsyncMock(return_value=ok)):
            await tr.transfer_amount_received(update, context)
        mock_ack.assert_called_once_with(CHAT_ID)

    async def test_transfer_out_of_aces_does_not_record_the_ack(self):
        update = _text_update("75")
        context = _context(
            transfer_club_id=CC_ID,
            transfer_chat_id=CHAT_ID,
            transfer_user_id=PLAYER_ID,
            transfer_destination="CC",
        )
        ok = SimpleNamespace(
            ok=True, failed_leg=None, reason="", claimed_amount=Decimal("75")
        )
        with patch.object(tr, "is_creator_club", return_value=True), patch.object(
            tr, "set_aces_join_ack"
        ) as mock_ack, patch.object(
            tr, "build_transfer_plan", return_value=_plan(CC_ID, "AT", "CC")
        ), patch.object(tr, "run_transfer", AsyncMock(return_value=ok)):
            await tr.transfer_amount_received(update, context)
        mock_ack.assert_not_called()

    async def test_module_never_records_player_activity(self):
        """Transfers must not reset cooldowns or inflate deposit thresholds."""
        self.assertNotIn("record_activity", dir(tr))
        self.assertNotIn("record_activity_for_chat", dir(tr))


class OffScriptTests(unittest.IsolatedAsyncioTestCase):
    def _context(self):
        return _context(
            transfer_club_id=RT_ID,
            transfer_chat_id=CHAT_ID,
            transfer_user_id=PLAYER_ID,
            transfer_destination="AT",
        )

    async def test_player_chatter_escalates_once_and_ends(self):
        update = _text_update("do i get a bonus?")
        with patch.object(tr, "build_transfer_plan", return_value=_plan()), patch.object(
            tr, "is_club_staff", return_value=False
        ), patch(
            "bot.services.escalation_notification.notify_transfer_escalation",
            AsyncMock(),
        ) as mock_notify:
            out = await tr.transfer_offscript(update, self._context())

        self.assertEqual(out, ConversationHandler.END)
        posted = [c.args[0] for c in update.effective_chat.send_message.await_args_list]
        self.assertEqual(posted, [tr.AGENT_SHORTLY_COPY])
        self.assertIn("Off-script", mock_notify.await_args.kwargs["detail"])
        # Nothing was claimed at this point.
        self.assertIsNone(mock_notify.await_args.kwargs["claimed_amount"])

    async def test_admin_message_is_ignored(self):
        update = _text_update("handling this", user_id=ADMIN_ID)
        with patch.object(tr, "ADMIN_USER_IDS", [ADMIN_ID]):
            out = await tr.transfer_offscript(update, self._context())
        self.assertIsNone(out)
        update.effective_chat.send_message.assert_not_awaited()

    async def test_club_staff_message_is_ignored(self):
        update = _text_update("on it", user_id=STAFF_ID)
        with patch.object(tr, "ADMIN_USER_IDS", []), patch.object(
            tr, "is_club_staff", return_value=True
        ):
            out = await tr.transfer_offscript(update, self._context())
        self.assertIsNone(out)
        update.effective_chat.send_message.assert_not_awaited()

    async def test_other_player_message_is_ignored(self):
        update = _text_update("hi", user_id=777)
        with patch.object(tr, "ADMIN_USER_IDS", []), patch.object(
            tr, "is_club_staff", return_value=False
        ):
            out = await tr.transfer_offscript(update, self._context())
        self.assertIsNone(out)
        update.effective_chat.send_message.assert_not_awaited()

    async def test_no_active_transfer_is_ignored(self):
        out = await tr.transfer_offscript(_text_update("hello"), _context())
        self.assertIsNone(out)


class IdleEscalationTests(unittest.TestCase):
    def test_transfer_counts_as_a_flow_command(self):
        """Otherwise a bare /transfer escalates as ordinary player chatter."""
        from bot.services.popup_keyboard import is_flow_command_text

        self.assertTrue(is_flow_command_text("/transfer"))
        self.assertTrue(is_flow_command_text("/transfer@GGSupportBot"))


class HandlerShapeTests(unittest.TestCase):
    def setUp(self):
        self.handler = tr.get_transfer_handler()

    def test_transfer_command_is_the_only_entry_point(self):
        commands = [
            c for ep in self.handler.entry_points for c in getattr(ep, "commands", [])
        ]
        self.assertEqual(set(commands), {"transfer"})

    def test_both_states_have_an_offscript_catch_all(self):
        for state in (tr.TRANSFER_DEST, tr.TRANSFER_AMOUNT):
            callbacks = [
                getattr(h, "callback", None) for h in self.handler.states[state]
            ]
            self.assertIn(
                tr.transfer_offscript,
                callbacks,
                f"state {state} must escalate off-script input",
            )

    def test_commands_never_reach_the_offscript_handler(self):
        """A stray /transfer or /cancel must not be treated as off-script."""
        for state in (tr.TRANSFER_DEST, tr.TRANSFER_AMOUNT):
            for h in self.handler.states[state]:
                if getattr(h, "callback", None) is tr.transfer_offscript:
                    self.assertNotIn(
                        "/transfer",
                        repr(h.filters),
                        "off-script handler must exclude commands",
                    )
                    self.assertIn("COMMAND", repr(h.filters).upper())


if __name__ == "__main__":
    unittest.main()
