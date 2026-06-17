"""Tests for payment bind candidate service."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.services.payment_bind_candidates import (
    CandidateGroup,
    list_candidate_groups,
    reset_all_candidates,
    upsert_candidate_on_bind,
)
from db.models import VenmoPayerBinding


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
