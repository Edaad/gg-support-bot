"""Tests for manual trade-request deposit methods."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api.payment_v2_helpers import apply_manual_trade_request_constraints
from bot.services.manual_deposit_requests import (
    ManualDepositCapacityError,
    capacity_allows,
    create_request_atomic,
)


class ApplyManualTradeConstraintsTests(unittest.TestCase):
    def test_requires_limit_message_variant(self):
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=None,
            manual_request_message="hi",
            manual_request_variant_name="v1",
            tiers=[],
        )
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

        method.deposit_limit = Decimal("1000")
        method.manual_request_message = "  "
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

        method.manual_request_message = "Send here"
        method.manual_request_variant_name = ""
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

    def test_forces_off_linking_and_sub_options(self):
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=Decimal("500"),
            manual_request_message=" Pay me ",
            manual_request_variant_name=" Union ",
            has_sub_options=True,
            first_time_linking_enabled=True,
            first_time_bind_mode="special_amount",
            tiers=[],
        )
        apply_manual_trade_request_constraints(method)
        self.assertFalse(method.has_sub_options)
        self.assertFalse(method.first_time_linking_enabled)
        self.assertIsNone(method.first_time_bind_mode)
        self.assertEqual(method.manual_request_message, "Pay me")
        self.assertEqual(method.manual_request_variant_name, "Union")

    def test_rejects_stripe_on_tiers(self):
        tier = SimpleNamespace(use_group_checkout_link=True, variants=[])
        method = SimpleNamespace(
            tracks_manual_requests=True,
            direction="deposit",
            deposit_limit=Decimal("100"),
            manual_request_message="x",
            manual_request_variant_name="y",
            has_sub_options=False,
            first_time_linking_enabled=False,
            first_time_bind_mode=None,
            tiers=[tier],
        )
        with self.assertRaises(ValueError):
            apply_manual_trade_request_constraints(method)

    def test_noop_when_flag_off(self):
        method = SimpleNamespace(
            tracks_manual_requests=False,
            has_sub_options=True,
            first_time_linking_enabled=True,
        )
        apply_manual_trade_request_constraints(method)
        self.assertTrue(method.has_sub_options)


class CapacityAllowsTests(unittest.TestCase):
    def test_amount_aware_remaining_capacity(self):
        session = MagicMock()
        with patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("900"),
        ):
            self.assertTrue(
                capacity_allows(
                    session,
                    method_id=1,
                    amount=Decimal("50"),
                    deposit_limit=Decimal("1000"),
                )
            )
            self.assertFalse(
                capacity_allows(
                    session,
                    method_id=1,
                    amount=Decimal("200"),
                    deposit_limit=Decimal("1000"),
                )
            )


class GetMethodsForAmountManualTests(unittest.TestCase):
    def test_hides_when_amount_exceeds_remaining(self):
        from bot.services import club_payment_v2

        method = SimpleNamespace(
            id=7,
            name="Zelle",
            slug="zelle-union",
            min_amount=None,
            max_amount=None,
            has_sub_options=False,
            is_public=True,
            tracks_manual_requests=True,
            manual_request_message="msg",
            manual_request_variant_name="v",
            deposit_limit=Decimal("1000"),
            accumulated_amount=Decimal("0"),
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            method
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch.object(club_payment_v2, "get_db", return_value=cm), patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("900"),
        ):
            shown = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("200")
            )
            self.assertEqual(shown, [])
            shown_ok = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("50")
            )
            self.assertEqual(len(shown_ok), 1)
            self.assertTrue(shown_ok[0]["tracks_manual_requests"])

    def test_hides_when_amount_outside_min_max(self):
        from bot.services import club_payment_v2

        method = SimpleNamespace(
            id=8,
            name="Zelle",
            slug="zelle-union",
            min_amount=Decimal("50"),
            max_amount=Decimal("200"),
            has_sub_options=False,
            is_public=True,
            tracks_manual_requests=True,
            manual_request_message="msg",
            manual_request_variant_name="v",
            deposit_limit=Decimal("10000"),
            accumulated_amount=Decimal("0"),
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            method
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch.object(club_payment_v2, "get_db", return_value=cm), patch(
            "bot.services.manual_deposit_requests.sum_for_method",
            return_value=Decimal("0"),
        ):
            self.assertEqual(
                club_payment_v2.get_methods_for_amount(1, "deposit", Decimal("25")),
                [],
            )
            self.assertEqual(
                club_payment_v2.get_methods_for_amount(1, "deposit", Decimal("250")),
                [],
            )
            shown = club_payment_v2.get_methods_for_amount(
                1, "deposit", Decimal("100")
            )
            self.assertEqual(len(shown), 1)


class CreateRequestAtomicTests(unittest.TestCase):
    def test_rejects_over_capacity(self):
        method = SimpleNamespace(
            id=3,
            name="Zelle",
            slug="zelle-union",
            tracks_manual_requests=True,
            is_active=True,
            deposit_limit=Decimal("1000"),
            manual_request_variant_name="Union",
        )
        session = MagicMock()
        session.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = (
            method
        )
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False

        with patch(
            "bot.services.manual_deposit_requests.get_db", return_value=cm
        ), patch(
            "bot.services.manual_deposit_requests.capacity_allows",
            return_value=False,
        ):
            with self.assertRaises(ManualDepositCapacityError):
                create_request_atomic(
                    club_id=1,
                    method_id=3,
                    amount=Decimal("200"),
                    telegram_chat_id=-100,
                    group_title="GTO / 2222-2222 / jz",
                )


class ManualTradeLedgerFetchTests(unittest.TestCase):
    def test_checked_rows_included_unchecked_excluded(self):
        from api import audit_ledger

        checked = SimpleNamespace(
            id=1,
            club_id=10,
            telegram_chat_id=-100,
            group_title="GTO / 2222-2222 / jz",
            method_slug="zelle-union",
            method_name="Zelle",
            variant_name="Union",
            amount=Decimal("100"),
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            trade_record_checked=True,
        )
        session = MagicMock()
        q = MagicMock()
        session.query.return_value = q
        q.filter.return_value = q
        q.order_by.return_value.all.return_value = [checked]

        with patch.object(
            audit_ledger, "apply_analytics_payment_exclusion", side_effect=lambda s, qq, c: qq
        ), patch.object(
            audit_ledger, "payment_in_audit_day_for_club", return_value=True
        ), patch.object(
            audit_ledger,
            "resolve_group_title",
            return_value=("GTO / 2222-2222 / jz", "2222-2222"),
        ):
            events = audit_ledger._fetch_manual_trade_request_events(
                session,
                club_slug="clubgto",
                audit_date=date(2026, 8, 25),
                from_dt=datetime(2026, 8, 25, tzinfo=timezone.utc),
                to_dt=datetime(2026, 8, 26, tzinfo=timezone.utc),
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source, "deposit_zelle-union")
        self.assertEqual(events[0].source_label, "Zelle")
        self.assertEqual(events[0].gg_player_id, "2222-2222")
        self.assertEqual(events[0].amount_usd, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
