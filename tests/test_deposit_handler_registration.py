"""Guard ConversationHandler construction for /deposit (worker boot)."""

from __future__ import annotations

import unittest

from telegram.ext import ConversationHandler

from bot.handlers.deposit import TIMEOUT_SECONDS, get_deposit_handler


class DepositHandlerRegistrationTests(unittest.TestCase):
    def test_get_deposit_handler_builds_with_timeout(self) -> None:
        handler = get_deposit_handler()
        self.assertIsInstance(handler, ConversationHandler)
        self.assertEqual(handler.conversation_timeout, TIMEOUT_SECONDS)
        self.assertEqual(TIMEOUT_SECONDS, 600)
        self.assertIn(ConversationHandler.TIMEOUT, handler.states)
