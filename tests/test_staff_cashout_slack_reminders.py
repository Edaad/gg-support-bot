"""Tests for staff cashout 5-minute head-admin Slack reminders."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from bot.services import staff_cashout_slack_reminders as rem


class FormatCashoutSlackReminderTests(unittest.TestCase):
    def test_includes_name_remaining_and_link(self) -> None:
        text = rem.format_cashout_slack_reminder(
            group_title="RT AT / 2208-1964 / Nathan",
            remaining=Decimal("1250"),
            record_id=42,
            dashboard_url="https://example.com/cashout-records/42",
        )
        self.assertIn(":siren: URGENT :siren:", text)
        self.assertIn("`RT AT / 2208-1964 / Nathan`", text)
        self.assertIn("Remaining: $1,250.00", text)
        self.assertIn(
            "<https://example.com/cashout-records/42|Open cashout>",
            text,
        )
        self.assertNotIn("(cashout name)", text)

    def test_omits_link_when_url_missing(self) -> None:
        text = rem.format_cashout_slack_reminder(
            group_title="GTO / 1 / X",
            remaining=Decimal("10.5"),
            record_id=1,
            dashboard_url=None,
        )
        self.assertIn("Remaining: $10.50", text)
        self.assertNotIn("Open cashout", text)

    def test_escapes_backticks_in_title(self) -> None:
        text = rem.format_cashout_slack_reminder(
            group_title="RT / 1 / Na`than",
            remaining=Decimal("1"),
            record_id=1,
            dashboard_url=None,
        )
        self.assertIn("`RT / 1 / Na'than`", text)


class DashboardUrlTests(unittest.TestCase):
    def test_prefers_dashboard_public_url(self) -> None:
        with patch.dict(
            "os.environ",
            {
                rem.DASHBOARD_PUBLIC_URL_ENV: "https://dash.example/",
                rem.HEROKU_APP_NAME_ENV: "ignored-app",
            },
            clear=False,
        ):
            self.assertEqual(
                rem.dashboard_public_base_url(),
                "https://dash.example",
            )
            self.assertEqual(
                rem.cashout_record_dashboard_url(9),
                "https://dash.example/cashout-records/9",
            )

    def test_falls_back_to_heroku_app_name(self) -> None:
        with patch.dict(
            "os.environ",
            {rem.HEROKU_APP_NAME_ENV: "gg-support-bot-2025"},
            clear=False,
        ):
            env = {
                k: v
                for k, v in __import__("os").environ.items()
                if k != rem.DASHBOARD_PUBLIC_URL_ENV
            }
            env[rem.HEROKU_APP_NAME_ENV] = "gg-support-bot-2025"
            with patch.dict("os.environ", env, clear=True):
                self.assertEqual(
                    rem.dashboard_public_base_url(),
                    "https://gg-support-bot-2025.herokuapp.com",
                )


class IsDueTests(unittest.TestCase):
    def test_too_new_not_due(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        self.assertFalse(
            rem._is_due(
                created_at=now - timedelta(minutes=2),
                last_slack_reminder_at=None,
                enabled_at=now - timedelta(hours=1),
                now=now,
            )
        )

    def test_never_pinged_is_due(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        self.assertTrue(
            rem._is_due(
                created_at=now - timedelta(minutes=6),
                last_slack_reminder_at=None,
                enabled_at=now - timedelta(hours=1),
                now=now,
            )
        )

    def test_recent_ping_not_due(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        self.assertFalse(
            rem._is_due(
                created_at=now - timedelta(minutes=30),
                last_slack_reminder_at=now - timedelta(minutes=2),
                enabled_at=now - timedelta(hours=1),
                now=now,
            )
        )

    def test_toggle_on_re_fires_even_if_recently_pinged(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        self.assertTrue(
            rem._is_due(
                created_at=now - timedelta(minutes=30),
                last_slack_reminder_at=now - timedelta(minutes=1),
                enabled_at=now,
                now=now,
            )
        )

    def test_five_minutes_since_last_ping_is_due(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        self.assertTrue(
            rem._is_due(
                created_at=now - timedelta(minutes=30),
                last_slack_reminder_at=now - timedelta(minutes=5),
                enabled_at=now - timedelta(hours=1),
                now=now,
            )
        )


class ListDueTests(unittest.TestCase):
    def _record(
        self,
        *,
        record_id: int = 1,
        title: str = "RT AT / 2208-1964 / Nathan",
        amount: Decimal = Decimal("500"),
        created_at: datetime | None = None,
        last_ping: datetime | None = None,
        do_not_send: bool = False,
        tracks: bool = True,
        sends: list | None = None,
    ) -> MagicMock:
        row = MagicMock()
        row.id = record_id
        row.group_title = title
        row.amount = amount
        row.created_at = created_at or (datetime.utcnow() - timedelta(minutes=10))
        row.last_slack_reminder_at = last_ping
        row.do_not_send = do_not_send
        row.tracks_money_sent = tracks
        row.money_sends = sends or []
        return row

    def test_disabled_returns_empty(self) -> None:
        control = MagicMock(enabled=False, enabled_at=None)
        session = MagicMock()
        q = MagicMock()
        session.query.return_value = q
        q.filter.return_value = q
        q.first.return_value = control

        with patch(
            "bot.services.staff_cashout_slack_reminders.get_db"
        ) as get_db:
            get_db.return_value.__enter__.return_value = session
            get_db.return_value.__exit__.return_value = False
            self.assertEqual(rem.list_due_cashout_reminders(), [])

    def test_skips_cleared_oversent_do_not_send_too_new(self) -> None:
        now = datetime(2026, 9, 11, 18, 0, 0)
        control = MagicMock(
            enabled=True,
            enabled_at=now - timedelta(hours=1),
        )
        active = self._record(
            record_id=1,
            created_at=now - timedelta(minutes=10),
        )
        cleared_send = MagicMock(amount=Decimal("500"), created_at=now)
        cleared = self._record(
            record_id=2,
            created_at=now - timedelta(minutes=10),
            sends=[cleared_send],
        )
        oversent_send = MagicMock(amount=Decimal("600"), created_at=now)
        oversent = self._record(
            record_id=3,
            created_at=now - timedelta(minutes=10),
            sends=[oversent_send],
        )
        # do_not_send / too-new filtered in SQL; still verify Active filter

        session = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.options.return_value = q
            q.order_by.return_value = q
            if model is rem.StaffCashoutSlackReminderControl:
                q.first.return_value = control
            else:
                q.all.return_value = [active, cleared, oversent]
            return q

        session.query.side_effect = query_side_effect

        with patch(
            "bot.services.staff_cashout_slack_reminders.get_db"
        ) as get_db:
            get_db.return_value.__enter__.return_value = session
            get_db.return_value.__exit__.return_value = False
            due = rem.list_due_cashout_reminders(now=now)

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], 1)
        self.assertEqual(due[0]["remaining"], Decimal("500"))


class SendDueTests(unittest.IsolatedAsyncioTestCase):
    async def test_updates_last_ping_only_on_success(self) -> None:
        due = [
            {
                "id": 1,
                "group_title": "RT / 1 / A",
                "remaining": Decimal("100"),
            },
            {
                "id": 2,
                "group_title": "RT / 2 / B",
                "remaining": Decimal("200"),
            },
        ]
        notify = AsyncMock(side_effect=[True, False])
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
            "bot.services.staff_cashout_slack_reminders.get_db"
        ) as get_db:
            get_db.return_value.__enter__.return_value = session
            get_db.return_value.__exit__.return_value = False
            sent = await rem.send_due_cashout_reminders()

        self.assertEqual(sent, 1)
        self.assertEqual(notify.await_count, 2)
        self.assertIsNotNone(row1.last_slack_reminder_at)
        first_text = notify.await_args_list[0].args[0]
        self.assertIn("`RT / 1 / A`", first_text)
        self.assertIn(
            "<https://dash.example/cashout-records/1|Open cashout>",
            first_text,
        )
        self.assertEqual(
            notify.await_args_list[0].kwargs.get("source"),
            rem.SLACK_SOURCE,
        )

    async def test_toggle_off_sends_nothing(self) -> None:
        with patch(
            "bot.services.staff_cashout_slack_reminders.list_due_cashout_reminders",
            return_value=[],
        ), patch(
            "bot.services.slack_ops_notify.notify_slack_head_admin_escalation",
            new_callable=AsyncMock,
        ) as notify:
            sent = await rem.send_due_cashout_reminders()
        self.assertEqual(sent, 0)
        notify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
