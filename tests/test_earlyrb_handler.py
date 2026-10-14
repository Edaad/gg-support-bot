"""Tests for the /earlyrb entry point.

Early feeback has **no** daily limit: a player may claim as often as they have
feeback remaining, and Elevate's ``nothing_remaining`` is what stops a second
claim. The only spacing is the short re-check throttle that protects the
single-threaded screen robot.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.earlyrb import (
    ADDING_COPY,
    ADDING_IN_PROGRESS_COPY,
    EARLYRB_CONFIRM,
    EARLYRB_ELIGIBLE_MESSAGE,
    earlyrb_cancel,
    earlyrb_claim,
    earlyrb_entry,
)
from telegram.ext import ConversationHandler


class CannedFallbackTestCase(unittest.IsolatedAsyncioTestCase):
    """With the auto toggle off (or either API unconfigured) nothing changed."""

    def _make_update(self, *, user_id: int = 111, chat_id: int = -100):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        update.effective_chat.type = "supergroup"
        update.effective_chat.title = "Player 12345"
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        return update

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_records_and_replies(
        self, _club, _rename, mock_record, _auto
    ) -> None:
        update = self._make_update()

        await earlyrb_entry(update, MagicMock())

        mock_record.assert_called_once_with(1, 111, update.effective_chat.id, "earlyrb")
        update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch("bot.handlers.earlyrb.record_activity")
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_back_to_back_requests_are_both_allowed(
        self, _club, _rename, _record, _auto
    ) -> None:
        """No 24h gate: a second /earlyrb seconds later is answered the same way."""
        for _ in range(2):
            update = self._make_update()
            await earlyrb_entry(update, MagicMock())
            update.message.reply_text.assert_called_once_with(EARLYRB_ELIGIBLE_MESSAGE)

    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=False)
    @patch("bot.handlers.earlyrb.record_activity", side_effect=RuntimeError("db down"))
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_record_failure_does_not_send_success(
        self, _club, _rename, mock_record, _auto
    ) -> None:
        update = self._make_update()

        await earlyrb_entry(update, MagicMock())

        mock_record.assert_called_once()
        update.message.reply_text.assert_called_once()
        self.assertNotIn(
            "We're checking your early rakeback",
            update.message.reply_text.call_args[0][0],
        )

    async def test_non_group_chat_is_refused(self) -> None:
        update = self._make_update()
        update.effective_chat.type = "private"

        state = await earlyrb_entry(update, MagicMock())

        self.assertEqual(state, ConversationHandler.END)
        self.assertIn("club group", update.message.reply_text.call_args[0][0])


class CopyTestCase(unittest.TestCase):
    def test_canned_message_keeps_the_cashout_timer_warning(self) -> None:
        self.assertIn(
            "Early feeback counts as a deposit and will reset the cashout timer",
            EARLYRB_ELIGIBLE_MESSAGE,
        )

    def test_canned_message_claims_no_daily_limit(self) -> None:
        lowered = EARLYRB_ELIGIBLE_MESSAGE.lower()
        self.assertNotIn("24 hour", lowered)
        self.assertNotIn("once every", lowered)


class RecheckThrottleTestCase(unittest.IsolatedAsyncioTestCase):
    """The throttle only spaces out fee lookups; it is not a daily limit."""

    def _make_update(self):
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        update.effective_chat.type = "supergroup"
        update.effective_chat.title = "Player 12345"
        update.effective_user = MagicMock()
        update.effective_user.id = 111
        return update

    @patch("bot.handlers.earlyrb.check_earlyrb_recheck_throttle")
    @patch(
        "bot.handlers.earlyrb.block_if_group_money_flow_active",
        new_callable=AsyncMock,
    )
    @patch("bot.handlers.earlyrb.auto_earlyrb_available", return_value=True)
    @patch("bot.handlers.earlyrb.update_group_name")
    @patch("bot.handlers.earlyrb.get_club_for_chat", return_value=1)
    async def test_throttled_lookup_ends_the_flow(
        self, _club, _rename, _auto, mock_block, mock_throttle
    ) -> None:
        mock_block.return_value = False
        mock_throttle.return_value = (False, "please wait a few minutes")
        update = self._make_update()

        state = await earlyrb_entry(update, MagicMock())

        self.assertEqual(state, ConversationHandler.END)
        update.message.reply_text.assert_called_once_with("please wait a few minutes")


class ClaimStatusMessageTests(unittest.IsolatedAsyncioTestCase):
    def _make(self):
        status = MagicMock()
        status.edit_text = AsyncMock()
        chat = MagicMock()
        chat.id = -100
        chat.send_message = AsyncMock(return_value=status)
        update = MagicMock()
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()
        update.effective_chat = chat
        update.effective_user = MagicMock()
        update.effective_user.id = 111
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_user_id": 111,
            "earlyrb_title": "RT / 8272-5942 / P",
            "earlyrb_fee": MagicMock(),
            "earlyrb_quote": MagicMock(nickname="P"),
            "earlyrb_target": MagicMock(),
            "earlyrb_player_id": "8272-5942",
        }
        return update, context, chat, status

    async def test_adding_status_is_replaced_with_the_result(self) -> None:
        update, context, chat, status = self._make()
        result = SimpleNamespace(
            kind="added",
            player_message="$12.00 feeback added to your account!",
            quote=None,
        )
        with (
            patch(
                "bot.handlers.earlyrb.handle_stale_flow_callback",
                AsyncMock(return_value=False),
            ),
            patch(
                "bot.services.early_rakeback_auto.claim_feeback",
                AsyncMock(return_value=result),
            ),
            patch("bot.services.early_rakeback_auto.create_claim_row", return_value=7),
            patch(
                "bot.services.early_rakeback_auto.new_idempotency_key", return_value="k"
            ),
        ):
            state = await earlyrb_claim(update, context)

        self.assertEqual(state, ConversationHandler.END)
        chat.send_message.assert_awaited_once_with(ADDING_COPY)
        status.edit_text.assert_awaited_once_with(
            "$12.00 feeback added to your account!"
        )


class LookupCancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_lookup_does_not_prompt_claim(self) -> None:
        from bot.handlers.earlyrb import _cleanup, _run_lookup
        from bot.services.early_rakeback_auto import FINDING_FEE_COPY, CALCULATING_COPY

        chat = MagicMock()
        chat.send_message = AsyncMock()
        update = MagicMock()
        update.effective_chat = chat
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_user_id": 111,
            "earlyrb_title": "t",
        }

        async def fee_then_cancel(**_kwargs):
            _cleanup(context)
            return SimpleNamespace(kind="ok", fee=MagicMock(), detail="")

        with (
            patch("bot.handlers.earlyrb.record_activity_for_chat"),
            patch("bot.services.early_rakeback_auto.check_fee", fee_then_cancel),
        ):
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(FINDING_FEE_COPY, sent)
        self.assertNotIn(CALCULATING_COPY, sent)


class UplineLookupTests(unittest.IsolatedAsyncioTestCase):
    def _make(self):
        chat = MagicMock()
        chat.send_message = AsyncMock()
        update = MagicMock()
        update.effective_chat = chat
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_user_id": 111,
            "earlyrb_title": "RT / 8272-5942 / P",
            "earlyrb_union_shorthand": "RT",
        }
        return update, context, chat

    def _fee(self, **kwargs):
        from decimal import Decimal

        from bot.services.clubgg_deposit_api import RakeOutcome

        defaults = dict(
            ok=True,
            status="success",
            reason="",
            player_id="8272-5942",
            clubgg_club="Round Table",
            rake_overall=Decimal("1200"),
            rake_filtered=Decimal("400"),
            pnl_overall=Decimal("-900"),
            pnl_filtered=Decimal("-250"),
            range_start="2026-09-14",
            range_end="2026-09-17",
            has_upline=True,
            job_id="job-1",
        )
        defaults.update(kwargs)
        return RakeOutcome(**defaults)

    def _stack(self, *, fee, quote_stage=None, gate=None):
        from contextlib import ExitStack

        from bot.handlers.earlyrb import EARLYRB_CONFIRM
        from bot.services.early_rakeback_auto import (
            ClubTarget,
            FeeStage,
            QuoteStage,
            UplineGate,
        )

        if quote_stage is None:
            quote_stage = QuoteStage(
                "eligible",
                SimpleNamespace(source="standard_player", remaining=12, nickname="P"),
            )
        if gate is None:
            gate = UplineGate("block")

        stack = ExitStack()
        stack.enter_context(patch("bot.handlers.earlyrb.record_activity_for_chat"))
        stack.enter_context(
            patch(
                "bot.services.early_rakeback_auto.check_fee",
                AsyncMock(return_value=FeeStage("has_upline", fee)),
            )
        )
        mocks = {
            "quote": stack.enter_context(
                patch(
                    "bot.services.early_rakeback_auto.quote_feeback",
                    AsyncMock(return_value=quote_stage),
                )
            ),
            "gate": stack.enter_context(
                patch(
                    "bot.services.early_rakeback_auto.gate_upline_claim",
                    AsyncMock(return_value=gate),
                )
            ),
        }
        stack.enter_context(
            patch(
                "bot.handlers.earlyrb.get_club_by_id",
                return_value=SimpleNamespace(name="Round Table"),
            )
        )
        stack.enter_context(
            patch(
                "bot.services.early_rakeback_auto.resolve_club_target",
                return_value=ClubTarget("Round Table", "round-table", "RT"),
            )
        )
        stack.enter_context(
            patch("bot.services.early_rakeback_auto.escalate_fee_stage", AsyncMock())
        )
        stack.enter_context(
            patch(
                "bot.handlers.earlyrb._prompt_claim",
                AsyncMock(return_value=EARLYRB_CONFIRM),
            )
        )
        return stack, mocks

    async def test_blocked_upline_gets_the_agency_copy(self) -> None:
        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import UPLINE_INELIGIBLE_COPY

        update, context, chat = self._make()
        stack, _mocks = self._stack(fee=self._fee())
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(UPLINE_INELIGIBLE_COPY, sent)

    async def test_allowed_upline_prompts_claim(self) -> None:
        from bot.handlers.earlyrb import EARLYRB_CONFIRM, _run_lookup
        from bot.services.early_rakeback_auto import (
            QuoteStage,
            UPLINE_INELIGIBLE_COPY,
            UplineGate,
        )

        update, context, chat = self._make()
        quote = SimpleNamespace(source="custom_player", remaining=12, nickname="P")
        stack, _mocks = self._stack(
            fee=self._fee(),
            quote_stage=QuoteStage("eligible", quote),
            gate=UplineGate("allow"),
        )
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, EARLYRB_CONFIRM)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertNotIn(UPLINE_INELIGIBLE_COPY, sent)

    async def test_zero_fee_allowed_is_no_fee(self) -> None:
        from decimal import Decimal

        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import NO_FEE_COPY, UplineGate

        update, context, chat = self._make()
        stack, mocks = self._stack(
            fee=self._fee(rake_filtered=Decimal("0")),
            gate=UplineGate("allow"),
        )
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(NO_FEE_COPY, sent)
        mocks["quote"].assert_not_awaited()

    async def test_zero_fee_blocked_is_still_agency_copy(self) -> None:
        from decimal import Decimal

        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import UPLINE_INELIGIBLE_COPY, UplineGate

        update, context, chat = self._make()
        stack, _mocks = self._stack(
            fee=self._fee(rake_filtered=Decimal("0")),
            gate=UplineGate("block"),
        )
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(UPLINE_INELIGIBLE_COPY, sent)

    async def test_quote_http_failure_escalates_without_agency(self) -> None:
        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import ADMIN_SHORTLY_COPY, QuoteStage

        update, context, chat = self._make()
        stack, mocks = self._stack(
            fee=self._fee(),
            quote_stage=QuoteStage(
                "escalate", detail="quote failed (request_failed): x"
            ),
        )
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(ADMIN_SHORTLY_COPY, sent)
        mocks["gate"].assert_not_awaited()

    async def test_suspect_dates_on_allowed_upline_escalate(self) -> None:
        from decimal import Decimal

        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import ADMIN_SHORTLY_COPY, UplineGate

        update, context, chat = self._make()
        fee = self._fee(
            rake_overall=Decimal("400"),
            rake_filtered=Decimal("400"),
            pnl_overall=Decimal("-9"),
            pnl_filtered=Decimal("-9"),
        )
        stack, _mocks = self._stack(fee=fee, gate=UplineGate("allow"))
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(ADMIN_SHORTLY_COPY, sent)

    async def test_suspect_dates_on_blocked_upline_stay_agency_copy(self) -> None:
        from decimal import Decimal

        from bot.handlers.earlyrb import _run_lookup
        from bot.services.early_rakeback_auto import UPLINE_INELIGIBLE_COPY, UplineGate

        update, context, chat = self._make()
        fee = self._fee(
            rake_overall=Decimal("400"),
            rake_filtered=Decimal("400"),
            pnl_overall=Decimal("-9"),
            pnl_filtered=Decimal("-9"),
        )
        stack, _mocks = self._stack(fee=fee, gate=UplineGate("block"))
        with stack:
            state = await _run_lookup(update, context)

        self.assertEqual(state, ConversationHandler.END)
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn(UPLINE_INELIGIBLE_COPY, sent)


class CancelDuringAddTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_chip_add_is_refused(self) -> None:
        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.chat_data = {
            "earlyrb_club_id": 1,
            "earlyrb_chat_id": -100,
            "earlyrb_committing": True,
        }

        state = await earlyrb_cancel(update, context)

        self.assertEqual(state, EARLYRB_CONFIRM)
        update.message.reply_text.assert_awaited_once_with(ADDING_IN_PROGRESS_COPY)
        self.assertEqual(context.chat_data.get("earlyrb_club_id"), 1)


class HandlerRegistrationTests(unittest.TestCase):
    def test_long_rpa_callbacks_are_non_blocking(self) -> None:
        from bot.handlers.earlyrb import get_earlyrb_handler

        handler = get_earlyrb_handler()
        self.assertFalse(handler.entry_points[0].block)
        union_cb = handler.states[0][0]
        self.assertFalse(union_cb.block)
        confirm_cbs = handler.states[1]
        self.assertFalse(confirm_cbs[0].block)
        self.assertIn(ConversationHandler.WAITING, handler.states)


def _button_update(data: str, message_id: int = 50):
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.message = MagicMock()
    update.callback_query.message.message_id = message_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = -100
    return update


class OrphanButtonTests(unittest.IsolatedAsyncioTestCase):
    """A Claim tap the conversation skipped (e.g. after a restart) must answer."""

    async def test_claim_after_restart_says_expired_and_drops_buttons(self) -> None:
        from bot.handlers.earlyrb import earlyrb_orphan_button

        update = _button_update("ebclaim")
        context = MagicMock()
        context.chat_data = {}

        await earlyrb_orphan_button(update, context)

        update.callback_query.answer.assert_awaited_once()
        kwargs = update.callback_query.answer.await_args.kwargs
        self.assertTrue(kwargs.get("show_alert"))
        self.assertIn("/earlyrb", update.callback_query.answer.await_args.args[0])
        update.callback_query.edit_message_reply_markup.assert_awaited_once_with(
            reply_markup=None
        )

    async def test_live_prompt_tapped_while_busy_is_left_alone(self) -> None:
        from bot.handlers.earlyrb import earlyrb_orphan_button
        from bot.handlers.flow_staleness import register_flow_callback_message

        update = _button_update("ebunion:RT", message_id=50)
        context = MagicMock()
        context.chat_data = {"earlyrb_club_id": 1}
        register_flow_callback_message(context, 50, flow="earlyrb")

        await earlyrb_orphan_button(update, context)

        update.callback_query.answer.assert_awaited_once_with()
        update.callback_query.edit_message_reply_markup.assert_not_awaited()

    async def test_old_prompt_during_a_new_session_says_expired(self) -> None:
        from bot.handlers.earlyrb import earlyrb_orphan_button
        from bot.handlers.flow_staleness import register_flow_callback_message

        update = _button_update("ebclaim", message_id=40)
        context = MagicMock()
        context.chat_data = {"earlyrb_club_id": 1}
        register_flow_callback_message(context, 50, flow="earlyrb")

        await earlyrb_orphan_button(update, context)

        self.assertTrue(update.callback_query.answer.await_args.kwargs["show_alert"])
        update.callback_query.edit_message_reply_markup.assert_awaited_once()

    def test_pattern_covers_every_earlyrb_button(self) -> None:
        from bot.handlers.earlyrb import get_earlyrb_orphan_handler

        handler = get_earlyrb_orphan_handler()
        for data in ("ebclaim", "ebcancel", "ebunion:RT", "ebunion:AT"):
            self.assertTrue(handler.pattern.match(data), data)
        self.assertIsNone(handler.pattern.match("dpclaim"))


class ButtonsRemovedOnEndTests(unittest.IsolatedAsyncioTestCase):
    def _context(self):
        from bot.handlers.flow_staleness import register_flow_callback_message

        context = MagicMock()
        context.bot.edit_message_reply_markup = AsyncMock()
        context.chat_data = {"earlyrb_club_id": 1, "earlyrb_chat_id": -100}
        register_flow_callback_message(context, 77, flow="earlyrb")
        return context

    async def test_timeout_removes_the_claim_buttons(self) -> None:
        from bot.handlers.earlyrb import TIMEOUT_COPY, earlyrb_timeout

        context = self._context()
        update = MagicMock()
        update.effective_chat.send_message = AsyncMock()

        await earlyrb_timeout(update, context)

        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=-100, message_id=77, reply_markup=None
        )
        update.effective_chat.send_message.assert_awaited_once_with(TIMEOUT_COPY)
        self.assertNotIn("earlyrb_club_id", context.chat_data)

    async def test_cancel_removes_the_claim_buttons(self) -> None:
        context = self._context()
        update = MagicMock()
        update.message.reply_text = AsyncMock()

        state = await earlyrb_cancel(update, context)

        self.assertEqual(state, ConversationHandler.END)
        context.bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=-100, message_id=77, reply_markup=None
        )

    def test_timeout_fires_after_a_button_tap_too(self) -> None:
        from bot.handlers.earlyrb import get_earlyrb_handler
        from telegram.ext import TypeHandler

        handler = get_earlyrb_handler()
        timeout = handler.states[ConversationHandler.TIMEOUT]
        self.assertIsInstance(timeout[0], TypeHandler)


class AmountChangedTests(unittest.IsolatedAsyncioTestCase):
    """Staff recorded it by hand while the Claim prompt was up."""

    async def test_nothing_left_after_requote_ends_without_a_new_claim(self) -> None:
        from bot.services.early_rakeback_auto import QuoteStage

        update, context, chat, status = ClaimStatusMessageTests._make(None)
        fresh = MagicMock(remaining=0)
        changed = SimpleNamespace(kind="amount_changed", quote=fresh)
        with (
            patch(
                "bot.handlers.earlyrb.handle_stale_flow_callback",
                AsyncMock(return_value=False),
            ),
            patch(
                "bot.services.early_rakeback_auto.claim_feeback",
                AsyncMock(return_value=changed),
            ),
            patch(
                "bot.services.early_rakeback_auto.classify_quote",
                AsyncMock(
                    return_value=QuoteStage(
                        "nothing_remaining", fresh, player_message="all claimed"
                    )
                ),
            ),
            patch("bot.services.early_rakeback_auto.create_claim_row", return_value=7),
            patch(
                "bot.services.early_rakeback_auto.new_idempotency_key", return_value="k"
            ),
            patch("bot.handlers.earlyrb._prompt_claim", AsyncMock()) as prompt,
        ):
            state = await earlyrb_claim(update, context)

        self.assertEqual(state, ConversationHandler.END)
        prompt.assert_not_awaited()
        sent = [c.args[0] for c in chat.send_message.await_args_list]
        self.assertIn("all claimed", sent)
        self.assertNotIn("earlyrb_club_id", context.chat_data)


if __name__ == "__main__":
    unittest.main()
