"""Tests for bind keyboard callback_data length."""

from __future__ import annotations

import unittest

from bot.services.payment_bind_candidates import CandidateGroup
from notification.bind_keyboards import (
    MAX_CALLBACK_BYTES,
    candidate_picker_markup,
    confirm_bind_markup,
    reassign_or_add_markup,
)


class BindKeyboardsTestCase(unittest.TestCase):
    def test_callback_data_within_limit(self):
        candidates = [
            CandidateGroup(
                telegram_chat_id=-1001234567890,
                club_id=2,
                group_title="RT / 6485-8168 / Angus Mcgoon",
            ),
            CandidateGroup(
                telegram_chat_id=-1009876543210,
                club_id=2,
                group_title="AT / 3454-3453 / Jingus",
            ),
        ]
        for markup in (
            candidate_picker_markup("venmo", 999999, candidates),
            confirm_bind_markup("venmo", 999999, -1001234567890),
            reassign_or_add_markup(
                "crypto",
                999999,
                target_chat_id=-1001234567890,
                target_title="GTO / 8190-5287 / Player",
            ),
        ):
            for row in markup["inline_keyboard"]:
                for button in row:
                    size = len(button["callback_data"].encode("utf-8"))
                    self.assertLessEqual(size, MAX_CALLBACK_BYTES)
