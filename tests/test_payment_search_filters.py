"""Unit tests for Payments list omnibox (group / player) SQL filters."""

from __future__ import annotations

import unittest

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query

from api.payments_helpers import (
    apply_session_filters,
    apply_venmo_payment_filters,
    group_or_player_match_clause,
)
from db.models import StripeCheckoutSession, VenmoPayment


def _compile(clause) -> str:
    return str(
        clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    ).lower()


class GroupOrPlayerMatchClauseTestCase(unittest.TestCase):
    def test_clause_targets_live_group_and_player_sources(self):
        sql = _compile(
            group_or_player_match_clause(VenmoPayment.telegram_chat_id, 1, "Alice")
        )
        self.assertIn("groups", sql)
        self.assertIn("support_group_chats", sql)
        self.assertIn("player_details", sql)
        self.assertIn("is not null", sql)

    def test_empty_term_still_builds_pattern_clause(self):
        # Caller strips before invoking; helper still accepts non-empty after strip in filters.
        sql = _compile(
            group_or_player_match_clause(VenmoPayment.telegram_chat_id, 2, " 8190 ")
        )
        self.assertIn("ilike", sql)


class ApplyVenmoPaymentFiltersSearchTestCase(unittest.TestCase):
    def _filtered_sql(self, *, q: str | None) -> str:
        query = Query([VenmoPayment])
        query = apply_venmo_payment_filters(
            query,
            club_id=1,
            status="all",
            from_dt=None,
            to_dt=None,
            include_test=False,
            q=q,
        )
        return _compile(query.statement)

    def test_q_matches_payer_and_live_group(self):
        sql = self._filtered_sql(q="Alice")
        self.assertIn("payer_name", sql)
        self.assertIn("venmo_handle", sql)
        self.assertIn("bound_group_title_at_bind", sql)
        self.assertIn("groups", sql)
        self.assertIn("player_details", sql)

    def test_q_none_skips_text_search(self):
        sql = self._filtered_sql(q=None)
        self.assertNotIn("ilike", sql)
        self.assertNotIn("player_details", sql)

    def test_unbound_rows_require_chat_id_for_group_player_match(self):
        """Group/player branch requires telegram_chat_id IS NOT NULL (unbound excluded)."""
        sql = self._filtered_sql(q="RT / 1111-2222 / Bob")
        self.assertIn("telegram_chat_id", sql)
        self.assertIn("is not null", sql)
        # Payer/handle still searchable for unbound
        self.assertIn("payer_name", sql)
        self.assertIn("ilike", sql)


class ApplySessionFiltersSearchTestCase(unittest.TestCase):
    def _filtered_sql(self, *, q: str | None) -> str:
        query = Query([StripeCheckoutSession])
        query = apply_session_filters(
            query,
            club_id=1,
            status="complete",
            method_id=None,
            manual_only=False,
            from_dt=None,
            to_dt=None,
            q=q,
        )
        return _compile(query.statement)

    def test_q_matches_group_player_and_stripe_customer(self):
        sql = self._filtered_sql(q="8190-5287")
        self.assertIn("groups", sql)
        self.assertIn("player_details", sql)
        self.assertIn("stripe_customers", sql)
        self.assertIn("gg_player_id", sql)

    def test_q_none_skips_text_search(self):
        sql = self._filtered_sql(q=None)
        self.assertNotIn("ilike", sql)
        self.assertNotIn("stripe_customers", sql)
        self.assertNotIn("player_details", sql)


if __name__ == "__main__":
    unittest.main()
