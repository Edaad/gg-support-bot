"""Club picker is the last /deposit message, after payment instructions."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ConversationHandler

from bot.handlers import deposit as dep

CHAT_ID = -1003978131309
PLAYER_ID = 8132930521
RT_UNIONS = (
    {"shorthand": "RT", "label": "Round Table (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)


def _amount_update(*, text: str = "100"):
    chat = SimpleNamespace(id=CHAT_ID, type="supergroup", title="RT / 1234 / Player")
    message = SimpleNamespace(
        text=text,
        date=datetime.now(timezone.utc),
        reply_text=AsyncMock(),
        chat=chat,
        message_id=10,
    )
    return SimpleNamespace(
        message=message,
        effective_message=message,
        effective_chat=chat,
        effective_user=SimpleNamespace(id=PLAYER_ID),
    )


def _context():
    return SimpleNamespace(
        chat_data={
            "deposit_club_id": 2,
            "deposit_chat_id": CHAT_ID,
            "deposit_user_id": PLAYER_ID,
            "deposit_awaiting_amount": True,
        },
        user_data={},
        bot=SimpleNamespace(),
    )


class DepositUnionAfterInstructionsTests(unittest.IsolatedAsyncioTestCase):
    @patch.object(dep, "_record_funnel_from_context")
    @patch.object(dep, "filter_deposit_methods_for_chat", side_effect=lambda _cid, ms: ms)
    @patch.object(dep, "_prompt_deposit_union", new_callable=AsyncMock)
    @patch.object(dep, "_prompt_deposit_methods", new_callable=AsyncMock)
    @patch.object(dep, "deposit_unions_for_chat", return_value=RT_UNIONS)
    @patch.object(
        dep,
        "get_methods_for_amount",
        return_value=[{"id": 29, "slug": "venmo", "name": "Venmo"}],
    )
    async def test_amount_goes_to_methods_even_when_club_has_unions(
        self, _methods, _unions, prompt_methods, prompt_union, *_rest
    ):
        prompt_methods.return_value = True
        update = _amount_update()
        result = await dep.deposit_amount_received(update, _context())

        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        prompt_methods.assert_awaited_once()
        prompt_union.assert_not_awaited()

    @patch.object(dep, "_schedule_deposit_reminder")
    @patch.object(dep, "_send_bonus_message", new_callable=AsyncMock)
    @patch.object(dep, "_record_deposit")
    @patch.object(dep, "_record_funnel_from_context")
    @patch.object(dep, "_exit_deposit_flow", new_callable=AsyncMock)
    @patch.object(dep, "_prompt_deposit_union", new_callable=AsyncMock)
    @patch.object(dep, "_deposit_unions_for_flow", return_value=RT_UNIONS)
    async def test_complete_prompts_union_after_bonus(
        self, _unions, prompt_union, exit_flow, *_rest
    ):
        exit_flow.return_value = ConversationHandler.END
        chat = SimpleNamespace(id=CHAT_ID, send_message=AsyncMock())
        context = _context()
        context.chat_data["deposit_amount"] = Decimal("100")

        result = await dep._complete_deposit_flow(chat, context)

        self.assertEqual(result, dep.DEPOSIT_UNION)
        prompt_union.assert_awaited_once()
        exit_flow.assert_not_awaited()

    @patch.object(dep, "_schedule_deposit_reminder")
    @patch.object(dep, "_send_bonus_message", new_callable=AsyncMock)
    @patch.object(dep, "_record_deposit")
    @patch.object(dep, "_record_funnel_from_context")
    @patch.object(dep, "_exit_deposit_flow", new_callable=AsyncMock)
    @patch.object(dep, "_prompt_deposit_union", new_callable=AsyncMock)
    @patch.object(dep, "_deposit_unions_for_flow", return_value=RT_UNIONS)
    async def test_manual_union_method_defers_picker_until_ack(
        self, _unions, prompt_union, exit_flow, *_rest
    ):
        exit_flow.return_value = ConversationHandler.END
        chat = SimpleNamespace(id=CHAT_ID, send_message=AsyncMock())
        context = _context()
        context.chat_data["deposit_tracks_manual_requests"] = True

        result = await dep._complete_deposit_flow(chat, context)

        self.assertEqual(result, dep.DEPOSIT_UNION)
        prompt_union.assert_not_awaited()
        exit_flow.assert_not_awaited()
        self.assertTrue(context.chat_data.get("deposit_prompt_union_after_ack"))

    @patch.object(dep, "_schedule_deposit_reminder")
    @patch.object(dep, "_send_bonus_message", new_callable=AsyncMock)
    @patch.object(dep, "_record_deposit")
    @patch.object(dep, "_record_funnel_from_context")
    @patch.object(dep, "_exit_deposit_flow", new_callable=AsyncMock)
    @patch.object(dep, "_prompt_deposit_union", new_callable=AsyncMock)
    @patch.object(dep, "_deposit_unions_for_flow", return_value=None)
    async def test_complete_without_unions_exits(
        self, _unions, prompt_union, exit_flow, *_rest
    ):
        exit_flow.return_value = ConversationHandler.END
        chat = SimpleNamespace(id=CHAT_ID, send_message=AsyncMock())

        result = await dep._complete_deposit_flow(chat, _context())

        self.assertEqual(result, ConversationHandler.END)
        prompt_union.assert_not_awaited()
        exit_flow.assert_awaited_once()
