"""Guard ConversationHandler construction for /deposit (worker boot)."""

from __future__ import annotations

import unittest

from telegram import Update
from telegram.ext import ConversationHandler, TypeHandler

from bot.handlers.deposit import TIMEOUT_SECONDS, deposit_timeout, get_deposit_handler


class DepositHandlerRegistrationTests(unittest.TestCase):
    def test_get_deposit_handler_builds_with_timeout(self) -> None:
        handler = get_deposit_handler()
        self.assertIsInstance(handler, ConversationHandler)
        self.assertEqual(handler.conversation_timeout, TIMEOUT_SECONDS)
        self.assertEqual(TIMEOUT_SECONDS, 600)
        self.assertIn(ConversationHandler.TIMEOUT, handler.states)
        timeout_handlers = handler.states[ConversationHandler.TIMEOUT]
        self.assertEqual(len(timeout_handlers), 1)
        timeout_handler = timeout_handlers[0]
        self.assertIsInstance(timeout_handler, TypeHandler)
        self.assertIs(timeout_handler.callback, deposit_timeout)
        self.assertIs(timeout_handler.type, Update)
