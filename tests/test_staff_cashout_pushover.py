"""Tests for cashout Pushover rail matching and recipient fan-out."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import staff_cashout_pushover as push
from bot.services import staff_cashout_slack_reminders as rem


class RailsForDisplayNamesTests(unittest.TestCase):
    def test_venmo_substring(self) -> None:
        self.assertEqual(
            push.rails_for_display_names(["RT Venmo"]),
            {"venmo"},
        )

    def test_cash_app_normalizes(self) -> None:
        self.assertEqual(
            push.rails_for_display_names(["Cash App"]),
            {"cashapp"},
        )

    def test_empty_is_other(self) -> None:
        self.assertEqual(push.rails_for_display_names([]), {"other"})
        self.assertEqual(push.rails_for_display_names([""]), {"other"})
        self.assertEqual(push.rails_for_display_names([None]), {"other"})

    def test_custom_is_other(self) -> None:
        self.assertEqual(
            push.rails_for_display_names(["Wire transfer"]),
            {"other"},
        )

    def test_multi_union(self) -> None:
        self.assertEqual(
            push.rails_for_display_names(["Venmo", "Zelle"]),
            {"venmo", "zelle"},
        )


class RecipientsForRailsTests(unittest.TestCase):
    def test_filters_by_method_intersection(self) -> None:
        session = MagicMock()
        edaad = MagicMock()
        edaad.id = 1
        edaad.name = "Edaad"
        edaad.pushover_user_key = "key_e"
        edaad.methods = ["venmo", "zelle"]
        aiden = MagicMock()
        aiden.id = 2
        aiden.name = "Aiden"
        aiden.pushover_user_key = "key_a"
        aiden.methods = ["crypto", "zelle"]
        q = MagicMock()
        session.query.return_value = q
        q.order_by.return_value = q
        q.all.return_value = [edaad, aiden]

        with patch("bot.services.staff_cashout_pushover.get_db") as get_db:
            get_db.return_value.__enter__.return_value = session
            get_db.return_value.__exit__.return_value = False
            venmo = push.recipients_for_rails({"venmo"})
            other = push.recipients_for_rails({"other"})

        self.assertEqual([r["name"] for r in venmo], ["Edaad"])
        self.assertEqual([r["name"] for r in other], ["Edaad", "Aiden"])


class NotifyCreateGatedTests(unittest.TestCase):
    def test_skips_when_master_toggle_off(self) -> None:
        with patch(
            "bot.services.staff_cashout_pushover.get_slack_reminder_enabled",
            return_value=False,
        ), patch(
            "bot.services.staff_cashout_pushover._load_record_notify_context"
        ) as load:
            sent = push.notify_cashout_pushover_sync(9, require_master_toggle=True)
        self.assertEqual(sent, 0)
        load.assert_not_called()


class OverdueUsesFanoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_async_fanout_after_slack(self) -> None:
        due = [
            {
                "id": 1,
                "group_title": "RT / 1 / A",
                "remaining": Decimal("100"),
            },
        ]
        notify = AsyncMock(return_value=True)
        fanout = AsyncMock(return_value=1)
        session = MagicMock()
        row1 = MagicMock()
        row1.id = 1
        row1.last_slack_reminder_at = None
        q = MagicMock()
        session.query.return_value = q
        q.filter.return_value = q
        q.first.return_value = row1

        with patch(
            "bot.services.staff_cashout_slack_reminders.list_due_cashout_reminders",
            return_value=due,
        ), patch(
            "bot.services.staff_cashout_slack_reminders.dashboard_public_base_url",
            return_value="https://dash.example",
        ), patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            notify,
        ), patch(
            "bot.services.staff_cashout_pushover.notify_cashout_pushover_async",
            fanout,
        ), patch(
            "bot.services.staff_cashout_slack_reminders.get_db"
        ) as get_db:
            get_db.return_value.__enter__.return_value = session
            get_db.return_value.__exit__.return_value = False
            sent = await rem.send_due_cashout_reminders()

        self.assertEqual(sent, 1)
        fanout.assert_awaited_once()
        self.assertEqual(fanout.await_args.args[0], 1)
        self.assertIsNotNone(row1.last_slack_reminder_at)


if __name__ == "__main__":
    unittest.main()
