"""Tests for weight-0 inactive deposit variants."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from api.schemas_v2 import ClubPaymentTierVariantCreate, ClubPaymentTierVariantUpdate
from bot.handlers import deposit as dep
from bot.services import club_payment_v2
from db.models import ClubPaymentMethod, ClubPaymentTier, ClubPaymentTierVariant


def _variant_row(
    *,
    vid: int = 1,
    weight: int = 100,
    method_id: int = 4,
    tier_id: int = 10,
    label: str = "Account 1",
):
    return SimpleNamespace(
        id=vid,
        weight=weight,
        method_id=method_id,
        tier_id=tier_id,
        label=label,
        response_type="text",
        response_text="pay here",
        response_file_id=None,
        response_caption=None,
        use_group_checkout_link=None,
        group_checkout_provider=None,
        hyperlink_text=None,
        checkout_min_amount=None,
        checkout_max_amount=None,
    )


class VariantWeightHelperTests(unittest.TestCase):
    def test_active_variants_excludes_zero_weight(self):
        rows = [_variant_row(vid=1, weight=0), _variant_row(vid=2, weight=50)]
        active = club_payment_v2._active_variants(rows)
        self.assertEqual([v.id for v in active], [2])

    def test_pick_weighted_variant_returns_none_when_all_inactive(self):
        rows = [_variant_row(weight=0), _variant_row(vid=2, weight=0)]
        self.assertIsNone(club_payment_v2._pick_weighted_variant(rows))


class PickVariantInactiveTests(unittest.TestCase):
    @patch("bot.services.club_payment_v2.get_db")
    def test_pick_variant_never_returns_weight_zero(self, mock_get_db):
        inactive = _variant_row(vid=1, weight=0)
        active = _variant_row(vid=2, weight=100)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            inactive,
            active,
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        with patch(
            "bot.services.club_payment_v2.random.choices", return_value=[active]
        ) as rc:
            result = club_payment_v2.pick_variant(4, tier_id=10)

        rc.assert_called_once()
        self.assertEqual(rc.call_args[0][0], [active])
        self.assertEqual(result["variant_id"], 2)

    @patch("bot.services.club_payment_v2.get_db")
    def test_pick_variant_none_when_all_inactive(self, mock_get_db):
        inactive = _variant_row(vid=1, weight=0)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            inactive,
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        self.assertIsNone(club_payment_v2.pick_variant(4, tier_id=10))

    @patch("bot.services.club_payment_v2.get_db")
    def test_sticky_inactive_variant_falls_back_to_weighted_pick(self, mock_get_db):
        inactive = _variant_row(vid=99, weight=0)
        active = _variant_row(vid=2, weight=100)
        session = MagicMock()
        session.query.return_value.get.return_value = inactive
        session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [
            inactive,
            active,
        ]
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        with patch(
            "bot.services.club_payment_v2.random.choices", return_value=[active]
        ) as rc:
            result = club_payment_v2.pick_variant(4, tier_id=10, variant_id=99)

        rc.assert_called_once()
        self.assertEqual(result["variant_id"], 2)


class DeliverabilityInactiveTests(unittest.TestCase):
    def _method_session(self, *, method_id: int = 4, tiers, variant_q):
        method_q = MagicMock()
        method_q.get.return_value = SimpleNamespace(
            id=method_id,
            is_active=True,
            has_sub_options=False,
        )
        tier_q = MagicMock()
        tier_q.filter_by.return_value = tier_q
        tier_q.order_by.return_value = tier_q
        tier_q.all.return_value = tiers

        session = MagicMock()

        def query_side_effect(model):
            if model is ClubPaymentMethod:
                return method_q
            if model is ClubPaymentTier:
                return tier_q
            if model is ClubPaymentTierVariant:
                return variant_q
            return MagicMock()

        session.query.side_effect = query_side_effect
        return session

    def test_not_deliverable_when_only_inactive_variants(self):
        tier = SimpleNamespace(
            id=1,
            min_amount=Decimal("101"),
            max_amount=Decimal("2000"),
            use_group_checkout_link=False,
            sort_order=0,
        )
        variant_q = MagicMock()
        variant_q.filter_by.return_value = variant_q
        variant_q.filter.return_value = variant_q
        variant_q.count.side_effect = [2, 0]

        session = self._method_session(tiers=[tier], variant_q=variant_q)

        self.assertFalse(
            club_payment_v2.club_deposit_method_deliverable(
                session, 4, Decimal("250")
            )
        )

    def test_deliverable_with_active_variant(self):
        tier = SimpleNamespace(
            id=1,
            min_amount=Decimal("20"),
            max_amount=Decimal("100"),
            use_group_checkout_link=False,
            sort_order=0,
        )
        variant_q = MagicMock()
        variant_q.filter_by.return_value = variant_q
        variant_q.filter.return_value = variant_q
        variant_q.count.side_effect = [2, 1]

        session = self._method_session(tiers=[tier], variant_q=variant_q)

        self.assertTrue(
            club_payment_v2.club_deposit_method_deliverable(
                session, 4, Decimal("75")
            )
        )

    def test_checkout_only_tier_without_variants_still_deliverable(self):
        tier = SimpleNamespace(
            id=1,
            min_amount=Decimal("20"),
            max_amount=None,
            use_group_checkout_link=True,
            sort_order=0,
        )
        variant_q = MagicMock()
        variant_q.filter_by.return_value = variant_q
        variant_q.filter.return_value = variant_q
        variant_q.count.return_value = 0

        session = self._method_session(tiers=[tier], variant_q=variant_q)

        self.assertTrue(
            club_payment_v2.club_deposit_method_deliverable(
                session, 4, Decimal("75")
            )
        )


class GetMethodsForAmountInactiveTests(unittest.TestCase):
    @patch("bot.services.club_payment_v2.club_deposit_method_deliverable", return_value=False)
    @patch("bot.services.club_payment_v2.get_db")
    def test_hides_method_when_tier_not_deliverable(self, mock_get_db, _deliverable):
        method = SimpleNamespace(
            id=4,
            name="Cashapp",
            slug="cashapp",
            min_amount=Decimal("20"),
            max_amount=Decimal("2000"),
            has_sub_options=False,
            is_public=True,
            tracks_manual_requests=False,
            union_type=None,
            deposit_union=None,
            method_tag=None,
            payment_account_name=None,
            deposit_limit=None,
            accumulated_amount=None,
            sort_order=0,
        )
        session = MagicMock()
        session.query.return_value.filter_by.return_value.filter.return_value.order_by.return_value.all.return_value = [
            method
        ]
        session.query.return_value.join.return_value.filter.return_value.options.return_value.order_by.return_value.all.return_value = []
        cm = MagicMock()
        cm.__enter__.return_value = session
        cm.__exit__.return_value = False
        mock_get_db.return_value = cm

        shown = club_payment_v2.get_methods_for_amount(1, "deposit", Decimal("250"))
        self.assertEqual(shown, [])


class DepositStickyInactiveTests(unittest.TestCase):
    def test_venmo_sticky_inactive_falls_back_without_forcing_variant_id(self):
        binding = SimpleNamespace(variant_id=99)
        over_tier = {"id": 2, "label": "Over $100"}
        venmo_method = {"id": 4, "name": "Venmo", "slug": "venmo"}
        active_pick = {"variant_id": 2, "response_type": "text", "response_text": "x"}

        with (
            patch.object(dep, "get_tier_for_amount", return_value=over_tier),
            patch.object(dep, "get_chat_binding", return_value=binding),
            patch.object(dep, "pick_variant", return_value=active_pick) as pick_mock,
        ):
            dep._pick_deposit_variant_response(
                4,
                venmo_method,
                Decimal("150"),
                chat_id=-100123,
                method_slug="venmo",
            )

        pick_mock.assert_called_once_with(4, tier_id=2, variant_id=99)


class VariantWeightSchemaTests(unittest.TestCase):
    def test_create_accepts_weight_zero(self):
        row = ClubPaymentTierVariantCreate(label="Paused account", weight=0)
        self.assertEqual(row.weight, 0)

    def test_create_rejects_negative_weight(self):
        with self.assertRaises(ValidationError):
            ClubPaymentTierVariantCreate(label="Bad", weight=-1)

    def test_update_accepts_weight_zero(self):
        row = ClubPaymentTierVariantUpdate(weight=0)
        self.assertEqual(row.weight, 0)


if __name__ == "__main__":
    unittest.main()
