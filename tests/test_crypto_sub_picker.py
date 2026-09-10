"""Deposit crypto sub-option picker copy."""

from __future__ import annotations

import unittest

from bot.handlers.deposit import CRYPTO_SUB_PICKER_NOTE, sub_option_picker_text


class SubOptionPickerTextTests(unittest.TestCase):
    def test_crypto_includes_btc_eth_note(self):
        text = sub_option_picker_text("Crypto", "crypto")
        self.assertEqual(
            text,
            "You selected Crypto. Which option?\n\n" + CRYPTO_SUB_PICKER_NOTE,
        )

    def test_other_methods_unchanged(self):
        self.assertEqual(
            sub_option_picker_text("Zelle", "zelle"),
            "You selected Zelle. Which option?",
        )
        self.assertNotIn("Note:", sub_option_picker_text("Zelle", "zelle"))
