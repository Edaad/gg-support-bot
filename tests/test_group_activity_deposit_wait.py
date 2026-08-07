"""Cross-dyno safety for deposit-wait vs idle activity persistence."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.services import group_activity as ga


class DepositWaitCacheSafetyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        ga.clear_activity_state_for_tests()

    def tearDown(self) -> None:
        ga.clear_activity_state_for_tests()

    def test_record_human_message_does_not_persist_deposit_wait(self) -> None:
        chat_id = -1001
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
        ):
            state = ga.get_chat_activity_state(chat_id)
            state.support_row_id = 7
            state.deposit_instructions_pending = True
            state.deposit_method_slug = "applepay"

            with patch.object(
                ga, "update_support_group_chat_row", return_value=(True, None)
            ) as upd:
                ga.record_human_message(chat_id, role="player")

        upd.assert_called_once()
        kwargs = upd.call_args.kwargs
        self.assertNotIn("escalation_deposit_instructions_pending", kwargs)
        self.assertNotIn("escalation_deposit_method_slug", kwargs)
        self.assertNotIn("escalation_deposit_sent_armed_at", kwargs)
        self.assertNotIn("escalation_deposit_sent_button_message_id", kwargs)

    def test_deposit_instructions_pending_resyncs_from_db(self) -> None:
        """Stale worker memory must not win over a clear written by another dyno."""
        chat_id = -1002
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
        ):
            state = ga.get_chat_activity_state(chat_id)
        state.support_row_id = 9
        state.deposit_instructions_pending = True
        state.deposit_method_slug = "applepay"

        db_row = SimpleNamespace(
            id=9,
            escalation_deposit_instructions_pending=False,
            escalation_deposit_method_slug=None,
            escalation_deposit_sent_armed_at=None,
            escalation_deposit_sent_button_message_id=None,
        )
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=db_row
        ):
            self.assertFalse(ga.deposit_instructions_pending(chat_id))

        self.assertFalse(state.deposit_instructions_pending)
        self.assertIsNone(state.deposit_method_slug)

    def test_mark_still_persists_deposit_wait(self) -> None:
        chat_id = -1003
        with patch.object(
            ga, "fetch_support_group_chat_by_telegram_chat_id", return_value=None
        ):
            state = ga.get_chat_activity_state(chat_id)
            state.support_row_id = 11

            with patch.object(
                ga, "update_support_group_chat_row", return_value=(True, None)
            ) as upd:
                ga.mark_deposit_instructions_pending(chat_id, method_slug="crypto")

        kwargs = upd.call_args.kwargs
        self.assertTrue(kwargs["escalation_deposit_instructions_pending"])
        self.assertEqual(kwargs["escalation_deposit_method_slug"], "crypto")


if __name__ == "__main__":
    unittest.main()
