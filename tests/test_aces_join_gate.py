"""Creator Club's one-time Aces Table join gate on /deposit."""

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


def _union_callback_update(shorthand: str):
    chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    message = SimpleNamespace(
        chat=chat, date=datetime.now(timezone.utc), message_id=99
    )
    query = SimpleNamespace(
        data=f"depunion:{shorthand}",
        message=message,
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=PLAYER_ID),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_message=message,
        effective_chat=chat,
    )


def _ack_callback_update(*, from_user_id: int = PLAYER_ID):
    chat = SimpleNamespace(id=CHAT_ID, type="supergroup")
    message = SimpleNamespace(
        chat=chat, date=datetime.now(timezone.utc), message_id=99
    )
    query = SimpleNamespace(
        data="depaces",
        message=message,
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=from_user_id),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(
        callback_query=query,
        effective_message=message,
        effective_chat=chat,
    )


def _context(club_id: int = 3):
    return SimpleNamespace(
        chat_data={
            "deposit_club_id": club_id,
            "deposit_chat_id": CHAT_ID,
            "deposit_user_id": PLAYER_ID,
            "deposit_amount": Decimal("100"),
        },
        user_data={},
        bot=SimpleNamespace(),
    )


CREATOR_CLUB_UNIONS = (
    {"shorthand": "CC", "label": "Creator Club (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)
ROUND_TABLE_UNIONS = (
    {"shorthand": "RT", "label": "Round Table (TMT Union)"},
    {"shorthand": "AT", "label": "Aces Table (Massiv Union)"},
)


class AcesJoinGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patches = [
            patch.object(
                dep, "handle_stale_flow_callback", new=AsyncMock(return_value=False)
            ),
            patch.object(dep, "_record_funnel_from_context"),
            patch.object(dep, "register_flow_callback_message"),
            patch.object(dep, "_prompt_deposit_methods", new=AsyncMock(return_value=True)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @patch.object(dep, "has_aces_join_ack", return_value=False)
    @patch.object(dep, "is_creator_club", return_value=True)
    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"CC", "AT"})
    )
    async def test_first_aces_pick_shows_join_link(self, *_mocks):
        update = _union_callback_update("AT")
        context = _context()
        result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, dep.DEPOSIT_ACES_JOIN)
        dep._prompt_deposit_methods.assert_not_awaited()
        text, kwargs = update.callback_query.edit_message_text.await_args
        self.assertIn(dep.ACES_TABLE_JOIN_LINK, text[0])
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.text, "I HAVE JOINED")
        self.assertEqual(button.callback_data, "depaces")

    @patch.object(dep, "has_aces_join_ack", return_value=True)
    @patch.object(dep, "is_creator_club", return_value=True)
    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"CC", "AT"})
    )
    async def test_returning_player_skips_join_link(self, *_mocks):
        update = _union_callback_update("AT")
        context = _context()
        result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        dep._prompt_deposit_methods.assert_awaited_once()

    @patch.object(dep, "has_aces_join_ack", return_value=False)
    @patch.object(dep, "is_creator_club", return_value=True)
    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"CC", "AT"})
    )
    async def test_creator_club_pick_never_gated(self, *_mocks):
        update = _union_callback_update("CC")
        context = _context()
        result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        dep._prompt_deposit_methods.assert_awaited_once()

    @patch.object(dep, "has_aces_join_ack", return_value=False)
    @patch.object(dep, "is_creator_club", return_value=False)
    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"RT", "AT"})
    )
    async def test_round_table_aces_pick_is_not_gated(self, *_mocks):
        update = _union_callback_update("AT")
        context = _context(club_id=2)
        result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        dep._prompt_deposit_methods.assert_awaited_once()

    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"CC", "AT"})
    )
    async def test_creator_club_cannot_pick_round_table_union(self, *_mocks):
        update = _union_callback_update("RT")
        context = _context()
        result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, ConversationHandler.END)
        dep._prompt_deposit_methods.assert_not_awaited()

    async def test_ack_continues_to_methods(self):
        update = _ack_callback_update()
        context = _context()
        result = await dep.deposit_aces_join_ack(update, context)

        self.assertEqual(result, dep.DEPOSIT_CHOOSE)
        dep._prompt_deposit_methods.assert_awaited_once()

    async def test_only_the_player_can_tap_i_have_joined(self):
        update = _ack_callback_update(from_user_id=7516419496)
        context = _context()
        result = await dep.deposit_aces_join_ack(update, context)

        self.assertEqual(result, dep.DEPOSIT_ACES_JOIN)
        dep._prompt_deposit_methods.assert_not_awaited()
        update.callback_query.answer.assert_awaited_once()

    @patch.object(dep, "_abandon_deposit_flow_session")
    @patch.object(dep, "has_aces_join_ack", return_value=False)
    @patch.object(dep, "is_creator_club", return_value=True)
    @patch.object(
        dep, "union_shorthands_for_club", return_value=frozenset({"CC", "AT"})
    )
    async def test_unpostable_join_gate_ends_flow(self, *_mocks):
        """A failed edit must not strand the player with no button to tap."""
        update = _union_callback_update("AT")
        update.callback_query.edit_message_text = AsyncMock(
            side_effect=RuntimeError("message to edit not found")
        )
        context = _context()
        with patch.object(dep, "_cleanup"):
            result = await dep.deposit_union_chosen(update, context)

        self.assertEqual(result, ConversationHandler.END)

    async def test_ack_after_session_expired_ends_flow(self):
        update = _ack_callback_update()
        context = _context()
        context.chat_data.pop("deposit_amount")
        with patch.object(dep, "_cleanup"):
            result = await dep.deposit_aces_join_ack(update, context)

        self.assertEqual(result, ConversationHandler.END)
        dep._prompt_deposit_methods.assert_not_awaited()


class AcesAckPersistenceTests(unittest.TestCase):
    @patch.object(dep, "set_aces_join_ack")
    @patch.object(dep, "is_creator_club", return_value=True)
    def test_aces_deposit_records_ack(self, _is_cc, set_ack):
        context = _context()
        context.chat_data["deposit_union_shorthand"] = "AT"
        dep._persist_aces_join_ack(context)
        set_ack.assert_called_once_with(CHAT_ID)

    @patch.object(dep, "set_aces_join_ack")
    @patch.object(dep, "is_creator_club", return_value=True)
    def test_creator_club_deposit_does_not_record_ack(self, _is_cc, set_ack):
        context = _context()
        context.chat_data["deposit_union_shorthand"] = "CC"
        dep._persist_aces_join_ack(context)
        set_ack.assert_not_called()

    @patch.object(dep, "set_aces_join_ack")
    @patch.object(dep, "is_creator_club", return_value=False)
    def test_round_table_aces_deposit_does_not_record_ack(self, _is_cc, set_ack):
        context = _context(club_id=2)
        context.chat_data["deposit_union_shorthand"] = "AT"
        dep._persist_aces_join_ack(context)
        set_ack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
