"""Tests for payment bind callbacks and keyboard markup."""

from __future__ import annotations

import unittest

from bot.services.payment_bind_candidates import CandidateGroup
from notification.bind_keyboards import (
    MAX_CALLBACK_BYTES,
    candidate_picker_markup,
    confirm_bind_markup,
    reassign_or_add_markup,
)
from notification.handlers.bind_callbacks import _is_stale_notification_button
from notification.payment_bind_helpers import inject_pending_confirm_group_line


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

    def test_confirm_bind_shows_group_title_on_button(self):
        markup = confirm_bind_markup(
            "venmo",
            42,
            -1001234567890,
            group_title="CC / 4334-4433 / TEST",
        )
        self.assertIn("CC / 4334-4433 / TEST", markup["inline_keyboard"][0][0]["text"])


class PendingConfirmGroupLineTestCase(unittest.TestCase):
    def test_inject_replaces_ambiguous_picker_with_selected_group(self):
        text = (
            "🔔 Venmo Payment Notification\n\n"
            "Group Chat: Unbound — select group below\n"
            "• CC / 4334-4433 / TEST\n"
            "• RT / 9090-9999 / TEST\n"
            "\n"
            "Name: Winson Dong"
        )
        updated = inject_pending_confirm_group_line(text, "CC / 4334-4433 / TEST")
        self.assertIn("Group Chat: CC / 4334-4433 / TEST — confirm below", updated)
        self.assertNotIn("select group below", updated)
        self.assertNotIn("9090-9999", updated)


class StaleNotificationButtonTestCase(unittest.TestCase):
    def test_same_message_is_never_stale(self):
        self.assertFalse(
            _is_stale_notification_button(
                action="s",
                payment_notification_message_id=8834,
                callback_message_id=8834,
            )
        )

    def test_notification_picker_on_wrong_message_is_stale(self):
        self.assertTrue(
            _is_stale_notification_button(
                action="s",
                payment_notification_message_id=8834,
                callback_message_id=8836,
            )
        )

    def test_reassign_add_on_bot_reply_is_allowed(self):
        for action in ("r", "a", "c", "ac", "b"):
            with self.subTest(action=action):
                self.assertFalse(
                    _is_stale_notification_button(
                        action=action,
                        payment_notification_message_id=8834,
                        callback_message_id=8836,
                    )
                )


if __name__ == "__main__":
    unittest.main()
