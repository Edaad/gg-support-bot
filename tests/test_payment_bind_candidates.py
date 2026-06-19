"""Tests for payment bind candidate service."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.services.payment_bind_candidates import (
    CandidateGroup,
    bind_scope_mismatch_error,
    candidates_for_payment,
    list_candidate_groups,
    reset_all_candidates,
    upsert_candidate_on_bind,
)
from db.models import VenmoPayerBinding, VenmoPayment

GROUP_TITLE_PROD = "RT / 6485-8168 / Angus Mcgoon"


class PaymentBindCandidatesTestCase(unittest.TestCase):
    def test_list_candidate_groups_empty(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = (
            []
        )
        result = list_candidate_groups(session, "venmo", payer_name="Moshe Toussoun")
        self.assertEqual(result, [])

    def test_list_candidate_groups_maps_rows(self):
        row = VenmoPayerBinding(
            payer_name_normalized="moshe toussoun",
            venmo_handle="@h",
            telegram_chat_id=-1001,
            club_id=2,
            bound_group_title_at_bind="RT / 1 / A",
            last_bound_at=datetime.now(timezone.utc),
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            row
        ]
        with patch(
            "bot.services.venmo_payments.resolve_display_group_title",
            return_value="RT / 1 / A",
        ):
            result = list_candidate_groups(session, "venmo", payer_name="Moshe Toussoun")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], CandidateGroup)
        self.assertEqual(result[0].telegram_chat_id, -1001)

    def test_reset_all_candidates_deletes(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.delete.return_value = 2
        deleted = reset_all_candidates(session, "venmo", payer_name="Moshe Toussoun")
        self.assertEqual(deleted, 2)

    def test_upsert_creates_new_row(self):
        session = MagicMock()
        session.query.return_value.filter_by.return_value.one_or_none.return_value = None
        upsert_candidate_on_bind(
            session,
            "venmo",
            payer_name="Moshe Toussoun",
            method_handle="@h",
            telegram_chat_id=-1001,
            club_id=2,
            bound_group_title_at_bind="RT / 1 / A",
        )
        session.add.assert_called_once()

    def test_list_candidate_groups_filters_test_scope(self):
        test_row = VenmoPayerBinding(
            payer_name_normalized="winson dong",
            venmo_handle="@h",
            telegram_chat_id=-1001,
            club_id=2,
            bound_group_title_at_bind="CC / 4334-4433 / TEST",
            last_bound_at=datetime.now(timezone.utc),
        )
        prod_row = VenmoPayerBinding(
            payer_name_normalized="winson dong",
            venmo_handle="@h",
            telegram_chat_id=-1002,
            club_id=2,
            bound_group_title_at_bind=GROUP_TITLE_PROD,
            last_bound_at=datetime.now(timezone.utc),
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            test_row,
            prod_row,
        ]

        def _title(chat_id: int) -> str:
            if chat_id == -1001:
                return "CC / 4334-4433 / TEST"
            if chat_id == -1002:
                return GROUP_TITLE_PROD
            return ""

        with patch(
            "bot.services.venmo_payments.resolve_display_group_title",
            side_effect=_title,
        ):
            test_only = list_candidate_groups(
                session,
                "venmo",
                payer_name="Winson Dong",
                test_scope=True,
            )
            prod_only = list_candidate_groups(
                session,
                "venmo",
                payer_name="Winson Dong",
                test_scope=False,
            )

        self.assertEqual(len(test_only), 1)
        self.assertEqual(test_only[0].telegram_chat_id, -1001)
        self.assertEqual(len(prod_only), 1)
        self.assertEqual(prod_only[0].telegram_chat_id, -1002)

    def test_candidates_for_payment_uses_payment_is_test(self):
        payment = VenmoPayment(
            payer_name="Winson Dong",
            amount_cents=20000,
            venmo_handle="@godfather4444",
            goods_or_services=False,
            is_test=True,
        )
        session = MagicMock()
        with patch(
            "bot.services.payment_bind_candidates.list_candidate_groups",
            return_value=[],
        ) as mock_list:
            candidates_for_payment(session, payment, "venmo")
        mock_list.assert_called_once()
        self.assertTrue(mock_list.call_args.kwargs["test_scope"])

    def test_bind_scope_mismatch_error(self):
        self.assertIsNotNone(
            bind_scope_mismatch_error(
                payment_is_test=True,
                group_title="RT / 6485-8168 / Angus Mcgoon",
            )
        )
        self.assertIsNotNone(
            bind_scope_mismatch_error(
                payment_is_test=False,
                group_title="CC / 4334-4433 / TEST",
            )
        )
        self.assertIsNone(
            bind_scope_mismatch_error(
                payment_is_test=True,
                group_title="CC / 4334-4433 / TEST",
            )
        )
